import pytest

from qa_agent.ip_allowlist import IpAllowlistStore, normalize_network


@pytest.fixture
def store(tmp_path):
    s = IpAllowlistStore(path=str(tmp_path / "ip_allowlist.db"))
    yield s
    s.close_thread_connection()


def test_normalize_network_accepts_single_ip():
    assert normalize_network("203.0.113.5") == "203.0.113.5/32"


def test_normalize_network_accepts_cidr():
    assert normalize_network("203.0.113.0/24") == "203.0.113.0/24"


def test_normalize_network_rejects_invalid_input():
    with pytest.raises(ValueError):
        normalize_network("not-an-ip")


def test_normalize_network_rejects_empty_input():
    with pytest.raises(ValueError):
        normalize_network("   ")


def test_add_and_list(store):
    entry = store.add("203.0.113.5", "사무실", "alice")
    assert entry["network"] == "203.0.113.5/32"
    assert entry["label"] == "사무실"
    assert entry["created_by"] == "alice"

    entries = store.list_all()
    assert len(entries) == 1
    assert entries[0]["id"] == entry["id"]


def test_add_rejects_duplicate_network(store):
    store.add("203.0.113.5", "first", "alice")
    with pytest.raises(ValueError):
        store.add("203.0.113.5/32", "duplicate (same normalized network)", "bob")


def test_add_rejects_invalid_ip(store):
    with pytest.raises(ValueError):
        store.add("not-an-ip", "", "alice")


def test_delete_removes_entry(store):
    entry = store.add("203.0.113.5", "", "alice")
    assert store.delete(entry["id"]) is True
    assert store.list_all() == []


def test_delete_unknown_id_returns_false(store):
    assert store.delete(999) is False


def test_update_network_and_label(store):
    entry = store.add("203.0.113.5", "old label", "alice")
    assert store.update(entry["id"], "198.51.100.0/24", "new label") is True
    updated = store.list_all()[0]
    assert updated["network"] == "198.51.100.0/24"
    assert updated["label"] == "new label"


def test_update_unknown_id_returns_false(store):
    assert store.update(999, "203.0.113.5", None) is False


def test_update_rejects_duplicate_network(store):
    store.add("203.0.113.5", "", "alice")
    entry_b = store.add("198.51.100.5", "", "alice")
    with pytest.raises(ValueError):
        store.update(entry_b["id"], "203.0.113.5", None)


def test_is_allowed_matches_single_ip(store):
    store.add("203.0.113.5", "", "alice")
    assert store.is_allowed("203.0.113.5") is True
    assert store.is_allowed("203.0.113.6") is False


def test_is_allowed_matches_cidr_range(store):
    store.add("203.0.113.0/24", "", "alice")
    assert store.is_allowed("203.0.113.200") is True
    assert store.is_allowed("203.0.114.1") is False


def test_is_allowed_denies_everything_when_empty(store):
    assert store.is_allowed("203.0.113.5") is False


def test_is_allowed_rejects_invalid_client_ip(store):
    store.add("203.0.113.0/24", "", "alice")
    assert store.is_allowed("not-an-ip") is False
