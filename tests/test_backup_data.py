import sqlite3
import tarfile

import pytest

from scripts import backup_data


@pytest.fixture(autouse=True)
def _isolate_backup_paths(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    reports_dir = tmp_path / "reports"
    backup_root = tmp_path / "backups"
    data_dir.mkdir()
    monkeypatch.setattr(backup_data, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_data, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(backup_data, "BACKUP_ROOT", backup_root)
    return {"data_dir": data_dir, "reports_dir": reports_dir, "backup_root": backup_root}


def _make_sqlite_db(path, table_data):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (table_data,))
    conn.commit()
    conn.close()


def test_backs_up_all_sqlite_files_with_content_intact(_isolate_backup_paths):
    data_dir = _isolate_backup_paths["data_dir"]
    _make_sqlite_db(data_dir / "users.db", "alice")
    _make_sqlite_db(data_dir / "sessions.db", "tok1")

    assert backup_data.main() == 0

    snapshots = list(_isolate_backup_paths["backup_root"].iterdir())
    assert len(snapshots) == 1
    backed_up_users_db = snapshots[0] / "users.db"
    assert backed_up_users_db.exists()
    conn = sqlite3.connect(str(backed_up_users_db))
    assert conn.execute("SELECT v FROM t").fetchone()[0] == "alice"
    conn.close()


def test_backs_up_reports_directory_as_tar_gz(_isolate_backup_paths):
    reports_dir = _isolate_backup_paths["reports_dir"]
    reports_dir.mkdir(exist_ok=True)  # conftest.py의 다른 autouse 픽스처가 같은 tmp_path 아래 reports/를 먼저 만들어둘 수 있음
    (reports_dir / "run_1.json").write_text('{"ok": true}', encoding="utf-8")

    assert backup_data.main() == 0

    snapshots = list(_isolate_backup_paths["backup_root"].iterdir())
    archive_path = snapshots[0] / "reports_backup.tar.gz"
    assert archive_path.exists()
    with tarfile.open(archive_path) as tar:
        names = tar.getnames()
    assert any("run_1.json" in name for name in names)


def test_missing_reports_directory_does_not_fail_backup(_isolate_backup_paths):
    """reports/ 디렉터리가 아예 없어도(신규 배포 직후 등) 백업 자체는 실패하지 않고
    DB만이라도 백업해야 한다."""
    _make_sqlite_db(_isolate_backup_paths["data_dir"] / "users.db", "alice")
    assert backup_data.main() == 0


def test_retention_removes_old_snapshots_beyond_limit(_isolate_backup_paths, monkeypatch):
    monkeypatch.setenv("BACKUP_RETENTION_COUNT", "2")
    backup_root = _isolate_backup_paths["backup_root"]
    backup_root.mkdir()
    for name in ["20260101T000000Z", "20260102T000000Z", "20260103T000000Z"]:
        (backup_root / name).mkdir()

    removed = backup_data._apply_retention(2)
    remaining = sorted(p.name for p in backup_root.iterdir())
    assert remaining == ["20260102T000000Z", "20260103T000000Z"]
    assert [p.name for p in removed] == ["20260101T000000Z"]


def test_retention_of_zero_disables_cleanup(_isolate_backup_paths):
    backup_root = _isolate_backup_paths["backup_root"]
    backup_root.mkdir()
    (backup_root / "20260101T000000Z").mkdir()

    removed = backup_data._apply_retention(0)
    assert removed == []
    assert (backup_root / "20260101T000000Z").exists()


def test_backup_failure_returns_nonzero_and_does_not_raise(_isolate_backup_paths, monkeypatch):
    """개별 DB 파일 백업이 실패해도(예: 손상된 파일) 예외를 그대로 흘려보내 스크립트를
    크래시시키지 말고, 실패를 숨기지도 말고 0이 아닌 종료 코드로 정직하게 알려야 한다."""
    data_dir = _isolate_backup_paths["data_dir"]
    (data_dir / "corrupt.db").write_bytes(b"not a real sqlite file")

    assert backup_data.main() == 1
