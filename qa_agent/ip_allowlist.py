"""Grafana/Prometheus 프록시 접근을 제어하는 IP 허용목록 - 관리자 CRUD 저장소.

qa_agent/users.py와 동일한 이유로 같은 패턴을 씀: 새 의존성(ORM 등) 추가 없이 표준
라이브러리 sqlite3만 사용하고, 스레드마다 별도 커넥션을 캐싱해 공유하지 않으므로
check_same_thread 기본값(True)을 그대로 둬도 안전함.

IP는 단일 주소(203.0.113.5) 또는 CIDR 대역(203.0.113.0/24) 모두 등록 가능 -
사무실/VPN처럼 IP 범위로 나가는 경우를 위함. 저장은 항상 ipaddress로 정규화한
네트워크 문자열로 하므로(strict=False), "203.0.113.5"와 "203.0.113.5/32"는
같은 값으로 취급되어 중복 등록을 막는다.
"""

from __future__ import annotations

import ipaddress
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

_DDL = """
CREATE TABLE IF NOT EXISTS ip_allowlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    network TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_by TEXT,
    created_at TEXT NOT NULL
);
"""


def normalize_network(ip_or_cidr: str) -> str:
    """단일 IP/CIDR 문자열을 검증하고 정규화된 네트워크 문자열로 변환.

    유효하지 않으면 ValueError(사용자에게 그대로 보여줄 수 있는 메시지 포함)."""
    value = (ip_or_cidr or "").strip()
    if not value:
        raise ValueError("IP 주소 또는 CIDR 대역을 입력하세요")
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ValueError(f"유효한 IP/CIDR 형식이 아닙니다: {value}") from exc
    return str(network)


class IpAllowlistStore:
    """Grafana/Prometheus 프록시(app/main.py의 /grafana-proxy, /prometheus-proxy)
    접근을 허용할 IP/CIDR 목록. 계정이 하나도 없는 LAN 모드에서는 이 저장소 상태와
    무관하게 항상 허용됨(app/main.py::_is_ip_allowed가 판단) - 기존 로그인 정책과
    동일한 부트스트랩 규칙."""

    def __init__(self, path: str = "data/ip_allowlist.db"):
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

    def list_all(self) -> List[Dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT id, network, label, created_by, created_at FROM ip_allowlist ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def add(self, ip_or_cidr: str, label: str, created_by: str) -> Dict[str, Any]:
        """등록 성공 시 새 행을 반환. 이미 등록된 네트워크면 ValueError."""
        network = normalize_network(ip_or_cidr)
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO ip_allowlist (network, label, created_by, created_at) VALUES (?, ?, ?, ?)",
                    (network, (label or "").strip(), created_by, datetime.now(timezone.utc).isoformat()),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"이미 등록된 IP/대역입니다: {network}")
        row = conn.execute("SELECT id, network, label, created_by, created_at FROM ip_allowlist WHERE network = ?", (network,)).fetchone()
        return dict(row)

    def update(self, entry_id: int, ip_or_cidr: Optional[str], label: Optional[str]) -> bool:
        """ip_or_cidr/label 중 넘겨진 값만 수정. 존재하지 않으면 False."""
        conn = self._conn()
        fields: List[str] = []
        params: List[Any] = []
        if ip_or_cidr is not None:
            fields.append("network = ?")
            params.append(normalize_network(ip_or_cidr))
        if label is not None:
            fields.append("label = ?")
            params.append(label.strip())
        if not fields:
            return False
        params.append(entry_id)
        try:
            with conn:
                cursor = conn.execute(f"UPDATE ip_allowlist SET {', '.join(fields)} WHERE id = ?", params)
        except sqlite3.IntegrityError:
            raise ValueError("이미 등록된 IP/대역입니다")
        return cursor.rowcount > 0

    def delete(self, entry_id: int) -> bool:
        conn = self._conn()
        with conn:
            cursor = conn.execute("DELETE FROM ip_allowlist WHERE id = ?", (entry_id,))
        return cursor.rowcount > 0

    def is_allowed(self, client_ip: str) -> bool:
        """client_ip가 등록된 네트워크 중 하나에 포함되면 True. 목록이 비어있으면 False
        (호출부인 app/main.py::_is_ip_allowed가 LAN 모드 예외를 별도로 처리함)."""
        try:
            addr = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        for row in self.list_all():
            try:
                if addr in ipaddress.ip_network(row["network"]):
                    return True
            except ValueError:
                continue
        return False
