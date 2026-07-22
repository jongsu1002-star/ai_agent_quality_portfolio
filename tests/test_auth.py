import json

from fastapi.testclient import TestClient

from app.main import app


def _signup(client: TestClient, username: str, password: str):
    return client.post("/signup", json={"username": username, "password": password})


def _upload_dataset(client: TestClient, filename: str = "cases.json"):
    payload = json.dumps([{"id": "TC-1", "category": "COM", "question": "q", "golden_answer": "a"}]).encode()
    return client.post("/api/dataset/upload", files={"file": (filename, payload, "application/json")})


def test_auth_disabled_by_default_allows_everything():
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/api/dataset/current").status_code == 200

    status = client.get("/api/auth/status").json()
    assert status == {"enabled": False, "authenticated": True, "username": None, "is_admin": False}


def test_first_signup_is_auto_approved_admin_and_logs_in_immediately():
    client = TestClient(app)
    response = _signup(client, "alice", "secret123")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert "qa_session" in response.cookies

    assert client.get("/").status_code == 200
    status = client.get("/api/auth/status").json()
    assert status["authenticated"] is True
    assert status["username"] == "alice"
    assert status["is_admin"] is True


def test_second_signup_stays_pending_and_cannot_log_in():
    _signup(TestClient(app), "alice", "secret123")

    bob_client = TestClient(app)
    response = _signup(bob_client, "bob", "secret456")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert "qa_session" not in response.cookies

    login_response = bob_client.post("/login", json={"username": "bob", "password": "secret456"})
    assert login_response.status_code == 403


def test_duplicate_signup_username_is_rejected():
    _signup(TestClient(app), "alice", "secret123")
    response = _signup(TestClient(app), "alice", "different-password")
    assert response.status_code == 409


def test_unauthenticated_page_request_redirects_to_login():
    _signup(TestClient(app), "alice", "secret123")

    client = TestClient(app, follow_redirects=False)
    response = client.get("/")
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("/login")


def test_unauthenticated_api_request_gets_401_json():
    _signup(TestClient(app), "alice", "secret123")

    client = TestClient(app)
    response = client.get("/api/dataset/current")
    assert response.status_code == 401
    assert "error" in response.json()


def test_exempt_paths_stay_open_without_login():
    _signup(TestClient(app), "alice", "secret123")

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/signup").status_code == 200
    status = client.get("/api/auth/status").json()
    assert status == {"enabled": True, "authenticated": False, "username": None, "is_admin": False}


def test_wrong_password_is_rejected():
    _signup(TestClient(app), "alice", "secret123")

    client = TestClient(app)
    response = client.post("/login", json={"username": "alice", "password": "nope"})
    assert response.status_code == 401
    assert "qa_session" not in response.cookies


def test_login_with_nonexistent_username_is_rejected():
    _signup(TestClient(app), "alice", "secret123")

    client = TestClient(app)
    response = client.post("/login", json={"username": "ghost", "password": "whatever"})
    assert response.status_code == 401


def test_correct_password_grants_a_session_that_unlocks_the_api():
    _signup(TestClient(app), "alice", "secret123")

    client = TestClient(app)
    login_response = client.post("/login", json={"username": "alice", "password": "secret123"})
    assert login_response.status_code == 200
    assert "qa_session" in login_response.cookies

    assert client.get("/").status_code == 200
    assert client.get("/api/dataset/current").status_code == 200

    status = client.get("/api/auth/status").json()
    assert status["authenticated"] is True
    assert status["username"] == "alice"


def test_logout_invalidates_the_session():
    _signup(TestClient(app), "alice", "secret123")

    client = TestClient(app)
    client.post("/login", json={"username": "alice", "password": "secret123"})
    assert client.get("/api/dataset/current").status_code == 200

    logout_response = client.post("/logout")
    assert logout_response.status_code == 200
    assert client.get("/api/dataset/current").status_code == 401


def test_metrics_addon_path_stays_open_for_prometheus_scraping():
    _signup(TestClient(app), "alice", "secret123")

    client = TestClient(app)
    response = client.get("/metrics-addon")
    assert response.status_code != 401


def test_non_admin_cannot_access_user_management():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    admin_client.post("/api/users/bob/approve")
    bob_client.post("/login", json={"username": "bob", "password": "secret456"})

    assert bob_client.get("/api/users").status_code == 403


def test_error_log_is_admin_only():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    admin_client.post("/api/users/bob/approve")
    bob_client.post("\login", json={"username": "bob", "password": "secret456"})

    assert bob_client.get("/api/error-log").status_code == 403
    admin_response = admin_client.get("/api/error-log")
    assert admin_response.status_code == 200
    assert "entries" in admin_response.json()


def test_admin_can_approve_a_pending_signup():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    pending_client = TestClient(app)
    _signup(pending_client, "bob", "secret456")

    users = {u["username"]: u for u in admin_client.get("/api/users").json()}
    assert users["bob"]["status"] == "pending"

    assert admin_client.post("/api/users/bob/approve").status_code == 200
    assert pending_client.post("/login", json={"username": "bob", "password": "secret456"}).status_code == 200


def test_admin_can_reject_a_pending_signup():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    pending_client = TestClient(app)
    _signup(pending_client, "bob", "secret456")

    assert admin_client.post("/api/users/bob/reject").status_code == 200
    # 거부되면 계정 자체가 지워지므로 로그인은 "존재하지 않는 아이디"로 실패
    assert pending_client.post("/login", json={"username": "bob", "password": "secret456"}).status_code == 401


def test_admin_can_transfer_admin_role_to_another_user():
    alice_client = TestClient(app)
    _signup(alice_client, "alice", "secret123")

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    alice_client.post("/api/users/bob/approve")
    bob_client.post("/login", json={"username": "bob", "password": "secret456"})

    response = alice_client.post("/api/users/bob/role", json={"role": "admin"})
    assert response.status_code == 200

    # bob은 이제 관리자이므로 사용자 관리 API에 접근할 수 있어야 함
    assert bob_client.get("/api/users").status_code == 200


def test_last_admin_cannot_demote_self_via_api():
    alice_client = TestClient(app)
    _signup(alice_client, "alice", "secret123")

    response = alice_client.post("/api/users/alice/role", json={"role": "user"})
    assert response.status_code == 400


def test_dataset_upload_is_isolated_per_user():
    alice_client = TestClient(app)
    _signup(alice_client, "alice", "secret123")

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    alice_client.post("/api/users/bob/approve")
    bob_client.post("/login", json={"username": "bob", "password": "secret456"})

    upload_response = _upload_dataset(alice_client)
    assert upload_response.status_code == 200

    alice_current = alice_client.get("/api/dataset/current").json()
    assert alice_current["case_count"] == 1

    bob_current = bob_client.get("/api/dataset/current").json()
    assert bob_current["path"] is None  # bob은 alice의 데이터셋을 볼 수 없음
    assert bob_client.get("/api/dataset/history").json() == []


def test_dataset_delete_by_non_owner_requires_admin():
    alice_client = TestClient(app)
    _signup(alice_client, "alice", "secret123")  # admin (first signup)

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    alice_client.post("/api/users/bob/approve")
    bob_client.post("/login", json={"username": "bob", "password": "secret456"})

    _upload_dataset(alice_client)
    alice_path = alice_client.get("/api/dataset/current").json()["path"]

    forbidden = bob_client.post("/api/dataset/delete", json={"path": alice_path, "owner": "alice"})
    assert forbidden.status_code == 403

    ok = alice_client.post("/api/dataset/delete", json={"path": alice_path, "owner": "alice"})
    assert ok.status_code == 200
    assert alice_path in ok.json()["deleted"]
