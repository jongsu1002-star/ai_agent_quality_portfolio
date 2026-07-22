"""실행 실패 시 사용자 화면에는 안내 메시지만 보여주고(원문 예외를 그대로 노출하면
내부 경로/설정값이 새어나갈 수 있음), 실제 원인은 관리자가 '오류 로그' 화면에서
확인할 수 있도록 서버 메모리에 최근 N건만 별도로 보관한다."""
from __future__ import annotations

import traceback
from collections import deque
from datetime import datetime
from threading import Lock
from typing import Any, Deque, Dict, List, Optional

_MAX_ENTRIES = 200
_lock = Lock()
_entries: Deque[Dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)


def record_error(feature: str, exc: BaseException, *, username: Optional[str] = None, run_id: Optional[str] = None) -> None:
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "feature": feature,
        "username": username,
        "run_id": run_id,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:],
    }
    with _lock:
        _entries.appendleft(entry)


def list_errors(limit: int = 100) -> List[Dict[str, Any]]:
    with _lock:
        return list(_entries)[:limit]
