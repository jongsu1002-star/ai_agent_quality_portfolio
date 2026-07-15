import io
import json
import pathlib
import re

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
        if "독립적인 QA 심사관" in system_prompt:
            return {
                "verdict": "PASS",
                "criteria": {"relevance": True, "root_cause_addressing": True, "feasibility": True, "measurability": True},
                "reasoning": "모든 기준을 충족함",
            }
        match = re.search(r"- \[([^\]]+)\]", user_prompt)
        example_id = match.group(1) if match else "post-1"
        return {"summary": "요약입니다", "top_issues": [{"theme": "속도", "frequency": 1, "severity": "high", "suggestion": "담당자가 즉시 최적화하고 응답시간을 측정", "example_ids": [example_id]}]}


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    _FakeJudgeClient.fail = False
    monkeypatch.setattr(voc_analysis_module, "OpenAIJudgeClient", _FakeJudgeClient)
    monkeypatch.setitem(
        voc_analysis_module._state,
        "independent_judge_kwargs",
        lambda settings: ({"provider": "anthropic", "api_key": "test-key"}, True),
    )


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
    assert data["result"]["judge"]["verdict"] == "PASS"
    assert data["result"]["quality_gate"]["status"] == "APPROVED"

    history = client.get("/api/voc-analysis/history")
    assert len(history.json()) == 1
    assert history.json()[0]["id"] == data["id"]
    assert history.json()[0]["quality_gate_status"] == "APPROVED"

    detail = client.get(f"/api/voc-analysis/{data['id']}")
    assert detail.status_code == 200
    assert detail.json()["result"]["summary"] == "요약입니다"


def test_consecutive_runs_get_unique_analysis_ids():
    """P0: 같은 초 안에 여러 번 실행돼도(마이크로초+uuid 접미사) 파일명이 충돌해 이전 결과를
    덮어쓰는 일이 없어야 함 - 감사 기록 유실 방지."""
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})

    ids = set()
    for _ in range(5):
        run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": False})
        assert run.status_code == 200
        ids.add(run.json()["id"])
    assert len(ids) == 5  # 5번 모두 서로 다른 id

    history = client.get("/api/voc-analysis/history").json()
    assert len(history) == 5  # 아무것도 덮어써지지 않고 5건 모두 남아있음


def test_delete_analysis_history_by_admin():
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": False})
    analysis_id = run.json()["id"]

    delete = client.delete(f"/api/voc-analysis/{analysis_id}")
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True

    assert client.get(f"/api/voc-analysis/{analysis_id}").status_code == 404
    assert client.get("/api/voc-analysis/history").json() == []


def test_delete_analysis_history_missing_returns_404():
    client = TestClient(app)
    response = client.delete("/api/voc-analysis/voc_does_not_exist")
    assert response.status_code == 404


def test_delete_analysis_history_rejects_path_traversal_id():
    client = TestClient(app)
    response = client.delete("/api/voc-analysis/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code in (400, 404)


def test_use_board_defaults_to_true_when_unspecified():
    """아무 소스도 명시하지 않으면(use_board 키 자체가 없으면) 게시판 VOC가 기본으로 쓰임."""
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={})
    assert run.status_code == 200
    assert run.json()["result"]["raw_source_counts"]["board"] == 1
    assert run.json()["params"]["use_board"] is True


def test_use_board_false_excludes_board_even_when_posts_exist(monkeypatch):
    """Jira/엑셀만 선택하고 use_board=false를 명시하면 게시판 VOC는 분석에서 빠져야 함."""
    monkeypatch.setattr(voc_analysis_module, "fetch_backlog_issues", lambda config, jql=None, max_results=50: [{"key": "QA-1", "summary": "s", "description": "d", "status": "Open", "updated": "2026-01-01"}])
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})

    run = client.post("/api/voc-analysis/run", json={"use_board": False, "use_jira": True})
    assert run.status_code == 200
    data = run.json()
    assert data["result"]["raw_source_counts"]["board"] == 0
    assert data["result"]["raw_source_counts"]["jira"] == 1
    assert data["params"]["use_board"] is False


def test_use_board_true_combined_with_jira():
    """use_board를 명시적으로 true로 두면 Jira를 함께 켜도 게시판 VOC가 계속 포함됨."""
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_board": True, "use_jira": False})
    assert run.status_code == 200
    assert run.json()["result"]["raw_source_counts"]["board"] == 1


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
            return {"summary": "요약", "top_issues": [{"theme": "속도", "frequency": 1, "severity": "high", "suggestion": "담당자가 즉시 최적화하고 응답시간을 측정", "example_ids": ["post-1"]}]}
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
    assert "llm down" not in response.json()["error"]


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


def test_voc_excel_selected_without_upload_path_returns_400():
    client = TestClient(app)
    response = client.post("/api/voc-analysis/run", json={"use_board": False, "use_excel": True})
    assert response.status_code == 400
    assert "업로드" in response.json()["error"]


def _build_voc_xlsx_bytes(rows=None):
    df = pd.DataFrame(rows or [{"source": "s", "date": "2026-01-01", "category": "c", "content": "content"}])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="voc")
    buf.seek(0)
    return buf.read()


def test_voc_excel_upload_rejects_unsupported_extension():
    client = TestClient(app)
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("voc.csv", b"source,date,category,content\n", "text/csv")})
    assert upload.status_code == 400
    assert "지원" in upload.json()["error"]
    assert list(voc_analysis_module.VOC_UPLOAD_DIR.glob("*voc.csv")) == []  # 저장되지 않아야 함


def test_voc_excel_upload_rejects_legacy_xls_extension():
    """.xls는 pandas가 읽으려면 xlrd가 필요한데 의존성에 없어 실제로는 처리 불가 -
    있지도 않은 지원을 광고하지 않도록 .xlsx만 허용."""
    client = TestClient(app)
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("voc.xls", _build_voc_xlsx_bytes(), "application/vnd.ms-excel")})
    assert upload.status_code == 400


def test_voc_excel_upload_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(voc_analysis_module, "MAX_UPLOAD_BYTES", 100)
    client = TestClient(app)
    oversized = _build_voc_xlsx_bytes(rows=[{"source": "s", "date": "2026-01-01", "category": "c", "content": "x" * 1000}])
    assert len(oversized) > 100
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("big.xlsx", oversized, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert upload.status_code == 413
    assert list(voc_analysis_module.VOC_UPLOAD_DIR.glob("*big.xlsx")) == []  # 저장되지 않아야 함


def test_voc_excel_upload_sanitizes_path_traversal_filename():
    client = TestClient(app)
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("../../evil.xlsx", _build_voc_xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert upload.status_code == 200
    saved_path = pathlib.Path(upload.json()["excel_path"])
    # 저장된 파일이 실제로 VOC_UPLOAD_DIR 안에 있어야 함(디렉터리 탈출 안 됨)
    assert saved_path.resolve().parent == voc_analysis_module.VOC_UPLOAD_DIR.resolve()
    assert ".." not in saved_path.name


def test_voc_excel_upload_deletes_saved_file_when_parsing_fails():
    client = TestClient(app)
    # 확장자는 맞지만 실제로는 엑셀이 아닌 내용 -> openpyxl 파싱 실패
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("broken.xlsx", b"not a real xlsx file", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert upload.status_code == 400
    assert list(voc_analysis_module.VOC_UPLOAD_DIR.glob("*broken.xlsx")) == []  # 실패 시 파일이 지워져야 함


def test_voc_excel_upload_hides_unexpected_internal_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("secret path C:/internal/data and token=abc")

    monkeypatch.setattr(voc_analysis_module, "load_voc_excel", _raise)
    client = TestClient(app)
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("voc.xlsx", _build_voc_xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})

    assert upload.status_code == 400
    assert "secret" not in upload.json()["error"]
    assert "C:/internal" not in upload.json()["error"]


def test_voc_excel_upload_deletes_saved_file_when_no_valid_rows():
    client = TestClient(app)
    empty_rows = _build_voc_xlsx_bytes(rows=[{"source": "s", "date": "2026-01-01", "category": "c", "content": ""}])
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("empty.xlsx", empty_rows, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert upload.status_code == 400
    assert list(voc_analysis_module.VOC_UPLOAD_DIR.glob("*empty.xlsx")) == []


def test_voc_excel_upload_accepts_rows_with_missing_date():
    """date는 선택 항목 - 컬럼 자체가 없어도 오류 없이 업로드/파싱돼야 한다."""
    client = TestClient(app)
    rows_without_date = [{"source": "s", "category": "c", "content": "날짜 없는 VOC"}]
    upload = client.post(
        "/api/voc-analysis/excel/upload",
        files={"file": ("no_date.xlsx", _build_voc_xlsx_bytes(rows=rows_without_date), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload.status_code == 200
    body = upload.json()
    assert body["row_count"] == 1
    assert body["preview"][0]["date"] == ""


def test_voc_json_template_and_upload_round_trip():
    client = TestClient(app)
    template = client.get("/api/voc-analysis/template.json")
    assert template.status_code == 200
    rows = json.loads(template.content)
    assert isinstance(rows, list) and len(rows) == 2
    assert set(rows[0].keys()) == {"source", "date", "category", "content"}

    upload = client.post(
        "/api/voc-analysis/excel/upload",
        files={"file": ("voc.json", json.dumps(rows).encode("utf-8"), "application/json")},
    )
    assert upload.status_code == 200
    body = upload.json()
    assert body["row_count"] == 2

    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_jira": False, "use_excel": True, "excel_path": body["excel_path"]})
    assert run.status_code == 200
    assert run.json()["result"]["raw_source_counts"]["excel"] == 2


def test_voc_json_upload_accepts_rows_with_missing_date():
    client = TestClient(app)
    payload = json.dumps([{"source": "s", "category": "c", "content": "날짜 없는 VOC"}]).encode("utf-8")
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("no_date.json", payload, "application/json")})
    assert upload.status_code == 200
    body = upload.json()
    assert body["row_count"] == 1
    assert body["preview"][0]["date"] == ""


def test_voc_json_upload_rejects_invalid_json_syntax():
    client = TestClient(app)
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("broken.json", b"{not valid json", "application/json")})
    assert upload.status_code == 400
    assert list(voc_analysis_module.VOC_UPLOAD_DIR.glob("*broken.json")) == []


def test_voc_json_upload_rejects_non_array_top_level():
    client = TestClient(app)
    payload = json.dumps({"source": "s", "content": "배열이 아님"}).encode("utf-8")
    upload = client.post("/api/voc-analysis/excel/upload", files={"file": ("notarray.json", payload, "application/json")})
    assert upload.status_code == 400


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
    assert "no network" not in response.json()["error"]


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


@pytest.mark.parametrize("bad_limit", [0, -1, True])
def test_run_rejects_item_limit_below_one_or_boolean(bad_limit):
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"item_limit": bad_limit})
    assert run.status_code == 400


def test_run_with_jira_included(monkeypatch):
    monkeypatch.setattr(voc_analysis_module, "fetch_backlog_issues", lambda config, jql=None, max_results=50: [{"key": "QA-1", "summary": "s", "description": "d", "status": "Open", "updated": "2026-01-01"}])
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    run = client.post("/api/voc-analysis/run", json={"use_jira": True, "use_excel": False})
    assert run.status_code == 200
    assert run.json()["result"]["raw_source_counts"]["jira"] == 1


def test_voc_analysis_history_and_uploads_are_isolated_per_user():
    admin = TestClient(app)
    assert admin.post("/signup", json={"username": "alice", "password": "secret123"}).status_code == 200
    admin.post("/api/board/posts", json={"board_type": "voc", "title": "대기", "content": "오래 기다림"})

    bob = TestClient(app)
    assert bob.post("/signup", json={"username": "bob", "password": "secret456"}).status_code == 200
    assert admin.post("/api/users/bob/approve").status_code == 200
    assert bob.post("/login", json={"username": "bob", "password": "secret456"}).status_code == 200

    admin_run = admin.post("/api/voc-analysis/run", json={"use_board": True}).json()
    bob_run = bob.post("/api/voc-analysis/run", json={"use_board": True}).json()

    assert [item["id"] for item in admin.get("/api/voc-analysis/history").json()] == [admin_run["id"]]
    assert [item["id"] for item in bob.get("/api/voc-analysis/history").json()] == [bob_run["id"]]
    assert bob.get(f"/api/voc-analysis/{admin_run['id']}").status_code == 404
    assert admin.get(f"/api/voc-analysis/{bob_run['id']}").status_code == 404

    upload = admin.post("/api/voc-analysis/excel/upload", files={"file": ("voc.xlsx", _build_voc_xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert upload.status_code == 200
    cross_user_run = bob.post("/api/voc-analysis/run", json={
        "use_board": False,
        "use_excel": True,
        "excel_path": upload.json()["excel_path"],
    })
    assert cross_user_run.status_code == 400
