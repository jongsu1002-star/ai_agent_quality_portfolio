import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import voc_analysis as voc_analysis_module


class _FakeJudgeClient:
    enabled = True
    fail = False

    def __init__(self, *args, **kwargs):
        pass

    def judge(self, system_prompt, user_prompt):
        if _FakeJudgeClient.fail:
            raise RuntimeError("llm down")
        return {"summary": "요약입니다", "top_issues": [{"theme": "속도", "frequency": 1, "severity": "high", "suggestion": "최적화", "example_ids": []}]}


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    _FakeJudgeClient.fail = False
    monkeypatch.setattr(voc_analysis_module, "OpenAIJudgeClient", _FakeJudgeClient)


def test_run_without_any_source_returns_400():
    client = TestClient(app)
    response = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": False})
    assert response.status_code == 400


def test_run_with_board_posts_succeeds_and_persists_history():
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "느려요", "content": "응답이 느립니다"})

    run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": False})
    assert run.status_code == 200
    data = run.json()
    assert data["result"]["summary"] == "요약입니다"
    assert data["result"]["raw_source_counts"]["board"] == 1

    history = client.get("/api/voc-analysis/history")
    assert len(history.json()) == 1
    assert history.json()[0]["id"] == data["id"]

    detail = client.get(f"/api/voc-analysis/{data['id']}")
    assert detail.status_code == 200
    assert detail.json()["result"]["summary"] == "요약입니다"


class _TwoStageFakeClient:
    """1번째 judge() 호출(생성)엔 summary/top_issues를, 2번째 호출(독립 검증)엔 verdict를
    반환 - 생성과 심사가 실제로 순차적인 별도 호출인지 확인하는 용도."""

    enabled = True
    call_count = 0

    def __init__(self, *args, **kwargs):
        pass

    def judge(self, system_prompt, user_prompt):
        _TwoStageFakeClient.call_count += 1
        if _TwoStageFakeClient.call_count == 1:
            return {"summary": "요약", "top_issues": [{"theme": "속도", "frequency": 1, "severity": "high", "suggestion": "최적화", "example_ids": []}]}
        return {"verdict": "PASS", "criteria": {"relevance": True, "root_cause_addressing": True, "feasibility": True, "measurability": True}, "reasoning": "타당함"}


def test_run_response_includes_independent_judge_verdict(monkeypatch):
    """test_pipeline_result_with_llm_judge에 해당 - HTTP 응답에 생성 결과와 별도로
    judge(독립 검증) 필드가 포함되고, 저장된 이력에도 남는지 확인."""
    _TwoStageFakeClient.call_count = 0
    monkeypatch.setattr(voc_analysis_module, "OpenAIJudgeClient", _TwoStageFakeClient)

    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": False})
    assert run.status_code == 200
    data = run.json()
    assert data["result"]["judge"]["verdict"] == "PASS"
    assert _TwoStageFakeClient.call_count == 2  # 생성 1회 + 독립 검증 1회

    detail = client.get(f"/api/voc-analysis/{data['id']}")
    assert detail.json()["result"]["judge"]["verdict"] == "PASS"


def test_run_gracefully_degrades_on_llm_failure():
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    _FakeJudgeClient.fail = True

    response = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": False})
    assert response.status_code == 502
    assert "error" in response.json()


def test_voc_excel_template_and_upload_round_trip():
    client = TestClient(app)
    template = client.get("/api/voc-analysis/template")
    assert template.status_code == 200

    df = pd.read_excel(io.BytesIO(template.content))
    assert list(df.columns) == ["source", "date", "category", "content"]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="voc")
    buf.seek(0)

    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("voc.xlsx", buf.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert upload.status_code == 200
    body = upload.json()
    assert body["row_count"] == 2

    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": True, "excel_path": body["excel_path"]})
    assert run.status_code == 200
    assert run.json()["result"]["raw_source_counts"]["excel"] == 2


def test_voc_excel_upload_rejects_invalid_path_on_run():
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": True, "excel_path": "../../etc/passwd"})
    assert run.status_code == 400


def test_jira_preview_success(monkeypatch):
    monkeypatch.setattr(voc_analysis_module, "fetch_backlog_issues", lambda config, jql=None, max_results=50: [{"key": "QA-1", "summary": "s", "description": "d", "status": "Open", "updated": "2026-01-01"}])
    client = TestClient(app)
    response = client.get("/api/voc-analysis/jira-preview")
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_jira_preview_failure_returns_502(monkeypatch):
    def _raise(*args, **kwargs):
        raise ConnectionError("no network")
    monkeypatch.setattr(voc_analysis_module, "fetch_backlog_issues", _raise)
    client = TestClient(app)
    response = client.get("/api/voc-analysis/jira-preview")
    assert response.status_code == 502


def test_run_with_focus_instruction_and_item_limit():
    client = TestClient(app)
    for i in range(5):
        client.post("/api/board/posts", json={"board_type": "voc", "title": f"t{i}", "content": f"c{i}"})

    run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": False, "focus_instruction": "불친절 관련만", "item_limit": 2})
    assert run.status_code == 200
    data = run.json()
    assert data["params"]["focus_instruction"] == "불친절 관련만"
    assert data["params"]["item_limit"] == 2
    assert data["result"]["raw_source_counts"]["total_considered"] == 2


def test_run_clamps_item_limit_above_max():
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": False, "item_limit": 99999})
    assert run.status_code == 200
    assert run.json()["params"]["item_limit"] == 150


def test_run_rejects_non_numeric_item_limit():
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": False, "item_limit": "abc"})
    assert run.status_code == 400


def test_run_with_jira_included(monkeypatch):
    monkeypatch.setattr(voc_analysis_module, "fetch_backlog_issues", lambda config, jql=None, max_results=50: [{"key": "QA-1", "summary": "s", "description": "d", "status": "Open", "updated": "2026-01-01"}])
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_jira": True, "use_excel": False})
    assert run.status_code == 200
    assert run.json()["result"]["raw_source_counts"]["jira"] == 1
