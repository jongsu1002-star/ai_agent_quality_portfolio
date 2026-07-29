"""로그인 세션 저장소 - SQLite에 영속화해 서버 재시작에도 로그인 상태가 유지되게 함.

qa_agent/users.py와 완전히 동일한 패턴(스레드별 커넥션 캐싱, WAL 모드)을 재사용한다.
이전에는 app/main.py의 전역 dict(`_ACTIVE_SESSIONS`)에만 세션을 들고 있어 서버 재시작·
재배포(`docker compose up -d --build`) 때마다 모든 로그인이 풀리는 문제가 있었음 - Redis 같은
새 인프라를 들이는 대신, 이 프로젝트가 이미 계정/게시판/IP허용목록에 쓰고 있는 SQLite
패턴을 그대로 재사용해 단일 워커/단일 SQLite 제약과 일치시켰다.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from threading import Lock
from typing import Optional

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""


class SessionStore:
    """토큰 -> 사용자명 매핑을 만료 시각과 함께 SQLite에 저장."""

    def __init__(self, path: str = "data/sessions.db"):
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

    def create_session(self, token: str, username: str, ttl_seconds: float) -> None:
        """세션 생성 - 호출 시점에 이미 만료된 오래된 세션도 함께 청소(무제한 증가 방지,
        별도 백그라운드 정리 작업 없이 자연스럽게 크기를 관리)."""
        now = time.time()
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT OR REPLACE INTO sessions (token, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, username, now, now + ttl_seconds),
            )

    def get_username(self, token: str) -> Optional[str]:
        """유효한(만료되지 않은) 세션이면 사용자명을, 아니면 None을 반환.

        이미 만료된 행을 우연히 조회했다면 그 자리에서 지워 다음 조회 때 다시 안 걸리게 함."""
        row = self._conn().execute("SELECT username, expires_at FROM sessions WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None
        if row["expires_at"] < time.time():
            self.delete_session(token)
            return None
        return row["username"]

    def delete_session(self, token: str) -> None:
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def delete_sessions_for_user(self, username: str) -> int:
        """해당 사용자의 모든 세션을 무효화(계정 사용 중지 시, 이미 로그인된 세션도 즉시
        끊기 위해 사용). 삭제된 세션 수를 반환."""
        conn = self._conn()
        with conn:
            cursor = conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        return cursor.rowcount
