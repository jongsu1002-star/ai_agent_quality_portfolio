"""SQLite 계정/세션/게시판 DB + reports/ 산출물을 정기적으로 백업하는 운영용 스크립트.

단일 EC2 인스턴스 운영에서는 이 DB/리포트들이 인스턴스 하나에만 존재하므로(별도 RDS/S3
없이 named volume·bind mount에만 저장), 인스턴스 장애·실수로 인한 데이터 손실에 대비해
주기적으로 별도 위치(이 스크립트는 backups/ 디렉터리 - docker-compose.yml에서 호스트에
바인드 마운트해 컨테이너 재생성과 무관하게 남게 함)에 스냅샷을 남겨야 한다.

SQLite는 WAL 모드를 쓰므로 .db 파일만 단순 복사(cp)하면 그 순간 다른 커넥션이 쓰기 중일 때
일관되지 않은 스냅샷을 뜰 위험이 있다 - 대신 sqlite3 표준 라이브러리의 온라인 백업 API
(Connection.backup())를 사용해 항상 일관된 스냅샷을 안전하게 뜬다.

실행 예시(운영, EC2 crontab):
    0 3 * * * docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T qa-platform python scripts/backup_data.py

실행 예시(로컬 확인):
    python scripts/backup_data.py
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("data")
REPORTS_DIR = Path("reports")
BACKUP_ROOT = Path("backups")


def _backup_sqlite_file(src_path: Path, dest_dir: Path) -> Path:
    """SQLite 온라인 백업 API로 src_path를 dest_dir 아래 같은 파일명으로 안전하게 복제."""
    dest_path = dest_dir / src_path.name
    src_conn = sqlite3.connect(str(src_path))
    dest_conn = sqlite3.connect(str(dest_path))
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()
    return dest_path


def _backup_all_sqlite_files(dest_dir: Path) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    backed_up = []
    for db_path in sorted(DATA_DIR.glob("*.db")):
        backed_up.append(_backup_sqlite_file(db_path, dest_dir))
    return backed_up


def _backup_reports(dest_dir: Path) -> Path | None:
    if not REPORTS_DIR.exists():
        return None
    archive_path = dest_dir / "reports_backup.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(REPORTS_DIR, arcname="reports")
    return archive_path


def _apply_retention(retention_count: int) -> list[Path]:
    """오래된 백업 스냅샷을 정리 - 디스크가 무한정 차는 것을 막기 위함(단일 인스턴스라
    디스크 공간이 유한). 최근 retention_count개는 그대로 남기고 그보다 오래된 것만 삭제."""
    if not BACKUP_ROOT.exists():
        return []
    snapshots = sorted((p for p in BACKUP_ROOT.iterdir() if p.is_dir()), key=lambda p: p.name)
    to_delete = snapshots[:-retention_count] if retention_count > 0 else []
    removed = []
    for snapshot in to_delete:
        shutil.rmtree(snapshot, ignore_errors=True)
        removed.append(snapshot)
    return removed


def main() -> int:
    retention_count = int(os.environ.get("BACKUP_RETENTION_COUNT", "7") or "7")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_dir = BACKUP_ROOT / timestamp

    try:
        db_backups = _backup_all_sqlite_files(dest_dir)
        reports_archive = _backup_reports(dest_dir)
    except Exception as exc:
        print(f"[backup_data] 백업 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    removed = _apply_retention(retention_count)

    print(f"[backup_data] 백업 완료: {dest_dir}")
    print(f"[backup_data]   DB {len(db_backups)}개: {', '.join(p.name for p in db_backups) or '없음'}")
    print(f"[backup_data]   reports: {'포함(' + reports_archive.name + ')' if reports_archive else '없음(reports/ 디렉터리 없음)'}")
    if removed:
        print(f"[backup_data]   보관 기간(retention={retention_count}) 초과로 {len(removed)}개 이전 스냅샷 삭제: {', '.join(p.name for p in removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
