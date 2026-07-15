import pytest

from qa_agent.board import BoardStore


@pytest.fixture
def store(tmp_path):
    s = BoardStore(path=str(tmp_path / "board.db"))
    yield s
    s.close_thread_connection()


def test_create_and_get_post(store):
    post = store.create_post("general", "제목", "내용", "alice")
    assert post["board_type"] == "general"
    assert post["visible"] == 1
    assert post["comment_count"] == 0
    fetched = store.get_post(post["id"])
    assert fetched["title"] == "제목"


def test_create_post_rejects_unknown_board_type(store):
    assert store.create_post("unknown", "t", "c", "alice") is None


def test_create_post_rejects_empty_title(store):
    assert store.create_post("general", "  ", "c", "alice") is None


def test_list_posts_filters_by_board_type(store):
    store.create_post("general", "g1", "c", "alice")
    store.create_post("faq", "f1", "c", "alice")
    general = store.list_posts("general")
    assert len(general) == 1
    assert general[0]["title"] == "g1"


def test_list_posts_search(store):
    store.create_post("general", "hello world", "c", "alice")
    store.create_post("general", "other", "c", "alice")
    results = store.list_posts("general", search="hello")
    assert len(results) == 1


def test_update_post(store):
    post = store.create_post("general", "orig", "c", "alice")
    updated = store.update_post(post["id"], "new title", "new content")
    assert updated["title"] == "new title"
    assert updated["content"] == "new content"


def test_update_post_rejects_empty_title(store):
    post = store.create_post("general", "orig", "c", "alice")
    assert store.update_post(post["id"], "", "new content") is None


def test_delete_post_cascades_comments(store):
    post = store.create_post("general", "t", "c", "alice")
    store.add_comment(post["id"], "bob", "hi")
    assert len(store.list_comments(post["id"])) == 1
    assert store.delete_post(post["id"]) is True
    assert store.get_post(post["id"]) is None
    assert store.list_comments(post["id"]) == []


def test_comment_crud(store):
    post = store.create_post("general", "t", "c", "alice")
    comment = store.add_comment(post["id"], "bob", "hello")
    assert comment is not None
    assert store.get_comment_author(comment["id"]) == "bob"
    updated = store.update_comment(comment["id"], "edited")
    assert updated["content"] == "edited"
    assert store.delete_comment(comment["id"]) is True
    assert store.list_comments(post["id"]) == []


def test_add_comment_to_missing_post_returns_none(store):
    assert store.add_comment(999, "bob", "hi") is None


def test_add_comment_with_empty_content_returns_none(store):
    post = store.create_post("general", "t", "c", "alice")
    assert store.add_comment(post["id"], "bob", "  ") is None


def test_comment_count_reflected_in_get_and_list(store):
    post = store.create_post("general", "t", "c", "alice")
    store.add_comment(post["id"], "bob", "hi")
    store.add_comment(post["id"], "carol", "hey")
    assert store.get_post(post["id"])["comment_count"] == 2
    assert store.list_posts("general")[0]["comment_count"] == 2


def test_visibility_toggle(store):
    post = store.create_post("general", "t", "c", "alice")
    hidden = store.set_post_visibility(post["id"], False)
    assert hidden["visible"] == 0
    shown = store.set_post_visibility(post["id"], True)
    assert shown["visible"] == 1


def test_list_posts_hides_invisible_by_default(store):
    post = store.create_post("general", "t", "c", "alice")
    store.set_post_visibility(post["id"], False)
    assert store.list_posts("general") == []
    assert store.count_posts("general") == 0


def test_list_posts_viewer_still_sees_own_hidden_post(store):
    post = store.create_post("general", "t", "c", "alice")
    store.set_post_visibility(post["id"], False)
    assert len(store.list_posts("general", viewer="alice")) == 1
    assert len(store.list_posts("general", viewer="bob")) == 0


def test_list_posts_include_hidden_for_admin_view(store):
    post = store.create_post("general", "t", "c", "alice")
    store.set_post_visibility(post["id"], False)
    assert len(store.list_posts("general", include_hidden=True)) == 1
