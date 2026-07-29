import json

from fastapi.testclient import TestClient

from app.main import app


def _signup(client: TestClient, username: str, password: str):
    return client.post("/signup", json={"username": username, "password": password, "note": "테스트 신청", "contact": "test@example.com"})


def _login(client: TestClient, username: str, password: str):
    return client.post("/login", json={"username": username, "password": password})


def test_shared_mode_allows_full_crud_without_login():
    client = TestClient(app)
    create = client.post("/api/board/posts", json={"board_type": "general", "title": "hello", "content": "world"})
    assert create.status_code == 200
    post = create.json()
    assert post["author"] == "shared"

    listing = client.get("/api/board/posts", params={"board_type": "general"})
    assert listing.json()["total"] == 1

    update = client.put(f"/api/board/posts/{post['id']}", json={"title": "updated", "content": "c2"})
    assert update.status_code == 200
    assert update.json()["title"] == "updated"

    delete = client.delete(f"/api/board/posts/{post['id']}")
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True


def test_create_post_rejects_unknown_board_type():
    client = TestClient(app)
    response = client.post("/api/board/posts", json={"board_type": "spam", "title": "t", "content": "c"})
    assert response.status_code == 400


def test_get_missing_post_returns_404():
    client = TestClient(app)
    assert client.get("/api/board/posts/999").status_code == 404


def _setup_two_users():
    """alice: 최초 가입자(관리자), bob: 두 번째 가입자(승인 후 일반 사용자)."""
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    admin_client.post("/api/users/bob/approve")
    _login(bob_client, "bob", "secret456")
    return admin_client, bob_client


def test_non_author_cannot_edit_or_delete_others_post():
    admin_client, bob_client = _setup_two_users()

    carol_client = TestClient(app)
    _signup(carol_client, "carol", "secret789")
    admin_client.post("/api/users/carol/approve")
    _login(carol_client, "carol", "secret789")

    create = bob_client.post("/api/board/posts", json={"board_type": "voc", "title": "불만", "content": "느려요"})
    post_id = create.json()["id"]
    assert create.json()["author"] == "bob"

    # carol(작성자도 관리자도 아님)은 수정 불가
    edit = carol_client.put(f"/api/board/posts/{post_id}", json={"title": "x", "content": "y"})
    assert edit.status_code == 403

    # bob(작성자, 관리자 아님)은 삭제 불가 - 삭제는 관리자만
    delete_by_author = bob_client.delete(f"/api/board/posts/{post_id}")
    assert delete_by_author.status_code == 403

    # bob(작성자)은 수정은 가능
    edit_by_author = bob_client.put(f"/api/board/posts/{post_id}", json={"title": "수정됨", "content": "y"})
    assert edit_by_author.status_code == 200

    # alice(관리자)는 삭제 가능
    delete_by_admin = admin_client.delete(f"/api/board/posts/{post_id}")
    assert delete_by_admin.status_code == 200


def test_visibility_toggle_by_author_hides_post_from_others():
    admin_client, bob_client = _setup_two_users()
    carol_client = TestClient(app)
    _signup(carol_client, "carol", "secret789")
    admin_client.post("/api/users/carol/approve")
    _login(carol_client, "carol", "secret789")

    create = bob_client.post("/api/board/posts", json={"board_type": "general", "title": "t", "content": "c"})
    post_id = create.json()["id"]

    hide = bob_client.post(f"/api/board/posts/{post_id}/visibility", json={"visible": False})
    assert hide.status_code == 200
    assert hide.json()["visible"] == 0

    # carol(작성자/관리자 아님) - 목록/상세 모두 안 보임
    assert carol_client.get("/api/board/posts", params={"board_type": "general"}).json()["total"] == 0
    assert carol_client.get(f"/api/board/posts/{post_id}").status_code == 404

    # bob(작성자)은 여전히 자기 글을 볼 수 있음
    assert bob_client.get(f"/api/board/posts/{post_id}").status_code == 200

    # alice(관리자)도 볼 수 있음
    assert admin_client.get(f"/api/board/posts/{post_id}").status_code == 200
    assert admin_client.get("/api/board/posts", params={"board_type": "general"}).json()["total"] == 1


def test_comment_crud_and_admin_only_delete():
    admin_client, bob_client = _setup_two_users()
    create = bob_client.post("/api/board/posts", json={"board_type": "general", "title": "t", "content": "c"})
    post_id = create.json()["id"]

    comment = bob_client.post(f"/api/board/posts/{post_id}/comments", json={"content": "댓글입니다"})
    assert comment.status_code == 200
    comment_id = comment.json()["id"]

    detail = bob_client.get(f"/api/board/posts/{post_id}")
    assert len(detail.json()["comments"]) == 1

    # 댓글 삭제도 게시글과 동일하게 관리자만
    delete_by_author = bob_client.delete(f"/api/board/comments/{comment_id}")
    assert delete_by_author.status_code == 403

    delete_by_admin = admin_client.delete(f"/api/board/comments/{comment_id}")
    assert delete_by_admin.status_code == 200


def test_comment_on_missing_post_returns_404():
    client = TestClient(app)
    response = client.post("/api/board/posts/999/comments", json={"content": "hi"})
    assert response.status_code == 404


def test_cannot_comment_on_hidden_post_of_another_user():
    """P0: 댓글 작성이 게시글 열람 권한을 우회하던 결함 - 비노출 글의 id를 알아도 작성자/관리자가
    아니면 댓글을 달 수 없어야 함(그 전엔 "존재 여부"만 확인해서 우회 가능했음)."""
    admin_client, bob_client = _setup_two_users()
    carol_client = TestClient(app)
    _signup(carol_client, "carol", "secret789")
    admin_client.post("/api/users/carol/approve")
    _login(carol_client, "carol", "secret789")

    create = bob_client.post("/api/board/posts", json={"board_type": "general", "title": "t", "content": "c"})
    post_id = create.json()["id"]
    bob_client.post(f"/api/board/posts/{post_id}/visibility", json={"visible": False})

    # carol(작성자/관리자 아님)은 비노출 글에 댓글을 달 수 없음 - 존재 자체를 알 수 없어야 함
    comment = carol_client.post(f"/api/board/posts/{post_id}/comments", json={"content": "몰래 댓글"})
    assert comment.status_code == 404

    # bob(작성자)은 자기 비노출 글에 여전히 댓글을 달 수 있음
    own_comment = bob_client.post(f"/api/board/posts/{post_id}/comments", json={"content": "내 글 댓글"})
    assert own_comment.status_code == 200

    # alice(관리자)도 댓글 가능
    admin_comment = admin_client.post(f"/api/board/posts/{post_id}/comments", json={"content": "관리자 댓글"})
    assert admin_comment.status_code == 200


def test_bulk_delete_requires_admin():
    admin_client, bob_client = _setup_two_users()
    create = bob_client.post("/api/board/posts", json={"board_type": "general", "title": "t", "content": "c"})
    post_id = create.json()["id"]

    response = bob_client.post("/api/board/posts/bulk-delete", json={"ids": [post_id]})
    assert response.status_code == 403
    assert admin_client.get(f"/api/board/posts/{post_id}").status_code == 200  # 안 지워짐


def test_bulk_delete_removes_multiple_posts_and_reports_not_found():
    admin_client, bob_client = _setup_two_users()
    id1 = bob_client.post("/api/board/posts", json={"board_type": "voc", "title": "a", "content": "c"}).json()["id"]
    id2 = bob_client.post("/api/board/posts", json={"board_type": "voc", "title": "b", "content": "c"}).json()["id"]

    response = admin_client.post("/api/board/posts/bulk-delete", json={"ids": [id1, id2, 999999]})
    assert response.status_code == 200
    body = response.json()
    assert sorted(body["deleted"]) == sorted([id1, id2])
    assert body["not_found"] == [999999]

    assert admin_client.get(f"/api/board/posts/{id1}").status_code == 404
    assert admin_client.get(f"/api/board/posts/{id2}").status_code == 404


def test_bulk_delete_rejects_empty_ids():
    admin_client, _bob_client = _setup_two_users()
    response = admin_client.post("/api/board/posts/bulk-delete", json={"ids": []})
    assert response.status_code == 400
