import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


@pytest.fixture(autouse=True)
def _isolate_dataset_storage(tmp_path, monkeypatch):
    """이 파일의 테스트들은 /api/dataset/*, /api/run 등을 TestClient로 실제 호출하므로, 실제
    reports/ 대신 임시 디렉터리를 쓰도록 격리한다 - 격리하지 않으면 라이브 서버가 떠 있는 동안
    pytest를 돌릴 때마다 사용자의 실제 활성 데이터셋/실행이력이 테스트 데이터로 덮어써지는
    문제가 반복됐다. 이 테스트들은 로그인하지 않으므로 전부 "shared" 버킷을 쓰는데, 그 버킷의
    루트(SHARED_REPORTS_ROOT)와 실명 계정 루트(USER_DATA_ROOT)를 각각 임시 경로로 바꿔치기하면
    dataset_dir/testcase_dir/run 리포트 등 이 버킷이 쓰는 모든 하위 경로가 함께 격리된다."""
    monkeypatch.setattr(main_module, "SHARED_REPORTS_ROOT", tmp_path)
    monkeypatch.setattr(main_module, "USER_DATA_ROOT", tmp_path / "users")


def test_dataset_upload_json_endpoint(tmp_path):
    payload = [{
        "id": "TC-100",
        "category": "COM",
        "question": "How do I reset my password?",
        "golden_answer": "Use the password reset link",
        "relevant_doc_ids": ["DOC-1"],
        "existing_answer": "Use the password reset link"
    }]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    client = TestClient(app)
    with path.open("rb") as fh:
        response = client.post("/api/dataset/upload", files={"file": (path.name, fh, "application/json")})

    assert response.status_code == 200
    data = response.json()
    assert data["case_count"] == 1


def _upload_dataset(client, tmp_path, filename, case_id):
    payload = [{
        "id": case_id,
        "category": "COM",
        "question": "q",
        "golden_answer": "a",
        "existing_answer": "a",
    }]
    path = tmp_path / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    with path.open("rb") as fh:
        response = client.post("/api/dataset/upload", files={"file": (filename, fh, "application/json")})
    assert response.status_code == 200
    return response.json()


def test_dataset_reset_clears_active_dataset(tmp_path):
    client = TestClient(app)
    _upload_dataset(client, tmp_path, "reset_case.json", "TC-RESET")
    assert client.get("/api/dataset/current").json()["path"] is not None

    reset_response = client.post("/api/dataset/reset")
    assert reset_response.status_code == 200

    current = client.get("/api/dataset/current").json()
    assert current["path"] is None
    assert current["is_default"] is True
    assert current["case_count"] == 0


def test_dataset_history_lists_uploads_with_active_flag(tmp_path):
    client = TestClient(app)
    _upload_dataset(client, tmp_path, "history_a.json", "TC-A")
    second = _upload_dataset(client, tmp_path, "history_b.json", "TC-B")

    history = client.get("/api/dataset/history").json()
    assert history[0]["path"] == second["dataset_path"]  # most recent first
    assert history[0]["active"] is True
    assert any(entry["active"] is False for entry in history if entry["path"] != second["dataset_path"])


def test_dataset_select_reactivates_a_previous_upload(tmp_path):
    client = TestClient(app)
    first = _upload_dataset(client, tmp_path, "select_a.json", "TC-SELECT-A")
    _upload_dataset(client, tmp_path, "select_b.json", "TC-SELECT-B")

    select_response = client.post("/api/dataset/select", json={"path": first["dataset_path"]})
    assert select_response.status_code == 200
    assert select_response.json()["dataset_path"] == first["dataset_path"]

    current = client.get("/api/dataset/current").json()
    assert current["path"] == first["dataset_path"]


def test_dataset_select_rejects_path_outside_dataset_dir():
    client = TestClient(app)
    response = client.post("/api/dataset/select", json={"path": "reports/settings.json"})
    assert response.status_code == 400


def _wait_for_run(client, run_id, timeout=10):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/run/{run_id}/status").json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.1)
    raise AssertionError("run did not finish in time")


def test_run_status_exposes_stage_field_and_reaches_done_on_completion():
    """실행 버튼에 단계별 진행사항을 보여주는 기능의 핵심 계약 - 상태 폴링 응답에 stage
    필드가 실려 있어야 하고, 완료 시점에는 "완료"로 남아야 한다(app/main.py::_execute_run
    참고). 스레드가 매우 빠르게 끝나는 테스트 환경에서는 중간 단계(데이터 준비/평가 실행 등)
    를 안정적으로 포착하기 어려우므로(경합), 필드 존재 여부와 최종 상태만 검증한다."""
    client = TestClient(app)
    run_response = client.post("/api/run", json={"techniques": ["rag"]})
    run_id = run_response.json()["run_id"]

    status_immediately = client.get(f"/api/run/{run_id}/status").json()
    assert "stage" in status_immediately

    final_status = _wait_for_run(client, run_id)
    assert final_status["status"] == "done"
    assert final_status["stage"] == "완료"


def test_excel_template_round_trip_runs_against_uploaded_cases():
    client = TestClient(app)

    template_response = client.get("/api/dataset/template")
    assert template_response.status_code == 200

    upload_response = client.post(
        "/api/dataset/upload",
        files={"file": ("qa_template.xlsx", template_response.content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["case_count"] == 2

    run_response = client.post("/api/run", json={"techniques": ["rag"]})
    run_id = run_response.json()["run_id"]
    final_status = _wait_for_run(client, run_id)
    assert final_status["status"] == "done"

    report = client.get(f"/api/run/{run_id}/result").json()
    assert [case["case_id"] for case in report["cases"]] == ["TC-001", "TC-002"]


def test_run_with_multiple_category_filter_values_via_api(tmp_path):
    client = TestClient(app)
    payload = [
        {"id": "TC-200", "category": "COM", "question": "q1", "golden_answer": "a1", "existing_answer": "a1"},
        {"id": "TC-201", "category": "ACC", "question": "q2", "golden_answer": "a2", "existing_answer": "a2"},
        {"id": "TC-202", "category": "REG", "question": "q3", "golden_answer": "a3", "existing_answer": "a3"},
    ]
    path = tmp_path / "multi_category_cases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with path.open("rb") as fh:
        upload_response = client.post("/api/dataset/upload", files={"file": (path.name, fh, "application/json")})
    assert upload_response.status_code == 200

    run_response = client.post("/api/run", json={"techniques": ["rag"], "category": ["COM", "REG"]})
    run_id = run_response.json()["run_id"]
    final_status = _wait_for_run(client, run_id)
    assert final_status["status"] == "done"

    report = client.get(f"/api/run/{run_id}/result").json()
    assert {case["case_id"] for case in report["cases"]} == {"TC-200", "TC-202"}
    assert set(report["category_stats"].keys()) == {"COM", "REG"}


def test_run_status_and_history_endpoints():
    client = TestClient(app)
    run_response = client.post("/api/run", json={"techniques": ["rag", "llm_quality"]})
    assert run_response.status_code == 200
    run_id = run_response.json()["run_id"]

    status_response = client.get(f"/api/run/{run_id}/status")
    assert status_response.status_code == 200
    assert status_response.json()["run_id"] == run_id

    history_response = client.get("/api/runs")
    assert history_response.status_code == 200
