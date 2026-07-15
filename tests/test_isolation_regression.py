"""conftest.py의 전역 격리 픽스처(_isolate_shared_reports_and_settings 등)가 실제로
동작하는지 검증하는 회귀 테스트.

이 파일이 존재하는 이유: 격리가 깨지면 pytest 실행마다 실제 reports/settings.json,
reports/run_*.json, reports/monitoring_targets.json, reports/users/{실명계정}/,
docs/결함보고서.md가 테스트 데이터로 덮어써진다(실제로 이 세션에서 발생해 확인된 문제).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


def _wait_for_run(client, run_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/run/{run_id}/status").json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.1)
    raise AssertionError("run did not finish in time")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REAL_SETTINGS_PATH = _PROJECT_ROOT / "reports" / "settings.json"
_REAL_MONITORING_TARGETS_PATH = _PROJECT_ROOT / "reports" / "monitoring_targets.json"
_REAL_DEFECT_REPORT_PATH = _PROJECT_ROOT / "docs" / "결함보고서.md"


def _snapshot(path: Path):
    if not path.exists():
        return None
    return (path.stat().st_mtime_ns, path.read_bytes())


def test_settings_path_points_inside_tmp_path(tmp_path):
    assert main_module.SETTINGS_PATH == tmp_path / "reports" / "settings.json"


def test_shared_reports_root_and_user_data_root_point_inside_tmp_path(tmp_path):
    assert main_module.SHARED_REPORTS_ROOT == tmp_path / "reports"
    assert main_module.USER_DATA_ROOT == tmp_path / "reports" / "users"


def test_external_monitor_registry_points_inside_tmp_path(tmp_path):
    assert main_module.EXTERNAL_MONITOR._path == tmp_path / "reports" / "monitoring_targets.json"


def test_defect_report_docs_dir_for_shared_bucket_points_inside_tmp_path(tmp_path):
    assert main_module._defect_report_docs_dir("shared") == str(tmp_path / "docs")


def test_saving_settings_does_not_touch_real_reports_settings_json():
    before = _snapshot(_REAL_SETTINGS_PATH)
    client = TestClient(app)
    response = client.post("/api/settings", json={"jira_base_url": "https://isolation-canary.example.invalid"})
    assert response.status_code == 200
    after = _snapshot(_REAL_SETTINGS_PATH)
    assert before == after, "격리가 깨져 테스트가 실제 reports/settings.json을 건드렸습니다"


def test_step1_saving_settings_sets_env_var_for_this_test_only():
    """save_settings()가 os.environ을 직접 mutate하는 것 자체는 이 테스트 안에서 정상 동작해야
    한다(설정을 저장한 요청이 바로 그 값을 쓸 수 있어야 하므로) - 다음 테스트로 새지 않는지는
    test_step2가 확인한다(두 테스트는 순서에 의존하는 쌍으로 설계됨, 파일 내 선언 순서대로 실행됨)."""
    client = TestClient(app)
    client.post("/api/settings", json={"jira_base_url": "https://isolation-canary.example.invalid"})
    assert os.environ.get("JIRA_BASE_URL") == "https://isolation-canary.example.invalid"


def test_step2_env_var_set_by_previous_test_does_not_leak_here():
    assert os.environ.get("JIRA_BASE_URL") != "https://isolation-canary.example.invalid"


def test_registering_monitoring_target_does_not_touch_real_monitoring_targets_json():
    before = _snapshot(_REAL_MONITORING_TARGETS_PATH)
    client = TestClient(app)
    client.post("/api/monitoring/targets", json={"name": "isolation-canary", "url": "http://127.0.0.1:9/health"})
    after = _snapshot(_REAL_MONITORING_TARGETS_PATH)
    assert before == after, "격리가 깨져 테스트가 실제 reports/monitoring_targets.json을 건드렸습니다"


def test_running_pipeline_in_shared_bucket_does_not_touch_real_defect_report_doc():
    before = _snapshot(_REAL_DEFECT_REPORT_PATH)
    client = TestClient(app)
    run_response = client.post("/api/run", json={"techniques": ["rag"]})
    assert run_response.status_code == 200
    final_status = _wait_for_run(client, run_response.json()["run_id"])
    assert final_status["status"] == "done"
    after = _snapshot(_REAL_DEFECT_REPORT_PATH)
    assert before == after, "격리가 깨져 테스트가 실제 docs/결함보고서.md를 건드렸습니다"
