"""모니터링 애드온 전용 SQLite - 기존 플랫폼의 어떤 DB/파일도 건드리지 않는 별도 파일.

FastAPI 동기 핸들러(uvicorn 스레드풀 - 여러 OS 스레드)와 스냅샷 백그라운드 스레드가 동시에
같은 SQLite 파일에 쓸 수 있어서, 커넥션 객체를 스레드 간에 절대 공유하지 않는 "스레드별 커넥션
캐시"(threading.local) 방식을 씁니다. 커넥션이 스레드 경계를 넘지 않으므로
`check_same_thread=False`가 필요 없고(그게 필요해지는 순간이 바로 위험 신호), WAL 모드로
읽기/쓰기가 서로를 거의 막지 않게 합니다. SQLAlchemy 등 새 의존성은 추가하지 않고 표준
라이브러리 `sqlite3`만 사용합니다(이 프로젝트의 최소 의존성 원칙 유지).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

_DDL = """
CREATE TABLE IF NOT EXISTS k6_test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    scenario TEXT,
    target_url TEXT,
    vus INTEGER,
    total_requests INTEGER,
    failed_rate REAL,
    checks_rate REAL,
    avg_ms REAL,
    min_ms REAL,
    med_ms REAL,
    max_ms REAL,
    p90_ms REAL,
    p95_ms REAL,
    p99_ms REAL,
    thresholds_passed INTEGER,
    result TEXT,
    raw_json_path TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_k6_runs_created_at ON k6_test_runs(created_at);

CREATE TABLE IF NOT EXISTS k6_threshold_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    metric_name TEXT,
    condition TEXT,
    passed INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_k6_thresholds_run_id ON k6_threshold_results(run_id);

CREATE TABLE IF NOT EXISTS monitoring_summary_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_time REAL NOT NULL,
    total_requests INTEGER,
    total_errors INTEGER,
    error_rate REAL,
    avg_response_ms REAL,
    p95_response_ms REAL,
    source TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON monitoring_summary_snapshots(snapshot_time);
"""


class MonitoringAddonDB:
    """k6 실행 이력 + 장기 요약 스냅샷을 저장하는 애드온 전용 저장소."""

    def __init__(self, path: str = "data/monitoring_addon.db"):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._schema_lock = Lock()
        self._schema_ready = False
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """CREATE TABLE IF NOT EXISTS는 그 자체로 멱등이지만, 이 객체를 여러 스레드가
        동시에 처음 생성할 때 DDL 실행이 겹치지 않도록 최초 1회만 실행되게 막습니다."""
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:  # 락 대기 중 다른 스레드가 이미 끝냈을 수 있음
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
        """호출 스레드 전용 커넥션을 재사용 - 다른 스레드에 절대 넘기지 않으므로
        check_same_thread 기본값(True)을 그대로 둬도 안전합니다."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close_thread_connection(self) -> None:
        """현재 스레드의 캐시된 커넥션을 명시적으로 닫음 - 주로 테스트 teardown에서,
        Windows의 tmp_path 정리가 열린 파일 핸들 때문에 실패하는 것을 막기 위해 사용."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ---------- k6_test_runs / k6_threshold_results ----------

    def insert_k6_run(self, run: Dict[str, Any], raw_json_path: str, thresholds: List[Dict[str, Any]]) -> bool:
        """k6 실행 1건을 저장. 같은 run_id가 이미 있으면 아무것도 하지 않고 False를 반환
        (설계서 8.4: "같은 run_id가 이미 있으면 중복 저장하지 않음")."""
        conn = self._conn()
        now = time.time()
        with conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO k6_test_runs
                    (run_id, scenario, target_url, vus, total_requests, failed_rate, checks_rate,
                     avg_ms, min_ms, med_ms, max_ms, p90_ms, p95_ms, p99_ms,
                     thresholds_passed, result, raw_json_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.get("run_id"),
                    run.get("scenario"),
                    run.get("target_url"),
                    run.get("vus"),
                    run.get("total_requests"),
                    run.get("failed_rate"),
                    run.get("checks_rate"),
                    run.get("http_req_duration", {}).get("avg_ms"),
                    run.get("http_req_duration", {}).get("min_ms"),
                    run.get("http_req_duration", {}).get("med_ms"),
                    run.get("http_req_duration", {}).get("max_ms"),
                    run.get("http_req_duration", {}).get("p90_ms"),
                    run.get("http_req_duration", {}).get("p95_ms"),
                    run.get("http_req_duration", {}).get("p99_ms"),
                    1 if run.get("thresholds_passed") else 0,
                    run.get("result"),
                    raw_json_path,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                return False  # 이미 존재 - threshold 행도 새로 넣지 않고 그대로 반환

            for item in thresholds:
                conn.execute(
                    "INSERT INTO k6_threshold_results (run_id, metric_name, condition, passed, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (run.get("run_id"), item.get("name"), item.get("condition"), 1 if item.get("passed") else 0, now),
                )
        return True

    def _thresholds_for(self, run_id: str) -> List[Dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT metric_name, condition, passed FROM k6_threshold_results WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [{"name": r["metric_name"], "condition": r["condition"], "passed": bool(r["passed"])} for r in rows]

    def get_latest_k6_run(self) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM k6_test_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        if row is None:
            return None
        result = dict(row)
        result["thresholds"] = self._thresholds_for(result["run_id"])
        return result

    def get_k6_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM k6_test_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["thresholds"] = self._thresholds_for(run_id)
        return result

    def list_k6_runs(self, page: int = 1, page_size: int = 10, result_filter: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
        conn = self._conn()
        where = ""
        params: List[Any] = []
        if result_filter:
            where = "WHERE result = ?"
            params.append(result_filter)

        total = conn.execute(f"SELECT COUNT(*) FROM k6_test_runs {where}", params).fetchone()[0]
        offset = max(0, (page - 1) * page_size)
        rows = conn.execute(
            f"SELECT * FROM k6_test_runs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    # ---------- monitoring_summary_snapshots ----------

    def insert_snapshot(self, total_requests: int, total_errors: int, error_rate: float, avg_response_ms: float, p95_response_ms: float, source: str) -> None:
        conn = self._conn()
        with conn:
            conn.execute(
                "INSERT INTO monitoring_summary_snapshots "
                "(snapshot_time, total_requests, total_errors, error_rate, avg_response_ms, p95_response_ms, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), total_requests, total_errors, error_rate, avg_response_ms, p95_response_ms, source, time.time()),
            )

    def recent_snapshots(self, limit: int = 60) -> List[Dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM monitoring_summary_snapshots ORDER BY snapshot_time DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
