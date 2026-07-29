"""사용자 계정 저장소 - 가입 신청/승인/역할(관리자·일반) 관리.

monitoring_addon/db.py와 같은 이유로 같은 패턴을 씀: 새 의존성(ORM 등) 추가 없이 표준
라이브러리 sqlite3만 사용하고, 스레드마다 별도 커넥션을 캐싱해 공유하지 않으므로
check_same_thread 기본값(True)을 그대로 둬도 안전함. 비밀번호 해시는 qa_agent/auth.py의
PBKDF2 함수를 그대로 재사용(별도 알고리즘을 새로 만들지 않음).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from .auth import hash_password, verify_password

_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
"""

# users 테이블에 나중에 추가된 컬럼들 - 이미 운영 중인 users.db(구 스키마)에도 ALTER TABLE로
# 안전하게 얹기 위해 CREATE TABLE의 컬럼 목록에 직접 넣지 않고 별도 마이그레이션으로 분리함
# (신규 DB는 CREATE TABLE 직후 이미 컬럼이 없으므로 마이그레이션이 그대로 추가해준다 -
# 신규/기존 경로를 하나로 통일).
_MIGRATION_COLUMNS = {
    "note": "TEXT NOT NULL DEFAULT ''",
    "contact": "TEXT NOT NULL DEFAULT ''",
}


class UserStore:
    """가입 신청/승인/역할 변경을 담당하는 SQLite 저장소.

    role은 'admin'/'user', status는 'pending'/'approved' 중 하나. 계정이 하나도 없는
    상태(has_any_users()==False)가 app/main.py 입장에서는 "인증 자체가 꺼진 LAN 모드"의
    신호로 쓰임 - 최초 가입자는 자동으로 admin+approved가 되어 그 순간부터 로그인이
    강제됨(닭과 달걀 문제 없이 항상 관리자 1명은 보장).
    """

    def __init__(self, path: str = "data/users.db"):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._schema_lock = Lock()
        self._schema_ready = False
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            conn = sqlite3.connect(self._path, timeout=5.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_DDL)
                existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
                for column, ddl_type in _MIGRATION_COLUMNS.items():
                    if column not in existing_columns:
                        conn.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl_type}")
                conn.commit()
            finally:
                conn.close()
            self._schema_ready = True

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close_thread_connection(self) -> None:
        """현재 스레드의 캐시된 커넥션을 명시적으로 닫음 - 주로 테스트 teardown용."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def has_any_users(self) -> bool:
        row = self._conn().execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None

    def count_admins(self) -> int:
        row = self._conn().execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND status='approved'").fetchone()
        return int(row["n"])

    def create_user(self, username: str, password: str, note: str = "", contact: str = "") -> Optional[Dict[str, Any]]:
        """가입 신청 - 아이디가 이미 있으면 None(호출부가 "이미 사용 중" 안내).

        DB에 계정이 하나도 없을 때(전체 서비스 최초 가입)만 자동으로 admin+approved로
        만들고, 그 외에는 항상 user+pending으로 시작해 관리자 승인을 거치게 함.

        note/contact는 관리자가 승인 여부를 판단할 때 "이 사람이 누구인지" 알 수 있게
        신청 시 함께 받는 자기소개/연락처 - 검증(비어있으면 안 됨 등)은 이 계층이 아니라
        app/main.py::signup_submit이 담당(scripts/create_admin.py처럼 UI 없이 CLI로 만드는
        비상 관리자 계정은 이 값들이 필요 없어 기본값 빈 문자열을 그대로 씀).
        """
        username = username.strip()
        if not username or not password:
            return None
        conn = self._conn()
        is_first = not self.has_any_users()
        role = "admin" if is_first else "user"
        status = "approved" if is_first else "pending"
        try:
            with conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, status, created_at, note, contact) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (username, hash_password(password), role, status, datetime.now(timezone.utc).isoformat(), note.strip(), contact.strip()),
                )
        except sqlite3.IntegrityError:
            return None
        return self.get_user(username)

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        row = self._conn().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def list_all(self) -> List[Dict[str, Any]]:
        rows = self._conn().execute("SELECT id, username, role, status, created_at, note, contact FROM users ORDER BY created_at ASC").fetchall()
        return [dict(row) for row in rows]

    def verify_login(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """승인된 계정이고 비밀번호가 맞을 때만 사용자 dict를 반환, 그 외엔 None.

        "왜 실패했는지"(계정 없음/대기중/비밀번호 오류)는 app/main.py가 get_user()를
        따로 호출해 상태를 보고 사람이 이해할 메시지를 고르도록 분리해뒀음 - 이 메서드는
        "로그인이 되는가/안 되는가"만 판단."""
        user = self.get_user(username)
        if not user or user["status"] != "approved":
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        return user

    def approve_user(self, username: str) -> bool:
        conn = self._conn()
        with conn:
            cursor = conn.execute("UPDATE users SET status='approved' WHERE username = ? AND status='pending'", (username,))
        return cursor.rowcount > 0

    def reject_user(self, username: str) -> bool:
        """대기 중인 신청을 거부 - 승인 대기 행 자체를 지워서, 같은 아이디로 다시 신청할 수 있게 함."""
        conn = self._conn()
        with conn:
            cursor = conn.execute("DELETE FROM users WHERE username = ? AND status='pending'", (username,))
        return cursor.rowcount > 0

    def delete_user(self, username: str) -> bool:
        user = self.get_user(username)
        if not user:
            return False
        if user["role"] == "admin" and user["status"] == "approved" and self.count_admins() <= 1:
            return False  # 마지막 관리자는 삭제 불가(락아웃 방지)
        conn = self._conn()
        with conn:
            cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cursor.rowcount > 0

    def disable_user(self, username: str) -> bool:
        """승인된 계정을 사용 중지(로그인 차단) - 삭제와 달리 데이터/이력이 그대로 남고
        나중에 enable_user로 되돌릴 수 있음. 마지막 남은 관리자는 중지할 수 없음
        (delete_user/set_role과 동일한 락아웃 방지 규칙)."""
        user = self.get_user(username)
        if not user or user["status"] != "approved":
            return False
        if user["role"] == "admin" and self.count_admins() <= 1:
            return False
        conn = self._conn()
        with conn:
            cursor = conn.execute("UPDATE users SET status='disabled' WHERE username = ? AND status='approved'", (username,))
        return cursor.rowcount > 0

    def enable_user(self, username: str) -> bool:
        """사용 중지된 계정을 다시 승인 상태로 되돌림."""
        conn = self._conn()
        with conn:
            cursor = conn.execute("UPDATE users SET status='approved' WHERE username = ? AND status='disabled'", (username,))
        return cursor.rowcount > 0

    def set_role(self, username: str, role: str) -> bool:
        """role은 'admin'/'user'만 허용. 마지막 남은 admin을 user로 내리는 건 항상 거부(락아웃 방지)."""
        if role not in ("admin", "user"):
            return False
        user = self.get_user(username)
        if not user or user["status"] != "approved":
            return False
        if role == "user" and user["role"] == "admin" and self.count_admins() <= 1:
            return False
        conn = self._conn()
        with conn:
            cursor = conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        return cursor.rowcount > 0
