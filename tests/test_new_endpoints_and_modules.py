import json
import time

from fastapi.testclient import TestClient

from app.main import app, get_local_ip
from performance.pipeline_benchmark import benchmark_pipeline
from qa_agent.models import GoldenCase
from quality.quality_checks import QualityCheckRunner


def _wait_for_run(client, run_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/run/{run_id}/status").json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.1)
    raise AssertionError("run did not finish in time")


def test_health_endpoint_uses_health_checker():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "qa_agent_quality_platform"
    assert "reports_dir_writable" in body["details"]


def test_connector_defaults_endpoint():
    client = TestClient(app)
    response = client.get("/api/config/connector-defaults")
    assert response.status_code == 200
    assert response.json()["mode"] == "dataset_only"


def test_monitoring_summary_endpoint_tracks_requests():
    client = TestClient(app)
    before = client.get("/api/monitoring/summary").json()

    client.get("/health")
    client.get("/health")

    after = client.get("/api/monitoring/summary").json()
    assert after["total_requests"] >= before["total_requests"] + 3  # 자기 자신의 두 /monitoring/summary 호출 + /health 2건
    assert "avg_response_ms" in after
    assert "requests_per_minute" in after
    assert "by_path" in after
    assert after["health"]["status"] in ("ok", "degraded")


def test_monitoring_summary_response_shape_is_unchanged_by_monitoring_addon():
    """모니터링 애드온(k6/SQLite/Prometheus)이 얹혀도 기존 /api/monitoring/summary의 응답
    key 집합은 절대 바뀌면 안 된다는 회귀 테스트 - 애드온 설계서의 "기존 응답 구조 변경 금지"
    원칙을 코드로 고정."""
    client = TestClient(app)
    body = client.get("/api/monitoring/summary").json()
    assert set(body.keys()) == {
        "started_at",
        "uptime_seconds",
        "total_requests",
        "total_errors",
        "error_rate",
        "window_seconds",
        "window_request_count",
        "avg_response_ms",
        "p95_response_ms",
        "status_counts",
        "requests_per_minute",
        "by_path",
        "health",
    }


def test_monitoring_target_rejects_url_without_http_scheme():
    client = TestClient(app)
    response = client.post("/api/monitoring/targets", json={"name": "잘못된 대상", "url": "ftp://example.com"})
    assert response.status_code == 400


def test_monitoring_target_add_list_and_remove_round_trip():
    client = TestClient(app)
    add_response = client.post("/api/monitoring/targets", json={
        "name": "우리 챗봇 서비스",
        "url": "https://example.com/health",
        "interval_seconds": 1,  # 최소값(10초) 미만 -> 서버가 강제로 10초로 올림
    })
    assert add_response.status_code == 200
    target = add_response.json()
    assert target["interval_seconds"] == 10  # MIN_INTERVAL_SECONDS로 강제 상향

    listed = client.get("/api/monitoring/targets").json()
    assert any(row["id"] == target["id"] and row["name"] == "우리 챗봇 서비스" for row in listed)

    remove_response = client.delete(f"/api/monitoring/targets/{target['id']}")
    assert remove_response.status_code == 200

    listed_after = client.get("/api/monitoring/targets").json()
    assert all(row["id"] != target["id"] for row in listed_after)

    assert client.delete(f"/api/monitoring/targets/{target['id']}").status_code == 404


def test_docs_endpoint_returns_current_file_content():
    client = TestClient(app)
    for doc_key in ("user_manual", "design_spec", "process_spec", "network_guide", "test_results", "defect_report"):
        response = client.get(f"/api/docs/{doc_key}")
        assert response.status_code == 200
        body = response.json()
        assert body["name"].endswith(".md")
        assert len(body["content"]) > 0


def test_docs_endpoint_rejects_unknown_key():
    client = TestClient(app)
    response = client.get("/api/docs/not_a_real_doc")
    assert response.status_code == 404


def test_get_local_ip_returns_a_dotted_quad_or_none():
    ip = get_local_ip()
    if ip is not None:
        assert len(ip.split(".")) == 4


def test_run_rejects_empty_techniques():
    client = TestClient(app)
    response = client.post("/api/run", json={"techniques": []})
    assert response.status_code == 400


def test_run_rejects_unknown_technique():
    client = TestClient(app)
    response = client.post("/api/run", json={"techniques": ["not_a_real_technique"]})
    assert response.status_code == 400


def test_run_result_and_run_detail_endpoints():
    client = TestClient(app)
    run_response = client.post("/api/run", json={"techniques": ["rag"]})
    run_id = run_response.json()["run_id"]

    final_status = _wait_for_run(client, run_id)
    assert final_status["status"] == "done"

    result_response = client.get(f"/api/run/{run_id}/result")
    assert result_response.status_code == 200
    assert result_response.json()["run_id"] == run_id

    detail_response = client.get(f"/api/runs/{run_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["run_id"] == run_id


def test_run_detail_rejects_path_traversal():
    client = TestClient(app)
    response = client.get("/api/runs/..%2F..%2Fsecret")
    assert response.status_code == 404


def test_jira_tickets_endpoint_returns_list():
    client = TestClient(app)
    response = client.get("/api/jira/tickets")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_quality_gate_reports_fail_below_threshold(tmp_path):
    (tmp_path / "latest.json").write_text('{"run_id": "r1", "overall_pass_rate": 0.5, "cases": [{"case_id": "TC-1", "overall_pass": false}]}', encoding="utf-8")
    runner = QualityCheckRunner(reports_dir=str(tmp_path))

    verdict = runner.run_gate(pass_rate_threshold=1.0)

    assert verdict["gate"] == "fail"
    assert verdict["failing_cases"] == [{"case_id": "TC-1", "reason": "failed evaluation"}]


def test_run_with_llm_provider_none_skips_llm_even_with_a_key_available(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-would-otherwise-work")
    client = TestClient(app)

    run_response = client.post("/api/run", json={"techniques": ["rag", "llm_quality"], "llm_provider": "none"})
    run_id = run_response.json()["run_id"]
    _wait_for_run(client, run_id)

    report = client.get(f"/api/run/{run_id}/result").json()
    assert report["cases"][0]["llm_judge"]["errored"] is True


def test_run_with_custom_llm_provider_uses_configured_header_and_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"accuracy": 5, "relevance": 5, "consistency": 5, "toxicity": 1, "reason": "ok"})}}]}

    def fake_post(url, headers=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("qa_agent.llm_client.requests.post", fake_post)

    client = TestClient(app)
    run_response = client.post("/api/run", json={
        "techniques": ["llm_quality"],
        "llm_provider": "custom",
        "llm_key_name": "X-Custom-Key",
        "llm_key_value": "my-secret",
        "llm_endpoint": "https://my-llm.example.com/v1",
    })
    run_id = run_response.json()["run_id"]
    _wait_for_run(client, run_id)

    report = client.get(f"/api/run/{run_id}/result").json()
    assert report["cases"][0]["llm_judge"]["passed"] is True
    assert captured["url"] == "https://my-llm.example.com/v1/chat/completions"
    assert captured["headers"]["X-Custom-Key"] == "my-secret"


def test_run_with_anthropic_llm_provider_calls_messages_api(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": json.dumps({"accuracy": 5, "relevance": 5, "consistency": 5, "toxicity": 1, "reason": "ok"})}]}

    def fake_post(url, headers=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("qa_agent.llm_client.requests.post", fake_post)

    client = TestClient(app)
    run_response = client.post("/api/run", json={
        "techniques": ["llm_quality"],
        "llm_provider": "anthropic",
        "anthropic_api_key": "sk-ant-secret",
    })
    run_id = run_response.json()["run_id"]
    _wait_for_run(client, run_id)

    report = client.get(f"/api/run/{run_id}/result").json()
    assert report["cases"][0]["llm_judge"]["passed"] is True
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-secret"


def test_benchmark_pipeline_times_a_real_run(tmp_path):
    case = GoldenCase(id="TC-1", category="COM", question="q", golden_answer="a", existing_answer="a")
    result = benchmark_pipeline([case], techniques=["rag"], reports_dir=str(tmp_path))

    assert result["case_count"] == 1
    assert result["elapsed_seconds"] >= 0
    assert result["overall_pass_rate"] == 1.0
