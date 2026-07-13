"""서버 운영 지표(요청 수 / 응답시간 / 에러율)를 메모리에서 집계하는 경량 컬렉터.

Prometheus·Grafana 같은 외부 도구 없이, 지금 떠 있는 FastAPI 프로세스 하나의 상태만
보여주면 되므로 전부 메모리 위에서 계산합니다 - `app/main.py`의 RUN_REGISTRY와 같은
트레이드오프로, 서버를 재시작하면 지표도 함께 초기화됩니다(디스크에 쓰지 않음).

`app/main.py`가 요청마다 `record()`를 호출해 채우고, `/api/monitoring/summary`
엔드포인트가 `summary()`를 그대로 JSON으로 내보냅니다. 기존 QA 파이프라인/리포트
흐름과는 완전히 분리된 별도 관심사라, 이 모듈이 없어도(또는 예외가 나도) 기존 기능에는
영향이 없도록 app/main.py 쪽에서 항상 미들웨어 실패를 흡수합니다.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any, Deque, Dict, List

_WINDOW_SECONDS = 3600  # 지표 계산에 쓰는 관측 구간 - 이보다 오래된 요청은 자동으로 제외
_MAX_SAMPLES = 5000  # 요청이 몰려도 메모리가 무한정 늘지 않도록 최근 N건만 유지(deque maxlen)
_TIMESERIES_MINUTES = 30  # "분당 요청 추이" 차트에 보여줄 구간


@dataclass
class _RequestSample:
    timestamp: float
    method: str
    path: str
    status_code: int
    duration_ms: float


class MetricsCollector:
    """모든 HTTP 요청 1건을 `record()`로 받아 요약 통계를 계산해주는 인메모리 컬렉터."""

    def __init__(self) -> None:
        self._started_at = time.time()
        self._lock = Lock()
        self._samples: Deque[_RequestSample] = deque(maxlen=_MAX_SAMPLES)
        self._total_requests = 0
        self._total_errors = 0  # status_code >= 500 누적 건수 (윈도우 밖으로 밀려나도 유지되는 전체 누계)

    def record(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        """요청 1건을 기록. 락으로 감싸서 동시 요청이 들어와도 카운터가 깨지지 않게 함."""
        with self._lock:
            self._samples.append(_RequestSample(time.time(), method, path, status_code, duration_ms))
            self._total_requests += 1
            if status_code >= 500:
                self._total_errors += 1

    def _recent_samples(self) -> List[_RequestSample]:
        cutoff = time.time() - _WINDOW_SECONDS
        return [s for s in self._samples if s.timestamp >= cutoff]

    def summary(self) -> Dict[str, Any]:
        """대시보드 모니터링 탭이 그대로 렌더링할 수 있는 요약 딕셔너리를 만듦."""
        with self._lock:
            samples = self._recent_samples()
            total_requests = self._total_requests
            total_errors = self._total_errors

        durations = sorted(s.duration_ms for s in samples)
        status_counts = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
        for s in samples:
            bucket = f"{s.status_code // 100}xx"
            if bucket in status_counts:
                status_counts[bucket] += 1

        return {
            "started_at": self._started_at,
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "total_requests": total_requests,
            "total_errors": total_errors,
            "error_rate": round(total_errors / total_requests, 4) if total_requests else 0.0,
            "window_seconds": _WINDOW_SECONDS,
            "window_request_count": len(samples),
            "avg_response_ms": round(sum(durations) / len(durations), 1) if durations else 0.0,
            "p95_response_ms": round(_percentile(durations, 0.95), 1) if durations else 0.0,
            "status_counts": status_counts,
            "requests_per_minute": _bucket_per_minute(samples),
            "by_path": _summarize_by_path(samples),
        }


def _percentile(sorted_values: List[float], pct: float) -> float:
    """정렬된 값 목록에서 백분위수를 근사 계산(보간 없이 가장 가까운 인덱스로 근사)."""
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(len(sorted_values) * pct))
    return sorted_values[idx]


def _bucket_per_minute(samples: List[_RequestSample]) -> List[Dict[str, Any]]:
    """최근 `_TIMESERIES_MINUTES`분을 1분 단위로 묶어 요청수/에러수/평균응답시간 시계열을 만듦.

    요청이 하나도 없던 분도 0건짜리 빈 버킷으로 채워서 반환 - 차트 x축이 끊기지 않게 함.
    """
    now_minute = int(time.time() // 60)
    buckets: Dict[int, Dict[str, Any]] = {
        now_minute - offset: {"count": 0, "error_count": 0, "total_ms": 0.0}
        for offset in range(_TIMESERIES_MINUTES - 1, -1, -1)
    }
    for s in samples:
        bucket = buckets.get(int(s.timestamp // 60))
        if bucket is None:
            continue
        bucket["count"] += 1
        bucket["total_ms"] += s.duration_ms
        if s.status_code >= 500:
            bucket["error_count"] += 1

    return [
        {
            "minute": minute,
            "count": data["count"],
            "error_count": data["error_count"],
            "avg_ms": round(data["total_ms"] / data["count"], 1) if data["count"] else 0.0,
        }
        for minute, data in sorted(buckets.items())
    ]


def _summarize_by_path(samples: List[_RequestSample], limit: int = 10) -> List[Dict[str, Any]]:
    """엔드포인트(path)별 요청수/에러수/평균응답시간 - 요청이 많은 순 상위 N개만."""
    by_path: Dict[str, Dict[str, Any]] = {}
    for s in samples:
        entry = by_path.setdefault(s.path, {"count": 0, "error_count": 0, "total_ms": 0.0})
        entry["count"] += 1
        entry["total_ms"] += s.duration_ms
        if s.status_code >= 500:
            entry["error_count"] += 1

    rows = [
        {
            "path": path,
            "count": entry["count"],
            "error_count": entry["error_count"],
            "avg_ms": round(entry["total_ms"] / entry["count"], 1) if entry["count"] else 0.0,
        }
        for path, entry in by_path.items()
    ]
    rows.sort(key=lambda row: row["count"], reverse=True)
    return rows[:limit]
