"""기존 MetricsCollector 요약값을 1분 주기로 읽기 전용 조회해 장기 스냅샷으로 저장.

`monitoring/external_monitor.py`의 `run_monitor_loop`와 정확히 같은 모양(종료 신호가 올
때까지 반복, 예외는 삼켜서 스레드가 죽지 않게 함)을 따릅니다. 기존 미들웨어/`MetricsCollector
.record()` 경로에는 어떤 코드도 추가하지 않습니다 - 요청 단위 DB 저장은 하지 않는다는 설계
원칙(요청 처리 흐름에 DB를 얹으면 지연/락 위험이 있음) 그대로, 스냅샷은 완전히 별도 스레드가
기존 summary() 딕셔너리를 읽기만 해서 만듭니다.
"""

from __future__ import annotations

from threading import Event

from monitoring.healthcheck import HealthChecker
from monitoring.metrics_collector import MetricsCollector

from .db import MonitoringAddonDB

SNAPSHOT_INTERVAL_SECONDS = 60


def run_snapshot_loop(db: MonitoringAddonDB, metrics: MetricsCollector, health_checker: HealthChecker, stop_event: Event) -> None:
    """백그라운드 스레드 본체. 종료 신호가 올 때까지 SNAPSHOT_INTERVAL_SECONDS마다 반복."""
    while not stop_event.is_set():
        try:
            summary = metrics.summary()  # 기존 MetricsCollector - 읽기 전용 호출
            health = health_checker.check()
            db.insert_snapshot(
                total_requests=summary["total_requests"],
                total_errors=summary["total_errors"],
                error_rate=summary["error_rate"],
                avg_response_ms=summary["avg_response_ms"],
                p95_response_ms=summary["p95_response_ms"],
                source="MetricsCollector.summary",
            )
        except Exception:
            pass  # 스냅샷 1회 실패로 스레드 자체가 죽으면 안 됨 (external_monitor와 동일한 관례)
        stop_event.wait(SNAPSHOT_INTERVAL_SECONDS)
