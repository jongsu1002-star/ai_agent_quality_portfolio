"""k6 실행 결과 JSON의 필수 필드를 검증하는 가벼운 모델.

DB 저장/조회는 db.py가 plain dict로 직접 처리하므로(불필요한 변환 계층을 두지 않기 위해),
여기서는 "이 dict가 k6 결과로서 최소한의 모양을 갖췄는가"만 검증합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

_REQUIRED_FIELDS = ("run_id", "total_requests", "failed_rate", "result")


@dataclass
class K6RunValidationError(Exception):
    """k6 결과 JSON에 필수 필드가 없을 때 - k6_result_reader가 invalid_json으로 취급."""

    missing: List[str]

    def __str__(self) -> str:
        return f"k6 result missing required fields: {self.missing}"


def validate_k6_run(data: Dict[str, Any]) -> Dict[str, Any]:
    """필수 필드가 다 있는지만 확인하고 그대로 반환 (변형하지 않음)."""
    missing = [f for f in _REQUIRED_FIELDS if f not in data]
    if missing:
        raise K6RunValidationError(missing)
    return data
