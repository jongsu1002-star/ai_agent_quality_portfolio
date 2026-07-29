import time

import pytest

from qa_agent.sessions import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(path=str(tmp_path / "sessions.db"))
    yield s
    s.close_thread_connection()


def test_create_and_get_session(store):
    store.create_session("tok1", "alice", ttl_seconds=3600)
    assert store.get_username("tok1") == "alice"


def test_unknown_token_returns_none(store):
    assert store.get_username("does-not-exist") is None


def test_expired_session_returns_none(store):
    store.create_session("tok1", "alice", ttl_seconds=-1)  # already expired
    assert store.get_username("tok1") is None


def test_expired_session_is_cleaned_up_on_read(store):
    store.create_session("tok1", "alice", ttl_seconds=-1)
    store.get_username("tok1")  # triggers lazy delete
    conn = store._conn()
    row = conn.execute("SELECT 1 FROM sessions WHERE token = ?", ("tok1",)).fetchone()
    assert row is None


def test_delete_session_invalidates_it(store):
    store.create_session("tok1", "alice", ttl_seconds=3600)
    store.delete_session("tok1")
    assert store.get_username("tok1") is None


def test_delete_session_on_missing_token_is_a_noop(store):
    store.delete_session("never-existed")  # should not raise


def test_delete_sessions_for_user_removes_all_their_tokens(store):
    store.create_session("tok1", "alice", ttl_seconds=3600)
    store.create_session("tok2", "alice", ttl_seconds=3600)
    store.create_session("tok3", "bob", ttl_seconds=3600)

    removed = store.delete_sessions_for_user("alice")
    assert removed == 2
    assert store.get_username("tok1") is None
    assert store.get_username("tok2") is None
    assert store.get_username("tok3") == "bob"  # bob's session untouched


def test_create_session_opportunistically_purges_expired_rows(store):
    store.create_session("old", "alice", ttl_seconds=-1)  # already expired
    store.create_session("new", "bob", ttl_seconds=3600)  # triggers cleanup sweep

    conn = store._conn()
    row = conn.execute("SELECT 1 FROM sessions WHERE token = ?", ("old",)).fetchone()
    assert row is None
    assert store.get_username("new") == "bob"


def test_creating_session_with_same_token_overwrites_previous_owner(store):
    """토큰이 우연히 재사용되는 이론적 경우에도(예: 테스트에서 고정 토큰을 쓸 때) 조용히
    덮어써야지 예외가 나면 안 됨 - INSERT OR REPLACE로 처리."""
    store.create_session("tok1", "alice", ttl_seconds=3600)
    store.create_session("tok1", "bob", ttl_seconds=3600)
    assert store.get_username("tok1") == "bob"
