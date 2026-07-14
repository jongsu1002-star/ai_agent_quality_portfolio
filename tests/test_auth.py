from fastapi.testclient import TestClient

from app.main import app


def test_auth_disabled_by_default_allows_everything(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/api/dataset/current").status_code == 200

    status = client.get("/api/auth/status").json()
    assert status == {"enabled": False, "authenticated": True}


def test_unauthenticated_page_request_redirects_to_login(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret123")
    client = TestClient(app, follow_redirects=False)

    response = client.get("/")
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("/login")


def test_unauthenticated_api_request_gets_401_json(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret123")
    client = TestClient(app)

    response = client.get("/api/dataset/current")
    assert response.status_code == 401
    assert "error" in response.json()


def test_exempt_paths_stay_open_without_login(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret123")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200
    # 로그인 여부를 물어보는 상태 조회 자체는 로그인 없이도 호출 가능해야 함(그래야
    # 로그인 페이지 등에서 "지금 로그인 상태인지"를 안전하게 먼저 확인할 수 있음)
    status = client.get("/api/auth/status").json()
    assert status == {"enabled": True, "authenticated": False}


def test_wrong_password_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret123")
    client = TestClient(app)

    response = client.post("/login", json={"password": "nope"})
    assert response.status_code == 401
    assert "qa_session" not in response.cookies


def test_correct_password_grants_a_session_that_unlocks_the_api(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret123")
    client = TestClient(app)

    login_response = client.post("/login", json={"password": "secret123"})
    assert login_response.status_code == 200
    assert "qa_session" in login_response.cookies

    # TestClient는 같은 인스턴스 안에서 쿠키를 유지하므로 이후 요청은 로그인된 상태로 감
    assert client.get("/").status_code == 200
    assert client.get("/api/dataset/current").status_code == 200

    status = client.get("/api/auth/status").json()
    assert status == {"enabled": True, "authenticated": True}


def test_logout_invalidates_the_session(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret123")
    client = TestClient(app)

    client.post("/login", json={"password": "secret123"})
    assert client.get("/api/dataset/current").status_code == 200

    logout_response = client.post("/logout")
    assert logout_response.status_code == 200

    assert client.get("/api/dataset/current").status_code == 401


def test_metrics_addon_path_stays_open_for_prometheus_scraping(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret123")
    client = TestClient(app)

    response = client.get("/metrics-addon")
    assert response.status_code != 401
