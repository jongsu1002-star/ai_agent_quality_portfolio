import pytest

from monitoring_addon.db import MonitoringAddonDB


@pytest.fixture
def db(tmp_path):
    instance = MonitoringAddonDB(path=str(tmp_path / "addon.db"))
    yield instance
    instance.close_thread_connection()  # Windows tmp_path 정리가 열린 파일 핸들 때문에 실패하는 것 방지


def _sample_run(run_id: str, result: str = "Pass") -> dict:
    return {
        "run_id": run_id,
        "tool": "k6",
        "target_url": "http://127.0.0.1:8000",
        "scenario": "basic-load-test",
        "vus": 10,
        "total_requests": 1000,
        "failed_rate": 0.01,
        "checks_rate": 0.99,
        "http_req_duration": {"avg_ms": 210.5, "min_ms": 80.1, "med_ms": 180.3, "max_ms": 1200.8, "p90_ms": 450.2, "p95_ms": 780.4, "p99_ms": 980.2},
        "thresholds_passed": result == "Pass",
        "result": result,
    }


def test_schema_creation_is_idempotent_across_instances(tmp_path):
    path = str(tmp_path / "addon.db")
    first = MonitoringAddonDB(path=path)
    second = MonitoringAddonDB(path=path)  # 같은 파일에 CREATE TABLE IF NOT EXISTS가 다시 실행돼도 에러 없어야 함
    first.close_thread_connection()
    second.close_thread_connection()


def test_insert_snapshot_and_recent_snapshots_orders_newest_first(db):
    db.insert_snapshot(total_requests=10, total_errors=0, error_rate=0.0, avg_response_ms=5.0, p95_response_ms=20.0, source="test")
    db.insert_snapshot(total_requests=20, total_errors=1, error_rate=0.05, avg_response_ms=6.0, p95_response_ms=25.0, source="test")

    rows = db.recent_snapshots(limit=10)
    assert len(rows) == 2
    assert rows[0]["total_requests"] == 20  # 가장 최근 것이 먼저


def test_insert_k6_run_is_idempotent_by_run_id(db):
    run = _sample_run("run-1")
    thresholds = [{"name": "http_req_failed", "condition": "rate<0.05", "passed": True}]

    first_insert = db.insert_k6_run(run, raw_json_path="reports/k6/latest.json", thresholds=thresholds)
    second_insert = db.insert_k6_run(run, raw_json_path="reports/k6/latest.json", thresholds=thresholds)

    assert first_insert is True
    assert second_insert is False  # 같은 run_id -> 중복 저장 안 됨

    stored = db.get_k6_run("run-1")
    assert stored is not None
    assert len(stored["thresholds"]) == 1  # threshold도 중복으로 쌓이지 않음


def test_list_k6_runs_supports_pagination_and_result_filter(db):
    db.insert_k6_run(_sample_run("run-1", "Pass"), raw_json_path="x", thresholds=[])
    db.insert_k6_run(_sample_run("run-2", "Fail"), raw_json_path="x", thresholds=[])
    db.insert_k6_run(_sample_run("run-3", "Pass"), raw_json_path="x", thresholds=[])

    all_items, total = db.list_k6_runs(page=1, page_size=2)
    assert total == 3
    assert len(all_items) == 2

    pass_only, pass_total = db.list_k6_runs(page=1, page_size=10, result_filter="Pass")
    assert pass_total == 2
    assert all(item["result"] == "Pass" for item in pass_only)


def test_get_latest_k6_run_returns_most_recently_inserted(db):
    db.insert_k6_run(_sample_run("run-1"), raw_json_path="x", thresholds=[])
    db.insert_k6_run(_sample_run("run-2"), raw_json_path="x", thresholds=[])

    latest = db.get_latest_k6_run()
    assert latest["run_id"] == "run-2"


def test_get_k6_run_returns_none_for_unknown_run_id(db):
    assert db.get_k6_run("does-not-exist") is None
