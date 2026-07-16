import io
import json
import pathlib
import re
import time

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
        if "classifications" in system_prompt:
            return {"classifications": [{"id": example_id, "intent": "complaint", "topic": "속도"}]}
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
    """system_prompt 내용으로 단계를 구분해 응답 - Interpreter(의도 분류)/Summarizer(생성)/
    독립 Judge(검증) 3단계가 실제로 순차적인 별도 호출인지 확인하는 용도."""

    enabled = True
    call_count = 0

    def __init__(self, *args, **kwargs):
        pass

    def judge(self, system_prompt, user_prompt):
        _TwoStageFakeClient.call_count += 1
        if "독립적인 QA 심사관" in system_prompt:
            return {"verdict": "PASS", "criteria": {"relevance": True, "root_cause_addressing": True, "feasibility": True, "measurability": True}, "reasoning": "타당함"}
        if "classifications" in system_prompt:
            return {"classifications": [{"id": "post-1", "intent": "complaint", "topic": "속도"}]}
        return {"summary": "요약", "top_issues": [{"theme": "속도", "frequency": 1, "severity": "high", "suggestion": "담당자가 즉시 최적화하고 응답시간을 측정", "example_ids": ["post-1"]}]}


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
    assert data["result"]["interpreter"]["applied"] is True
    assert data["result"]["self_check"]["before_verdict"] == "PASS"
    assert _TwoStageFakeClient.call_count == 4  # Interpreter 1회 + 생성 1회 + 내부 재점검 1회 + 독립 검증 1회

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


def test_voc_analysis_history_and_detail_are_visible_to_all_users_but_uploads_stay_isolated():
    """이력 조회/상세는 게시판·Jira 티켓과 같은 팀 공용 산출물이라 전원 공개로 바뀌었지만
    (실행자 격리 폐지), 업로드 파일은 여전히 사용자별로 격리됨(별개 정책 - 그대로 유지)."""
    admin = TestClient(app)
    assert admin.post("/signup", json={"username": "alice", "password": "secret123"}).status_code == 200
    admin.post("/api/board/posts", json={"board_type": "voc", "title": "대기", "content": "오래 기다림"})

    bob = TestClient(app)
    assert bob.post("/signup", json={"username": "bob", "password": "secret456"}).status_code == 200
    assert admin.post("/api/users/bob/approve").status_code == 200
    assert bob.post("/login", json={"username": "bob", "password": "secret456"}).status_code == 200

    admin_run = admin.post("/api/voc-analysis/run", json={"use_board": True}).json()
    bob_run = bob.post("/api/voc-analysis/run", json={"use_board": True}).json()

    admin_history_ids = {item["id"] for item in admin.get("/api/voc-analysis/history").json()}
    bob_history_ids = {item["id"] for item in bob.get("/api/voc-analysis/history").json()}
    assert admin_history_ids == bob_history_ids == {admin_run["id"], bob_run["id"]}
    assert bob.get(f"/api/voc-analysis/{admin_run['id']}").status_code == 200
    assert admin.get(f"/api/voc-analysis/{bob_run['id']}").status_code == 200

    upload = admin.post("/api/voc-analysis/excel/upload", files={"file": ("voc.xlsx", _build_voc_xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert upload.status_code == 200
    cross_user_run = bob.post("/api/voc-analysis/run", json={
        "use_board": False,
        "use_excel": True,
        "excel_path": upload.json()["excel_path"],
    })
    assert cross_user_run.status_code == 400


def test_voc_analysis_delete_allowed_for_admin_or_owner_only():
    """삭제는 여전히 좁게 제한됨 - 관리자이거나 그 분석을 실행한 본인만 가능. 관리자도
    작성자도 아닌 제3자는 남의 실행 결과를 지울 수 없어야 한다."""
    admin = TestClient(app)
    assert admin.post("/signup", json={"username": "alice", "password": "secret123"}).status_code == 200
    admin.post("/api/board/posts", json={"board_type": "voc", "title": "대기", "content": "오래 기다림"})

    bob = TestClient(app)
    assert bob.post("/signup", json={"username": "bob", "password": "secret456"}).status_code == 200
    assert admin.post("/api/users/bob/approve").status_code == 200
    assert bob.post("/login", json={"username": "bob", "password": "secret456"}).status_code == 200

    carol = TestClient(app)
    assert carol.post("/signup", json={"username": "carol", "password": "secret789"}).status_code == 200
    assert admin.post("/api/users/carol/approve").status_code == 200
    assert carol.post("/login", json={"username": "carol", "password": "secret789"}).status_code == 200

    bob_run = bob.post("/api/voc-analysis/run", json={"use_board": True}).json()

    # 제3자(carol)는 자기가 실행하지도, 관리자도 아니므로 거부됨
    forbidden = carol.delete(f"/api/voc-analysis/{bob_run['id']}")
    assert forbidden.status_code == 403

    # 실행자 본인(bob)은 자기 결과를 지울 수 있음
    own_delete = bob.delete(f"/api/voc-analysis/{bob_run['id']}")
    assert own_delete.status_code == 200

    admin_run = admin.post("/api/voc-analysis/run", json={"use_board": True}).json()
    # 관리자(admin)는 남이 실행한 것도 지울 수 있음 - 여기선 자기 자신이 실행자이지만
    # is_admin 경로도 함께 통과하는지 별도로 확인
    admin_delete = admin.delete(f"/api/voc-analysis/{admin_run['id']}")
    assert admin_delete.status_code == 200


def test_quality_dashboard_reports_no_data_when_test_results_doc_missing():
    """격리된 테스트 환경에는 docs/테스트_결과.md가 없으므로 - 0이나 성공값으로 위장하지
    않고 available=False로 정직하게 표시해야 한다(2차 리뷰 P1-1)."""
    client = TestClient(app)
    response = client.get("/api/voc-analysis/quality-dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["test_summary"]["available"] is False


def test_quality_dashboard_parses_real_test_results_doc(monkeypatch):
    doc = (
        "# 테스트 결과\n\n"
        "- 최종 실행 시각: 2026-07-15T16:00:00\n"
        "- 총 테스트 수: 10\n"
        "- 통과: 10\n\n"
        "## 파일별 결과\n\n"
        "| 테스트 파일 | 설명 | 건수 | 결과 |\n"
        "|---|---|---|---|\n"
        "| `tests/test_voc_analysis.py` | - | 4 | 통과 |\n"
        "| `tests/test_board_api.py` | - | 6 | 통과 |\n"
    )
    voc_analysis_module.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (voc_analysis_module.DOCS_DIR / "테스트_결과.md").write_text(doc, encoding="utf-8")

    client = TestClient(app)
    body = client.get("/api/voc-analysis/quality-dashboard").json()
    summary = body["test_summary"]
    assert summary["available"] is True
    assert summary["total"] == 10
    assert summary["passed"] == 10
    labels = {row["label"]: row["count"] for row in summary["layer_breakdown"]}
    assert labels["함수 단위(voc_analysis)"] == 4
    assert labels["HTTP API(board)"] == 6


def test_quality_dashboard_scans_real_voc_history_for_verdict_distribution():
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "불친절한 상담원 때문에 화가 납니다"})
    run1 = client.post("/api/voc-analysis/run", json={"use_board": True})
    assert run1.status_code == 200

    body = client.get("/api/voc-analysis/quality-dashboard").json()
    history = body["voc_history"]
    assert history["total_runs"] >= 1
    assert sum(history["judge_verdict"].values()) == history["total_runs"]
    assert sum(history["quality_gate"].values()) == history["total_runs"]


def test_quality_dashboard_includes_curated_defect_status():
    client = TestClient(app)
    body = client.get("/api/voc-analysis/quality-dashboard").json()
    defect = body["defect_status"]
    assert defect["p0_resolved"] == defect["p0_total"]
    assert defect["p1_resolved"] <= defect["p1_total"]


def test_report_versions_list_returns_empty_when_no_snapshot_file():
    client = TestClient(app)
    response = client.get("/api/voc-analysis/report-versions/voc_quality_report")
    assert response.status_code == 200
    assert response.json() == []


def test_report_versions_rejects_unknown_doc_key():
    client = TestClient(app)
    assert client.get("/api/voc-analysis/report-versions/not_a_real_doc").status_code == 404
    assert client.get("/api/voc-analysis/report-versions/not_a_real_doc/abc123").status_code == 404


def test_report_version_content_round_trip(monkeypatch):
    snapshot = {
        "voc_quality_report": [
            {"commit": "abc1234", "date": "2026-07-15 10:00:00 +0900", "message": "첫 작성", "content": "# 옛날 버전 내용"},
            {"commit": "def5678", "date": "2026-07-15 12:00:00 +0900", "message": "개정", "content": "# 최신 버전 내용"},
        ]
    }
    voc_analysis_module.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (voc_analysis_module.DOCS_DIR / "report_versions.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    client = TestClient(app)
    listing = client.get("/api/voc-analysis/report-versions/voc_quality_report").json()
    assert [v["commit"] for v in listing] == ["abc1234", "def5678"]
    assert "content" not in listing[0]

    content_response = client.get("/api/voc-analysis/report-versions/voc_quality_report/abc1234")
    assert content_response.status_code == 200
    assert content_response.json()["content"] == "# 옛날 버전 내용"

    assert client.get("/api/voc-analysis/report-versions/voc_quality_report/does-not-exist").status_code == 404


# ===================== 백그라운드 실행 + 폴링 (POST /run-async) =====================

def _poll_until(client, url, done_statuses, timeout_s=5.0, interval_s=0.05):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = client.get(url).json()
        if last.get("status") in done_statuses:
            return last
        time.sleep(interval_s)
    raise AssertionError(f"timed out waiting for {done_statuses}, last={last}")


def test_run_async_returns_run_id_immediately_then_completes():
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})

    start = client.post("/api/voc-analysis/run-async", json={"use_jira": False, "use_excel": False})
    assert start.status_code == 200
    body = start.json()
    assert body["run_id"].startswith("voc_async_")
    assert body["status"] == "queued"

    final = _poll_until(client, f"/api/voc-analysis/run-async/{body['run_id']}/status", {"done", "error"})
    assert final["status"] == "done"

    result = client.get(f"/api/voc-analysis/run-async/{body['run_id']}/result")
    assert result.status_code == 200
    data = result.json()
    assert data["result"]["summary"] == "요약입니다"
    assert data["result"]["judge"]["verdict"] == "PASS"

    # 동기 /run과 동일한 record 형식이라 이력 목록에도 그대로 잡혀야 함
    history_ids = {item["id"] for item in client.get("/api/voc-analysis/history").json()}
    assert data["id"] in history_ids


def test_run_async_validation_error_returns_immediately_without_run_id():
    """엑셀 경로 누락 등 _prepare_voc_run 단계에서 걸리는 검증 실패는 스레드를 띄우기도
    전에 동기적으로 걸러져야 함 - 폴링할 run_id 자체가 생기지 않고 즉시 에러를 받아야 함.
    (VOC 데이터가 아예 없는 경우처럼 실제 실행 중에만 알 수 있는 오류는 이 단계에서
    잡히지 않고 async error 상태로 나타남 - 별개의 정상 동작이라 이 테스트 범위 밖)."""
    client = TestClient(app)
    response = client.post("/api/voc-analysis/run-async", json={"use_board": False, "use_excel": True})
    assert response.status_code == 400
    assert "run_id" not in response.json()


def test_run_async_with_no_voc_data_becomes_error_status_not_500():
    """소스가 하나도 없는 경우("분석할 VOC 데이터가 없습니다")는 prepare 단계에서는
    잡히지 않고(어떤 소스든 켤 수는 있으니) 실제 실행 중 ValueError로 나타남 - 비동기
    경로에서는 500으로 스레드가 죽는 대신 status="error"로 정직하게 남아야 함."""
    client = TestClient(app)
    start = client.post("/api/voc-analysis/run-async", json={"use_board": False, "use_jira": False, "use_excel": False})
    assert start.status_code == 200
    final = _poll_until(client, f"/api/voc-analysis/run-async/{start.json()['run_id']}/status", {"done", "error"})
    assert final["status"] == "error"
    assert "VOC 데이터가 없습니다" in final["error"]


def test_run_async_status_for_unknown_run_id_returns_404():
    client = TestClient(app)
    assert client.get("/api/voc-analysis/run-async/voc_async_doesnotexist/status").status_code == 404
    assert client.get("/api/voc-analysis/run-async/voc_async_doesnotexist/result").status_code == 404


def test_run_async_result_before_completion_returns_404():
    """아직 실행 중(done 아님)이면 result는 404 - 부분 결과를 노출하지 않음."""
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    start = client.post("/api/voc-analysis/run-async", json={"use_jira": False, "use_excel": False}).json()
    # 완료 전에 바로 조회 - 매우 빠르게 끝날 수도 있으므로 status가 done이면 이 검증은 건너뜀
    immediate = client.get(f"/api/voc-analysis/run-async/{start['run_id']}/result")
    status_now = client.get(f"/api/voc-analysis/run-async/{start['run_id']}/status").json()["status"]
    if status_now != "done":
        assert immediate.status_code == 404
    _poll_until(client, f"/api/voc-analysis/run-async/{start['run_id']}/status", {"done", "error"})


class _SlowFakeClient:
    """실행 단계마다 인위적으로 지연을 줘서, 테스트가 완료 전에 취소 요청을 보낼
    시간을 확보한다(취소가 "다음 단계로 못 넘어가게 막는" 동작을 확인하려면 최소
    1단계는 끝나고 다음 단계 시작 전에 취소가 걸려야 함)."""
    enabled = True

    def __init__(self, *args, **kwargs):
        pass

    def judge(self, system_prompt, user_prompt):
        time.sleep(0.3)
        if "독립적인 QA 심사관" in system_prompt:
            return {"verdict": "PASS", "criteria": {"relevance": True, "root_cause_addressing": True, "feasibility": True, "measurability": True}, "reasoning": "ok"}
        if "classifications" in system_prompt:
            return {"classifications": [{"id": "post-1", "intent": "complaint", "topic": "x"}]}
        return {"summary": "요약", "top_issues": [{"theme": "t", "frequency": 1, "severity": "high", "suggestion": "담당자가 즉시 조치하고 효과를 측정", "example_ids": ["post-1"]}]}


def test_run_async_cancel_stops_before_next_step(monkeypatch):
    monkeypatch.setattr(voc_analysis_module, "OpenAIJudgeClient", _SlowFakeClient)
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})

    start = client.post("/api/voc-analysis/run-async", json={"use_jira": False, "use_excel": False}).json()
    run_id = start["run_id"]

    cancel = client.post(f"/api/voc-analysis/run-async/{run_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["canceled"] is True

    final = _poll_until(client, f"/api/voc-analysis/run-async/{run_id}/status", {"canceled", "done", "error"}, timeout_s=5.0)
    assert final["status"] == "canceled"
    assert client.get(f"/api/voc-analysis/run-async/{run_id}/result").status_code == 404


def test_run_async_cancel_on_finished_run_is_a_noop():
    client = TestClient(app)
    client.post("/api/board/posts", json={"board_type": "voc", "title": "t", "content": "c"})
    start = client.post("/api/voc-analysis/run-async", json={"use_jira": False, "use_excel": False}).json()
    _poll_until(client, f"/api/voc-analysis/run-async/{start['run_id']}/status", {"done", "error"})

    late_cancel = client.post(f"/api/voc-analysis/run-async/{start['run_id']}/cancel")
    assert late_cancel.status_code == 200
    assert late_cancel.json()["canceled"] is False
