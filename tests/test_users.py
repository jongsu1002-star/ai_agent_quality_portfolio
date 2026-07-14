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
