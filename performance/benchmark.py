"""범용 실행 시간 측정 유틸. 부하/동시성 테스트 도구가 아니라 "이 함수 한 번 도는데
얼마나 걸리나"만 재는 간단한 타이머입니다 (부하 테스트는 k6/locust 등을 사용)."""

from __future__ import annotations

import time
from typing import Callable, Dict, Any


class BenchmarkRunner:
    def run(self, func: Callable[[], Any]) -> Dict[str, Any]:
        """func()를 한 번 실행하고 소요 시간(초)과 반환값을 함께 돌려줌."""
        started = time.perf_counter()
        result = func()
        elapsed = time.perf_counter() - started
        return {"elapsed_seconds": round(elapsed, 4), "result": result}
