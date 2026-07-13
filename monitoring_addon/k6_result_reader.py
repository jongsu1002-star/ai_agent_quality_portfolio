"""reports/k6/*.json 파일을 읽어 dict로 반환 - 파일 없음/JSON 깨짐을 구분해서 예외로 알림.

라우터가 이 예외들을 잡아 설계서 11장 표대로 `no_data`/`invalid_json` 응답으로 바꿉니다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .models import K6RunValidationError, validate_k6_run


class K6ResultNotFound(Exception):
    """reports/k6/latest.json (또는 history 파일)이 존재하지 않음 - "no_data" 상황."""


class K6ResultInvalid(Exception):
    """파일은 있지만 JSON 파싱에 실패했거나 필수 필드가 없음 - "invalid_json" 상황."""


def read_k6_result(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise K6ResultNotFound(str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise K6ResultInvalid(f"invalid JSON in {path}: {exc}") from exc
    try:
        return validate_k6_run(data)
    except K6RunValidationError as exc:
        raise K6ResultInvalid(str(exc)) from exc
