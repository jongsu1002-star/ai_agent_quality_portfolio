from monitoring.external_monitor import ExternalMonitorRegistry, ExternalTarget, probe_once


def test_probe_once_records_success_for_2xx_response(monkeypatch):
    class FakeResponse:
        status_code = 200

    monkeypatch.setattr("monitoring.external_monitor.requests.request", lambda *a, **k: FakeResponse())

    target = ExternalTarget(id="t1", name="테스트 대상", url="https://example.com")
    result = probe_once(target)

    assert result.success is True
    assert result.status_code == 200
    assert result.error is None


def test_probe_once_records_failure_on_request_exception(monkeypatch):
    import requests

    def fake_request(*args, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr("monitoring.external_monitor.requests.request", fake_request)

    target = ExternalTarget(id="t2", name="죽어있는 대상", url="https://example.com/down")
    result = probe_once(target)

    assert result.success is False
    assert result.status_code is None
    assert "connection refused" in result.error


def test_registry_summary_computes_uptime_and_avg_response_time(tmp_path):
    registry = ExternalMonitorRegistry(path=str(tmp_path / "monitoring_targets.json"))
    target = registry.add(name="대상A", url="https://example.com", interval_seconds=30)

    from monitoring.external_monitor import _ProbeResult

    registry.record_probe(target, _ProbeResult(timestamp=1.0, success=True, status_code=200, duration_ms=100.0))
    registry.record_probe(target, _ProbeResult(timestamp=2.0, success=False, status_code=500, duration_ms=50.0))

    rows = registry.summary()
    assert len(rows) == 1
    row = rows[0]
    assert row["check_count"] == 2
    assert row["uptime_pct_recent"] == 50.0  # 2건 중 1건 성공
    assert row["avg_response_ms_recent"] == 100.0  # 성공한 요청만으로 평균 계산
    assert row["last_success"] is False  # 가장 최근 체크는 실패
    assert [h["duration_ms"] for h in row["history"]] == [100.0, 50.0]  # 차트용 이력이 시간순으로 담김


def test_registry_persists_targets_across_reload(tmp_path):
    path = str(tmp_path / "monitoring_targets.json")
    registry = ExternalMonitorRegistry(path=path)
    registry.add(name="영속성 확인용", url="https://example.com", interval_seconds=45)

    reloaded = ExternalMonitorRegistry(path=path)
    names = [t.name for t in reloaded.list_targets()]
    assert names == ["영속성 확인용"]


def test_registry_add_enforces_minimum_interval(tmp_path):
    registry = ExternalMonitorRegistry(path=str(tmp_path / "monitoring_targets.json"))
    target = registry.add(name="너무 짧은 주기", url="https://example.com", interval_seconds=1)
    assert target.interval_seconds == 10
