"""게시판(일반/FAQ/VOC) + 댓글 저장소.

qa_agent/users.py와 동일한 이유로 같은 패턴을 씀: 새 의존성(ORM 등) 없이 표준 라이브러리
sqlite3만 사용하고, 스레드마다 별도 커넥션을 캐싱해 공유하지 않으므로 check_same_thread
기본값(True)을 그대로 둬도 안전함.

게시판은 데이터셋/테스트케이스와 달리 사용자별로 격리하지 않음 - 전체 로그인 사용자가
공유하는 팀 공용 데이터라서 단일 DB 파일(data/board.db)에 author만 기록.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

BOARD_TYPES = ("general", "faq", "voc")

_DDL = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board_type TEXT NOT NULL CHECK(board_type IN ('general','faq','voc')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    author TEXT NOT NULL,
    visible INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_board_type ON posts(board_type, created_at DESC);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);
"""


class BoardStore:
    """게시글/댓글 CRUD - 소유자(작성자) 권한 판단은 여기서 하지 않고 호출부(라우트)에 맡김.

    board_type은 'general'/'faq'/'voc' 중 하나. 댓글은 게시글 삭제 시 FK CASCADE로 함께
    삭제되므로 별도 정리 로직이 필요 없음(단, SQLite는 연결마다 PRAGMA foreign_keys=ON을
    켜줘야 CASCADE가 실제로 동작함).
    """

    def __init__(self, path: str = "data/board.db"):
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
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close_thread_connection(self) -> None:
        """현재 스레드의 캐시된 커넥션을 명시적으로 닫음 - 주로 테스트 teardown용."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ---------------------------------------------------------------- posts

    def create_post(self, board_type: str, title: str, content: str, author: str) -> Optional[Dict[str, Any]]:
        if board_type not in BOARD_TYPES or not title.strip() or not author:
            return None
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        with conn:
            cursor = conn.execute(
                "INSERT INTO posts (board_type, title, content, author, visible, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (board_type, title, content, author, now, now),
            )
        return self.get_post(cursor.lastrowid)

    def get_post(self, post_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn().execute(
            """
            SELECT p.*, (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count
            FROM posts p WHERE p.id = ?
            """,
            (post_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_post_author(self, post_id: int) -> Optional[str]:
        row = self._conn().execute("SELECT author FROM posts WHERE id = ?", (post_id,)).fetchone()
        return row["author"] if row else None

    def list_posts(
        self,
        board_type: str,
        limit: int = 300,
        offset: int = 0,
        search: Optional[str] = None,
        viewer: Optional[str] = None,
        include_hidden: bool = False,
    ) -> List[Dict[str, Any]]:
        """viewer/include_hidden으로 비노출 글의 가시 범위를 제어.

        include_hidden=True(관리자)면 비노출 글도 전부 보임. 그 외에는 노출 글 + (viewer가
        지정됐다면) 본인이 쓴 비노출 글까지만 보임 - 작성자는 자기 비노출 글을 계속 관리할
        수 있어야 하기 때문."""
        conn = self._conn()
        query = """
            SELECT p.*, (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count
            FROM posts p WHERE p.board_type = ?
        """
        params: List[Any] = [board_type]
        if not include_hidden:
            if viewer:
                query += " AND (p.visible = 1 OR p.author = ?)"
                params.append(viewer)
            else:
                query += " AND p.visible = 1"
        if search:
            query += " AND (p.title LIKE ? OR p.content LIKE ?)"
            like = f"%{search}%"
            params += [like, like]
        query += " ORDER BY p.created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def count_posts(
        self,
        board_type: str,
        search: Optional[str] = None,
        viewer: Optional[str] = None,
        include_hidden: bool = False,
    ) -> int:
        conn = self._conn()
        query = "SELECT COUNT(*) AS n FROM posts WHERE board_type = ?"
        params: List[Any] = [board_type]
        if not include_hidden:
            if viewer:
                query += " AND (visible = 1 OR author = ?)"
                params.append(viewer)
            else:
                query += " AND visible = 1"
        if search:
            query += " AND (title LIKE ? OR content LIKE ?)"
            like = f"%{search}%"
            params += [like, like]
        row = conn.execute(query, params).fetchone()
        return int(row["n"])

    def set_post_visibility(self, post_id: int, visible: bool) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        with conn:
            cursor = conn.execute(
                "UPDATE posts SET visible = ?, updated_at = ? WHERE id = ?",
                (1 if visible else 0, datetime.now(timezone.utc).isoformat(), post_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_post(post_id)

    def update_post(self, post_id: int, title: str, content: str) -> Optional[Dict[str, Any]]:
        if not title.strip():
            return None
        conn = self._conn()
        with conn:
            cursor = conn.execute(
                "UPDATE posts SET title = ?, content = ?, updated_at = ? WHERE id = ?",
                (title, content, datetime.now(timezone.utc).isoformat(), post_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_post(post_id)

    def delete_post(self, post_id: int) -> bool:
        conn = self._conn()
        with conn:
            cursor = conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------- comments

    def add_comment(self, post_id: int, author: str, content: str) -> Optional[Dict[str, Any]]:
        if not content.strip() or not author or not self.get_post(post_id):
            return None
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        with conn:
            cursor = conn.execute(
                "INSERT INTO comments (post_id, author, content, created_at) VALUES (?, ?, ?, ?)",
                (post_id, author, content, now),
            )
        row = conn.execute("SELECT * FROM comments WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row) if row else None

    def list_comments(self, post_id: int) -> List[Dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT * FROM comments WHERE post_id = ? ORDER BY created_at ASC", (post_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_comment_author(self, comment_id: int) -> Optional[str]:
        row = self._conn().execute("SELECT author FROM comments WHERE id = ?", (comment_id,)).fetchone()
        return row["author"] if row else None

    def update_comment(self, comment_id: int, content: str) -> Optional[Dict[str, Any]]:
        if not content.strip():
            return None
        conn = self._conn()
        with conn:
            cursor = conn.execute("UPDATE comments SET content = ? WHERE id = ?", (content, comment_id))
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
        return dict(row) if row else None

    def delete_comment(self, comment_id: int) -> bool:
        conn = self._conn()
        with conn:
            cursor = conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        return cursor.rowcount > 0
