from pathlib import Path

import pytest

from qa_agent.users import UserStore


@pytest.fixture
def store(tmp_path):
    s = UserStore(path=str(tmp_path / "users.db"))
    yield s
    s.close_thread_connection()


def test_first_user_is_auto_approved_admin(store):
    user = store.create_user("alice", "secret123")
    assert user["role"] == "admin"
    assert user["status"] == "approved"
    assert store.has_any_users() is True


def test_second_user_starts_as_pending(store):
    store.create_user("alice", "secret123")
    bob = store.create_user("bob", "secret456")
    assert bob["role"] == "user"
    assert bob["status"] == "pending"


def test_duplicate_username_is_rejected(store):
    store.create_user("alice", "secret123")
    assert store.create_user("alice", "different-password") is None


def test_pending_user_cannot_log_in_until_approved(store):
    store.create_user("alice", "secret123")
    store.create_user("bob", "secret456")

    assert store.verify_login("bob", "secret456") is None  # still pending
    assert store.approve_user("bob") is True
    assert store.verify_login("bob", "secret456") is not None


def test_verify_login_rejects_wrong_password(store):
    store.create_user("alice", "secret123")
    assert store.verify_login("alice", "wrong-password") is None


def test_reject_user_removes_the_pending_row(store):
    store.create_user("alice", "secret123")
    store.create_user("bob", "secret456")

    assert store.reject_user("bob") is True
    assert store.get_user("bob") is None
    # 거부 후에는 같은 아이디로 다시 신청할 수 있어야 함
    assert store.create_user("bob", "secret789") is not None


def test_reject_only_affects_pending_accounts(store):
    store.create_user("alice", "secret123")  # already approved (first user)
    assert store.reject_user("alice") is False


def test_set_role_grants_and_revokes_admin(store):
    store.create_user("alice", "secret123")  # admin
    store.create_user("bob", "secret456")
    store.approve_user("bob")

    assert store.set_role("bob", "admin") is True
    assert store.get_user("bob")["role"] == "admin"
    assert store.count_admins() == 2

    assert store.set_role("bob", "user") is True
    assert store.get_user("bob")["role"] == "user"


def test_last_admin_cannot_be_demoted(store):
    store.create_user("alice", "secret123")  # sole admin
    assert store.set_role("alice", "user") is False
    assert store.get_user("alice")["role"] == "admin"


def test_last_admin_cannot_be_deleted(store):
    store.create_user("alice", "secret123")
    assert store.delete_user("alice") is False


def test_admin_can_be_deleted_when_another_admin_remains(store):
    store.create_user("alice", "secret123")
    store.create_user("bob", "secret456")
    store.approve_user("bob")
    store.set_role("bob", "admin")

    assert store.delete_user("alice") is True
    assert store.get_user("alice") is None
    assert store.count_admins() == 1


def test_set_role_rejects_unknown_role(store):
    store.create_user("alice", "secret123")
    assert store.set_role("alice", "superuser") is False


def test_list_all_excludes_password_hash(store):
    store.create_user("alice", "secret123")
    users = store.list_all()
    assert len(users) == 1
    assert "password_hash" not in users[0]
    assert users[0]["username"] == "alice"


def test_create_user_stores_note_and_contact(store):
    store.create_user("alice", "secret123", note="  QA팀 홍길동입니다  ", contact="  010-1234-5678  ")
    user = store.get_user("alice")
    assert user["note"] == "QA팀 홍길동입니다"  # 앞뒤 공백은 저장 시 정리됨
    assert user["contact"] == "010-1234-5678"


def test_create_user_defaults_note_and_contact_to_empty_string(store):
    store.create_user("alice", "secret123")
    user = store.get_user("alice")
    assert user["note"] == ""
    assert user["contact"] == ""


def test_list_all_includes_note_and_contact(store):
    store.create_user("alice", "secret123", note="관리자입니다", contact="alice@example.com")
    users = store.list_all()
    assert users[0]["note"] == "관리자입니다"
    assert users[0]["contact"] == "alice@example.com"


def test_disable_user_blocks_future_login(store):
    store.create_user("alice", "secret123")  # admin
    store.create_user("bob", "secret456")
    store.approve_user("bob")

    assert store.disable_user("bob") is True
    assert store.get_user("bob")["status"] == "disabled"
    assert store.verify_login("bob", "secret456") is None


def test_enable_user_restores_login(store):
    store.create_user("alice", "secret123")
    store.create_user("bob", "secret456")
    store.approve_user("bob")
    store.disable_user("bob")

    assert store.enable_user("bob") is True
    assert store.get_user("bob")["status"] == "approved"
    assert store.verify_login("bob", "secret456") is not None


def test_disable_user_rejects_pending_account(store):
    store.create_user("alice", "secret123")
    store.create_user("bob", "secret456")  # still pending, not approved
    assert store.disable_user("bob") is False


def test_last_admin_cannot_be_disabled(store):
    store.create_user("alice", "secret123")  # sole admin
    assert store.disable_user("alice") is False
    assert store.get_user("alice")["status"] == "approved"


def test_admin_can_be_disabled_when_another_admin_remains(store):
    store.create_user("alice", "secret123")
    store.create_user("bob", "secret456")
    store.approve_user("bob")
    store.set_role("bob", "admin")

    assert store.disable_user("alice") is True
    assert store.get_user("alice")["status"] == "disabled"


def test_enable_user_rejects_account_that_is_not_disabled(store):
    store.create_user("alice", "secret123")
    assert store.enable_user("alice") is False  # already approved, not disabled


def test_existing_database_without_note_contact_columns_migrates_safely(tmp_path):
    """이미 운영 중인 users.db(신원 정보 컬럼이 생기기 전 구 스키마)를 여는 UserStore가
    ALTER TABLE로 컬럼을 안전하게 추가해야 하고, 기존 행은 빈 문자열 기본값을 받아야 한다
    - 실제 운영 DB를 덮어쓰거나 초기화하지 않는다는 제약과 직결되는 회귀 테스트."""
    import sqlite3

    db_path = tmp_path / "legacy_users.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, role, status, created_at) VALUES (?, ?, ?, ?, ?)",
        ("legacy_admin", "irrelevant-hash", "admin", "approved", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    migrated = UserStore(path=str(db_path))
    try:
        legacy_user = migrated.get_user("legacy_admin")
        assert legacy_user["note"] == ""
        assert legacy_user["contact"] == ""
        # 새 가입자는 정상적으로 note/contact를 받을 수 있어야 함
        migrated.create_user("newbie", "secret123", note="신규", contact="new@example.com")
        assert migrated.get_user("newbie")["note"] == "신규"
    finally:
        migrated.close_thread_connection()
