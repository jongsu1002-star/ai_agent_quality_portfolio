import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import monitoring_addon as monitoring_addon_router
from monitoring_addon.db import MonitoringAddonDB

_VALID_RUN = {
    "run_id": "20260708_103000",
    "tool": "k6",
    "target_url": "http://127.0.0.1:8000",
    "scenario": "basic-load-test",
    "vus": 10,
    "total_requests": 1000,
    "failed_rate": 0.01,
    "checks_rate": 0.99,
    "http_req_duration": {"avg_ms": 210.5, "min_ms": 80.1, "med_ms": 180.3, "max_ms": 1200.8, "p90_ms": 450.2, "p95_ms": 780.4, "p99_ms": 980.2},
    "thresholds": [{"name": "http_req_failed", "condition": "rate<0.05", "passed": True}],
    "thresholds_passed": True,
    "result": "Pass",
}


@pytest.fixture
def isolated_addon_db(tmp_path, monkeypatch):
    """실제 data/monitoring_addon.db 대신 이 테스트만의 임시 DB를 라우터에 주입.

    app.routers.monitoring_addon._state["db"]를 monkeypatch로 바꿔치기 - monkeypatch가
    테스트가 끝나면 자동으로 원래 값(app.main이 넣어둔 실제 DB)으로 복원해줌.
    """
    db = MonitoringAddonDB(path=str(tmp_path / "addon.db"))
    monkeypatch.setitem(monitoring_addon_router._state, "db", db)
    yield db
    db.close_thread_connection()


def test_k6_latest_returns_no_data_when_nothing_imported(isolated_addon_db):
    # DB는 isolated_addon_db로 격리했지만, k6/latest는 DB가 비었을 때 실제
    # reports/k6/latest.json으로 폴백하므로 - 실제 k6를 돌려본 흔적이 남아있지 않도록 명시적으로 치움
    Path("reports/k6/latest.json").unlink(missing_ok=True)

    client = TestClient(app)
    response = client.get("/api/monitoring-addon/k6/latest")
    assert response.status_code == 200
    assert response.json() == {"status": "no_data"}


def test_k6_runs_list_is_empty_initially(isolated_addon_db):
    client = TestClient(app)
    response = client.get("/api/monitoring-addon/k6/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_k6_run_detail_404_for_unknown_run(isolated_addon_db):
    client = TestClient(app)
    response = client.get("/api/monitoring-addon/k6/runs/does-not-exist")
    assert response.status_code == 404


def test_k6_import_endpoint_and_latest_round_trip(isolated_addon_db):
    client = TestClient(app)
    k6_dir = Path("reports/k6")
    k6_dir.mkdir(parents=True, exist_ok=True)
    latest_path = k6_dir / "latest.json"
    latest_path.write_text(json.dumps(_VALID_RUN), encoding="utf-8")
    try:
        import_response = client.post("/api/monitoring-addon/k6/import")
        assert import_response.status_code == 200
        assert import_response.json()["imported"] is True

        latest_response = client.get("/api/monitoring-addon/k6/latest")
        assert latest_response.status_code == 200
        body = latest_response.json()
        assert body["run_id"] == _VALID_RUN["run_id"]
        assert body["http_req_duration"]["p95_ms"] == 780.4

        runs_response = client.get("/api/monitoring-addon/k6/runs")
        assert runs_response.json()["total"] == 1

        detail_response = client.get(f"/api/monitoring-addon/k6/runs/{_VALID_RUN['run_id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["thresholds"][0]["passed"] is True
    finally:
        latest_path.unlink(missing_ok=True)


def test_history_summary_reflects_inserted_snapshots(isolated_addon_db):
    isolated_addon_db.insert_snapshot(total_requests=5, total_errors=0, error_rate=0.0, avg_response_ms=3.0, p95_response_ms=10.0, source="test")
    client = TestClient(app)
    response = client.get("/api/monitoring-addon/history/summary")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["total_requests"] == 5


def test_metrics_addon_returns_prometheus_text_with_self_metrics(isolated_addon_db):
    client = TestClient(app)
    response = client.get("/metrics-addon")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "qa_platform_total_requests" in response.text
    assert "qa_platform_error_rate" in response.text


def test_metrics_addon_includes_k6_metrics_after_import(isolated_addon_db):
    isolated_addon_db.insert_k6_run(_VALID_RUN, raw_json_path="reports/k6/latest.json", thresholds=_VALID_RUN["thresholds"])
    client = TestClient(app)
    response = client.get("/metrics-addon")
    assert "qa_platform_k6_p95_ms 780.4" in response.text
    assert "qa_platform_k6_thresholds_passed 1" in response.text


def test_monitoring_addon_page_is_served_separately_from_main_dashboard():
    client = TestClient(app)
    addon_page = client.get("/monitoring-addon")
    main_page = client.get("/")
    assert addon_page.status_code == 200
    assert "모니터링 애드온" in addon_page.text
    assert "AI Agent 품질관리·운영 모니터링 플랫폼" in main_page.text  # 기존 대시보드 타이틀 그대로
