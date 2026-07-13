"""사용자가 등록한 외부 URL(다른 서버에서 운영 중인 챗봇/API 서비스 등)을 백그라운드에서
주기적으로 호출해 살아있는지/응답 속도를 기록하는 합성 모니터링(synthetic monitoring).

이 플랫폼 자신의 트래픽을 관찰하는 `monitoring/metrics_collector.py`와는 완전히 별개입니다 -
여기서는 "이 서버가 클라이언트가 되어" 등록된 URL에 직접 요청을 보내고 결과를 기록합니다.

대상 목록(누가 등록했는지)은 재시작해도 유지되도록 JSON 파일에 저장하지만, 체크 이력(응답시간
추이 등)은 metrics_collector와 동일한 트레이드오프로 메모리에만 유지합니다(재시작 시 초기화).
"""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any, Deque, Dict, List, Optional

import requests

MIN_INTERVAL_SECONDS = 10  # 대상 서비스에 과도한 부하를 주지 않기 위한 최소 체크 주기
DEFAULT_TIMEOUT_SECONDS = 10
_HISTORY_LIMIT = 200  # 대상 1개당 보관하는 최근 체크 이력 건수
_RECENT_WINDOW = 20  # 가동률/평균응답시간 계산에 쓰는 "최근 N회"
_CHART_HISTORY_LIMIT = 60  # 응답시간 추이 차트에 실어보내는 최근 체크 건수


@dataclass
class ExternalTarget:
    """등록된 외부 모니터링 대상 1건 (사용자가 이름/URL/주기 등을 직접 입력)."""

    id: str
    name: str
    url: str
    method: str = "GET"
    interval_seconds: int = 60
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "method": self.method,
            "interval_seconds": self.interval_seconds,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class _ProbeResult:
    timestamp: float
    success: bool
    status_code: Optional[int]
    duration_ms: float
    error: Optional[str] = None


class ExternalMonitorRegistry:
    """등록된 외부 대상 목록 + 대상별 최근 체크 이력을 관리하는 저장소."""

    def __init__(self, path: str = "reports/monitoring_targets.json"):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._targets: Dict[str, ExternalTarget] = {}
        self._history: Dict[str, Deque[_ProbeResult]] = {}
        self._next_check_at: Dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        """서버 (재)시작 시 이전에 등록해둔 대상 목록을 복원 - 이력은 복원하지 않음."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in data:
            try:
                target = ExternalTarget(
                    id=item["id"],
                    name=item["name"],
                    url=item["url"],
                    method=item.get("method", "GET"),
                    interval_seconds=item.get("interval_seconds", 60),
                    timeout_seconds=item.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                )
            except KeyError:
                continue
            self._targets[target.id] = target
            self._history[target.id] = deque(maxlen=_HISTORY_LIMIT)
            self._next_check_at[target.id] = 0.0  # 재시작 직후 곧바로 1회 체크되도록

    def _persist(self) -> None:
        payload = [t.to_dict() for t in self._targets.values()]
        self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def add(self, name: str, url: str, method: str = "GET", interval_seconds: int = 60, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> ExternalTarget:
        """대상을 새로 등록. interval_seconds는 최소값(MIN_INTERVAL_SECONDS) 미만이면 강제로 올림."""
        interval = max(MIN_INTERVAL_SECONDS, int(interval_seconds or 60))
        target = ExternalTarget(
            id=uuid.uuid4().hex[:12],
            name=(name or "").strip() or url,
            url=(url or "").strip(),
            method=(method or "GET").upper(),
            interval_seconds=interval,
            timeout_seconds=int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS),
        )
        with self._lock:
            self._targets[target.id] = target
            self._history[target.id] = deque(maxlen=_HISTORY_LIMIT)
            self._next_check_at[target.id] = 0.0
            self._persist()
        return target

    def remove(self, target_id: str) -> bool:
        with self._lock:
            existed = target_id in self._targets
            self._targets.pop(target_id, None)
            self._history.pop(target_id, None)
            self._next_check_at.pop(target_id, None)
            if existed:
                self._persist()
            return existed

    def list_targets(self) -> List[ExternalTarget]:
        with self._lock:
            return list(self._targets.values())

    def due_targets(self) -> List[ExternalTarget]:
        """지금 체크할 시각이 된(주기가 지난) 대상만 반환 - 백그라운드 루프 전용."""
        now = time.time()
        with self._lock:
            return [t for t in self._targets.values() if self._next_check_at.get(t.id, 0.0) <= now]

    def record_probe(self, target: ExternalTarget, result: _ProbeResult) -> None:
        with self._lock:
            if target.id not in self._targets:
                return  # 체크가 진행되는 동안 사용자가 이미 삭제한 대상이면 조용히 버림
            self._history[target.id].append(result)
            self._next_check_at[target.id] = time.time() + target.interval_seconds

    def summary(self) -> List[Dict[str, Any]]:
        """모니터링 탭이 그대로 렌더링할 수 있는 대상별 요약(최근 상태/가동률/평균응답시간)."""
        with self._lock:
            targets = list(self._targets.values())
            histories = {tid: list(h) for tid, h in self._history.items()}

        rows: List[Dict[str, Any]] = []
        for target in targets:
            history = histories.get(target.id, [])
            recent = history[-_RECENT_WINDOW:]
            last = history[-1] if history else None
            success_count = sum(1 for r in recent if r.success)
            uptime_pct = round(100 * success_count / len(recent), 1) if recent else None
            avg_ms = round(sum(r.duration_ms for r in recent if r.success) / success_count, 1) if success_count else None
            rows.append({
                "id": target.id,
                "name": target.name,
                "url": target.url,
                "method": target.method,
                "interval_seconds": target.interval_seconds,
                "checked": last is not None,
                "last_checked_at": last.timestamp if last else None,
                "last_success": last.success if last else None,
                "last_status_code": last.status_code if last else None,
                "last_duration_ms": last.duration_ms if last else None,
                "last_error": last.error if last else None,
                "uptime_pct_recent": uptime_pct,
                "avg_response_ms_recent": avg_ms,
                "check_count": len(history),
                # 응답시간/가동 추이 차트용 - 대상별 최근 체크 이력을 시간순으로 그대로 실어보냄
                "history": [
                    {
                        "timestamp": r.timestamp,
                        "success": r.success,
                        "status_code": r.status_code,
                        "duration_ms": r.duration_ms,
                    }
                    for r in history[-_CHART_HISTORY_LIMIT:]
                ],
            })
        return rows


def probe_once(target: ExternalTarget) -> _ProbeResult:
    """대상 URL에 실제로 HTTP 요청 1회를 보내고 결과를 기록용 객체로 반환.

    2xx/3xx는 성공으로 간주(리다이렉트까지는 "서비스가 응답은 하고 있다"로 봄).
    네트워크 오류/타임아웃은 예외를 여기서 흡수해 실패 결과로 변환합니다 - 호출부(백그라운드
    루프)가 대상 하나의 오류 때문에 죽지 않게 하기 위함입니다.
    """
    started = time.perf_counter()
    try:
        response = requests.request(target.method, target.url, timeout=target.timeout_seconds)
        duration_ms = (time.perf_counter() - started) * 1000
        return _ProbeResult(
            timestamp=time.time(),
            success=200 <= response.status_code < 400,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 1),
        )
    except requests.exceptions.RequestException as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        return _ProbeResult(timestamp=time.time(), success=False, status_code=None, duration_ms=round(duration_ms, 1), error=str(exc))


def run_monitor_loop(registry: ExternalMonitorRegistry, stop_event: Event) -> None:
    """백그라운드 스레드 본체 - 종료 신호가 올 때까지 반복하며, 체크 시각이 된 대상만 골라 probe."""
    while not stop_event.is_set():
        for target in registry.due_targets():
            result = probe_once(target)
            registry.record_probe(target, result)
        stop_event.wait(1)  # 1초마다만 "체크할 게 있는지" 확인 (바쁜 대기 방지)
