import json

import pytest

from monitoring_addon.db import MonitoringAddonDB
from monitoring_addon.k6_result_importer import import_latest_k6_result

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
def db(tmp_path):
    instance = MonitoringAddonDB(path=str(tmp_path / "addon.db"))
    yield instance
    instance.close_thread_connection()


def test_import_missing_file_returns_no_data(db, tmp_path):
    result = import_latest_k6_result(db, reports_dir=str(tmp_path / "does-not-exist"))
    assert result == {"status": "no_data"}


def test_import_invalid_json_returns_invalid_json_status(db, tmp_path):
    k6_dir = tmp_path / "k6"
    k6_dir.mkdir()
    (k6_dir / "latest.json").write_text("{not valid json", encoding="utf-8")

    result = import_latest_k6_result(db, reports_dir=str(k6_dir))
    assert result["status"] == "invalid_json"


def test_import_missing_required_fields_returns_invalid_json_status(db, tmp_path):
    k6_dir = tmp_path / "k6"
    k6_dir.mkdir()
    (k6_dir / "latest.json").write_text(json.dumps({"tool": "k6"}), encoding="utf-8")  # run_id 등 필수 필드 없음

    result = import_latest_k6_result(db, reports_dir=str(k6_dir))
    assert result["status"] == "invalid_json"


def test_import_valid_result_stores_it_and_is_idempotent(db, tmp_path):
    k6_dir = tmp_path / "k6"
    k6_dir.mkdir()
    (k6_dir / "latest.json").write_text(json.dumps(_VALID_RUN), encoding="utf-8")

    first = import_latest_k6_result(db, reports_dir=str(k6_dir))
    assert first == {"status": "ok", "imported": True, "run_id": "20260708_103000"}

    second = import_latest_k6_result(db, reports_dir=str(k6_dir))
    assert second == {"status": "ok", "imported": False, "run_id": "20260708_103000"}  # 같은 run_id 재수입 -> 건너뜀

    stored = db.get_latest_k6_run()
    assert stored["run_id"] == "20260708_103000"
    assert stored["thresholds"][0]["condition"] == "rate<0.05"
