import json
import time

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app, _fetch_public_ip as _real_fetch_public_ip


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
    assert status["enabled"] is False
    assert status["authenticated"] is True
    assert status["username"] is None
    assert status["is_admin"] is False
    assert "client_ip" in status


def test_auth_status_includes_public_ip_field():
    """공인 IP 조회는 conftest.py의 autouse 픽스처가 고정값으로 대체하므로(실제 네트워크
    호출 방지), 응답에 그 필드가 그대로 실려 나가는지만 확인."""
    client = TestClient(app)
    status = client.get("/api/auth/status").json()
    assert status["public_ip"] == "203.0.113.1"


def test_fetch_public_ip_caches_result_and_survives_failure(monkeypatch):
    """_fetch_public_ip() 자체의 캐싱/실패 처리 로직 - conftest의 autouse 픽스처가 이
    함수 자체를 통째로 대체해두므로, 모듈 임포트 시점에 잡아둔 원본 함수 참조로 직접
    테스트한다."""
    monkeypatch.setattr(main_module, "_PUBLIC_IP_CACHE", {"value": None, "fetched_at": 0.0})

    calls = {"n": 0}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ip": "198.51.100.7"}

    def _fake_get(url, timeout):
        calls["n"] += 1
        return _FakeResponse()

    monkeypatch.setattr("requests.get", _fake_get)

    assert _real_fetch_public_ip() == "198.51.100.7"
    assert _real_fetch_public_ip() == "198.51.100.7"  # 캐시 TTL 안이라 재호출 없음
    assert calls["n"] == 1


def test_fetch_public_ip_returns_none_on_failure_without_raising(monkeypatch):
    monkeypatch.setattr(main_module, "_PUBLIC_IP_CACHE", {"value": None, "fetched_at": 0.0})

    def _fake_get(url, timeout):
        raise ConnectionError("no network")

    monkeypatch.setattr("requests.get", _fake_get)

    assert _real_fetch_public_ip() is None


def test_fetch_public_ip_refetches_after_ttl_expires(monkeypatch):
    monkeypatch.setattr(main_module, "_PUBLIC_IP_CACHE", {"value": "old-ip", "fetched_at": time.time() - 10_000})

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ip": "203.0.113.99"}

    monkeypatch.setattr("requests.get", lambda url, timeout: _FakeResponse())

    assert _real_fetch_public_ip() == "203.0.113.99"


def test_auth_status_reports_client_ip_for_ip_allowlist_registration():
    """화면 상단(로그아웃 버튼 왼쪽)에 보여주는 "내 접속 IP"가 실제 IP 허용목록 판정에
    쓰이는 값(X-Forwarded-For 우선)과 정확히 일치해야, 사용자가 그 값을 그대로 등록했을 때
    실제로 통과된다."""
    client = TestClient(app)
    status = client.get("/api/auth/status", headers={"X-Forwarded-For": "203.0.113.42, 10.0.0.1"}).json()
    assert status["client_ip"] == "203.0.113.42"

    status_no_header = client.get("/api/auth/status").json()
    assert status_no_header["client_ip"]  # 헤더 없으면 request.client.host로 폴백, 빈 값은 아님


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
    assert status["enabled"] is True
    assert status["authenticated"] is False
    assert status["username"] is None
    assert status["is_admin"] is False
    assert "client_ip" in status


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


def test_ip_allowlist_crud_is_admin_only():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    admin_client.post("/api/users/bob/approve")
    bob_client.post("\login", json={"username": "bob", "password": "secret456"})

    assert bob_client.get("/api/ip-allowlist").status_code == 403
    assert bob_client.post("/api/ip-allowlist", json={"network": "203.0.113.5"}).status_code == 403

    add_response = admin_client.post("/api/ip-allowlist", json={"network": "203.0.113.5", "label": "사무실"})
    assert add_response.status_code == 200
    entry_id = add_response.json()["id"]

    assert bob_client.delete(f"/api/ip-allowlist/{entry_id}").status_code == 403
    assert bob_client.put(f"/api/ip-allowlist/{entry_id}", json={"label": "x"}).status_code == 403

    list_response = admin_client.get("/api/ip-allowlist")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    assert admin_client.put(f"/api/ip-allowlist/{entry_id}", json={"label": "본사"}).status_code == 200
    assert admin_client.delete(f"/api/ip-allowlist/{entry_id}").status_code == 200
    assert admin_client.get("/api/ip-allowlist").json() == []


def test_ip_allowlist_rejects_invalid_network():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    response = admin_client.post("/api/ip-allowlist", json={"network": "not-an-ip"})
    assert response.status_code == 400


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
