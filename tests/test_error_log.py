import pytest

from qa_agent import error_log


@pytest.fixture(autouse=True)
def _clear_error_log():
    error_log._entries.clear()
    yield
    error_log._entries.clear()


def test_record_error_captures_type_message_and_traceback():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        error_log.record_error("qa_pipeline", exc, username="alice", run_id="run-1")

    entries = error_log.list_errors()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["feature"] == "qa_pipeline"
    assert entry["username"] == "alice"
    assert entry["run_id"] == "run-1"
    assert entry["error_type"] == "ValueError"
    assert entry["message"] == "boom"
    assert "ValueError: boom" in entry["traceback"]
    assert entry["at"]


def test_list_errors_returns_most_recent_first():
    for i in range(3):
        try:
            raise RuntimeError(f"error-{i}")
        except RuntimeError as exc:
            error_log.record_error("voc_analysis", exc)

    entries = error_log.list_errors()
    assert [e["message"] for e in entries] == ["error-2", "error-1", "error-0"]


def test_list_errors_respects_limit():
    for i in range(5):
        try:
            raise RuntimeError(f"error-{i}")
        except RuntimeError as exc:
            error_log.record_error("voc_analysis", exc)

    assert len(error_log.list_errors(limit=2)) == 2


def test_ring_buffer_drops_oldest_entries_beyond_max():
    total = error_log._MAX_ENTRIES + 10
    for i in range(total):
        try:
            raise RuntimeError(f"error-{i}")
        except RuntimeError as exc:
            error_log.record_error("voc_analysis", exc)

    entries = error_log.list_errors(limit=error_log._MAX_ENTRIES)
    assert len(entries) == error_log._MAX_ENTRIES
    assert entries[0]["message"] == f"error-{total - 1}"
    assert entries[-1]["message"] == f"error-{total - error_log._MAX_ENTRIES}"
