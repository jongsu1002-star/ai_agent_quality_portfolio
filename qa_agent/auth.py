"""비밀번호 해시 생성/검증 - 순수 유틸리티.

qa_agent/users.py(계정별 비밀번호)와 scripts/create_admin.py가 공유해서 쓰는 함수
모음입니다. FastAPI 앱이나 서버 상태에 의존하지 않아 임포트만 해도 부작용이 없습니다
(qa_agent/는 코어 로직 전용이고 app/은 결과를 노출만 한다는 이 프로젝트의 기본 원칙과
같은 이유).
"""

from __future__ import annotations

import hashlib
import os
import secrets

PBKDF2_ITERATIONS = 260_000  # OWASP 2023 권장 최소값 근처


def hash_password(password: str) -> str:
    """`{iterations}${salt_hex}${hash_hex}` 형태의 자기완결형 문자열을 만듦 - 별도 salt
    컬럼 없이 이 한 줄만 저장하면 됨."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """형식이 깨졌거나(수동 편집 실수 등) 비어있으면 조용히 실패(False) 처리."""
    try:
        iterations_str, salt_hex, hash_hex = stored_hash.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations_str))
    return secrets.compare_digest(candidate, expected)
