import json
import time

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app, _fetch_public_ip as _real_fetch_public_ip


def _signup(client: TestClient, username: str, password: str):
    return client.post("/signup", json={"username": username, "password": password, "note": "테스트 신청", "contact": "test@example.com"})


def _upload_dataset(client: TestClient, filename: str = "cases.json"):
    payload = json.dumps([{"id": "TC-1", "category": "COM", "question": "q", "golden_answer": "a"}]).encode()
    return client.post("/api/dataset/upload", files={"file": (filename, payload, "application/json")})


def test_shared_ui_stylesheet_is_public_when_login_is_required():
    """로그인 화면 자체가 사용하는 공통 CSS는 인증 전에 접근할 수 있어야 한다."""
    owner = TestClient(app)
    _signup(owner, "alice", "secret12345")

    anonymous = TestClient(app)
    response = anonymous.get("/static/ui-system.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert "--ui-brand" in response.text

    script_response = anonymous.get("/static/ui-theme.js")
    assert script_response.status_code == 200
    assert "javascript" in script_response.headers["content-type"]
    assert "qa-ui-theme" in script_response.text


def test_session_cookie_defaults_to_not_secure_for_local_dev():
    """COOKIE_SECURE 기본값(false)에서는 Secure 속성이 없어야 함 - 평문 HTTP로 돌아가는
    로컬 개발 환경에서 Secure 쿠키를 강제하면 브라우저가 쿠키 저장 자체를 거부해
    로그인이 깨지기 때문(운영에서만 docker-compose.prod.yml이 COOKIE_SECURE=true로 켬)."""
    client = TestClient(app)
    response = _signup(client, "alice", "secret12345")
    set_cookie = response.headers.get("set-cookie", "")
    assert "qa_session=" in set_cookie
    assert "secure" not in set_cookie.lower()
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_session_cookie_is_secure_when_cookie_secure_env_enabled(monkeypatch):
    """COOKIE_SECURE=true(운영 배포)일 때는 로그인/가입 쿠키 모두 Secure 속성이 붙어야 함.

    Secure 쿠키는 표준에 따라 HTTPS 연결에만 다시 실려 나가므로, TestClient도 https://
    base_url로 만들어야 가입 이후의 요청(승인 등)에도 세션 쿠키가 정상적으로 동봉된다
    (기본 http://testserver로는 브라우저와 동일하게 쿠키가 아예 재전송되지 않음 - 이것도
    Secure 플래그가 실제로 지켜지고 있다는 방증)."""
    monkeypatch.setattr(main_module, "COOKIE_SECURE", True)
    client = TestClient(app, base_url="https://testserver")
    response = _signup(client, "alice", "secret12345")
    assert "secure" in response.headers.get("set-cookie", "").lower()

    bob_client = TestClient(app, base_url="https://testserver")
    _signup(bob_client, "bob", "secret12345")
    client.post("/api/users/bob/approve")
    login_response = bob_client.post("/login", json={"username": "bob", "password": "secret12345"})
    assert "secure" in login_response.headers.get("set-cookie", "").lower()


def test_logout_delete_cookie_matches_secure_setting(monkeypatch):
    """삭제(로그아웃) 쿠키도 설정된 쿠키와 동일한 secure/samesite/path 속성을 써야
    브라우저가 정확히 같은 쿠키로 인식해 지운다."""
    monkeypatch.setattr(main_module, "COOKIE_SECURE", True)
    client = TestClient(app)
    _signup(client, "alice", "secret12345")
    logout_response = client.post("/logout")
    delete_cookie_header = logout_response.headers.get("set-cookie", "")
    assert "qa_session=" in delete_cookie_header
    assert "secure" in delete_cookie_header.lower()
    assert "samesite=lax" in delete_cookie_header.lower()


def test_session_survives_simulated_server_restart(monkeypatch):
    """세션이 SQLite에 영속화돼야 컨테이너 재배포(재시작)에도 로그인이 풀리지 않는다 -
    같은 DB 파일을 가리키는 새 SessionStore 인스턴스(=프로세스 재시작을 흉내)로 바꿔도
    기존 쿠키가 여전히 인증돼야 함(예전엔 메모리 dict라 재시작하면 전부 무효화됐음)."""
    from qa_agent.sessions import SessionStore

    client = TestClient(app)
    _signup(client, "alice", "secret12345")
    assert client.get("/api/auth/status").json()["authenticated"] is True

    db_path = main_module.SESSION_STORE._path
    fresh_store_simulating_restart = SessionStore(path=str(db_path))
    monkeypatch.setattr(main_module, "SESSION_STORE", fresh_store_simulating_restart)

    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_session_expires_server_side_after_ttl(monkeypatch):
    """서버 측 만료가 실제로 강제돼야 함 - 쿠키(client-side)의 max_age와 별개로, 서버가
    독립적으로 세션 TTL이 지난 토큰을 거부해야 한다."""
    monkeypatch.setattr(main_module, "SESSION_TTL_SECONDS", -1)  # 이미 만료된 상태로 발급되게 함
    client = TestClient(app)
    _signup(client, "alice", "secret12345")
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_is_https_request_ignores_forwarded_proto_header_by_default():
    """TRUST_PROXY_HEADERS가 꺼져 있으면(기본값) X-Forwarded-Proto를 무조건 신뢰하지
    않아야 함 - 신뢰할 수 있는 리버스 프록시가 없는 한 누구나 이 헤더를 위조해 HTTPS인
    척할 수 있기 때문."""
    from app.main import _is_https_request
    from starlette.requests import Request as StarletteRequest

    scope = {
        "type": "http",
        "scheme": "http",
        "headers": [(b"x-forwarded-proto", b"https")],
        "method": "GET",
        "path": "/",
    }
    request = StarletteRequest(scope)
    assert _is_https_request(request) is False


def test_security_headers_absent_by_default():
    """SECURITY_HEADERS_ENABLED 기본값(false)에서는 기존 응답과 완전히 동일해야 함 -
    로컬 개발 환경에 영향이 없어야 하기 때문."""
    client = TestClient(app)
    response = client.get("/health")
    assert "Content-Security-Policy" not in response.headers
    assert "X-Content-Type-Options" not in response.headers
    assert "Strict-Transport-Security" not in response.headers


def test_security_headers_present_when_enabled(monkeypatch):
    """SECURITY_HEADERS_ENABLED=true(운영)일 때 5개 헤더가 모두 응답에 실려야 하며, CSP는
    기존 인라인 <script>/style="..." 사용(nosniff 대상 소스코드 자체 변경 없음)을 깨지
    않도록 script-src/style-src에 'unsafe-inline'을 포함해야 한다."""
    monkeypatch.setattr(main_module, "SECURITY_HEADERS_ENABLED", True)
    client = TestClient(app)
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Permissions-Policy" in response.headers
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "frame-src 'self'" in csp
    # 평문 HTTP 테스트 요청에는 HSTS를 붙이지 않아야 함(HTTPS일 때만 의미가 있으므로)
    assert "Strict-Transport-Security" not in response.headers


def test_security_headers_include_hsts_when_request_is_https(monkeypatch):
    monkeypatch.setattr(main_module, "SECURITY_HEADERS_ENABLED", True)
    client = TestClient(app, base_url="https://testserver")
    response = client.get("/health")
    assert "max-age=" in response.headers["Strict-Transport-Security"]


def test_is_https_request_honors_forwarded_proto_when_trust_enabled(monkeypatch):
    monkeypatch.setattr(main_module, "TRUST_PROXY_HEADERS", True)
    from app.main import _is_https_request
    from starlette.requests import Request as StarletteRequest

    scope = {
        "type": "http",
        "scheme": "http",
        "headers": [(b"x-forwarded-proto", b"https")],
        "method": "GET",
        "path": "/",
    }
    request = StarletteRequest(scope)
    assert _is_https_request(request) is True


def test_signup_rejects_password_shorter_than_default_minimum():
    """PASSWORD_MIN_LENGTH를 설정하지 않은 로컬 개발 기본값(4자)은 기존 동작과 동일해야 함."""
    client = TestClient(app)
    response = _signup(client, "alice", "abc")
    assert response.status_code == 400
    assert "4자" in response.json()["error"]


def test_signup_rejects_password_shorter_than_configured_minimum(monkeypatch):
    """PASSWORD_MIN_LENGTH=12(운영 기본값)일 때 그보다 짧은 신규 가입은 거부되고, 오류
    메시지도 실제 설정된 길이를 정확히 알려줘야 한다(클라이언트 안내 문구와 일치시키기 위함)."""
    monkeypatch.setattr(main_module, "PASSWORD_MIN_LENGTH", 12)
    client = TestClient(app)
    response = _signup(client, "alice", "short12345")  # 10자 - 12자 미만
    assert response.status_code == 400
    assert "12자" in response.json()["error"]

    ok_response = _signup(client, "alice2", "a" * 12)
    assert ok_response.status_code == 200


def test_signup_requires_setup_code_endpoint_reports_password_min_length(monkeypatch):
    monkeypatch.setattr(main_module, "PASSWORD_MIN_LENGTH", 12)
    client = TestClient(app)
    response = client.get("/api/signup/requires-setup-code")
    assert response.json()["password_min_length"] == 12


def test_raising_password_min_length_does_not_break_existing_users_login(monkeypatch):
    """운영에서 PASSWORD_MIN_LENGTH를 12로 올려도, 이미 짧은 비밀번호로 가입해둔 기존
    사용자의 로그인은 절대 깨지면 안 된다(길이 검사는 가입/생성 시점에만 적용)."""
    client = TestClient(app)
    _signup(client, "alice", "abcd")  # 4자 - 기존 정책 하에서 가입
    monkeypatch.setattr(main_module, "PASSWORD_MIN_LENGTH", 12)
    login_response = client.post("/login", json={"username": "alice", "password": "abcd"})
    assert login_response.status_code == 200


def test_signup_requires_note_identifying_the_applicant():
    """관리자가 승인 여부를 판단할 수 있도록, 가입 신청 시 본인 소개(신청 사유)가 없으면
    거부돼야 한다."""
    client = TestClient(app)
    response = client.post("/signup", json={"username": "alice", "password": "secret123", "contact": "a@example.com"})
    assert response.status_code == 400
    assert "소개" in response.json()["error"]


def test_signup_requires_contact_identifying_the_applicant():
    client = TestClient(app)
    response = client.post("/signup", json={"username": "alice", "password": "secret123", "note": "안녕하세요"})
    assert response.status_code == 400
    assert "연락처" in response.json()["error"]


def test_signup_rejects_note_and_contact_over_max_length():
    client = TestClient(app)
    too_long_note = "a" * (main_module.SIGNUP_NOTE_MAX_LENGTH + 1)
    response = client.post("/signup", json={"username": "alice", "password": "secret123", "note": too_long_note, "contact": "a@example.com"})
    assert response.status_code == 400

    too_long_contact = "a" * (main_module.SIGNUP_CONTACT_MAX_LENGTH + 1)
    response2 = client.post("/signup", json={"username": "alice", "password": "secret123", "note": "안녕하세요", "contact": too_long_contact})
    assert response2.status_code == 400


def test_signup_stores_note_and_contact_and_admin_can_see_it_for_approval():
    """관리자가 "사용자 관리" 탭에서 대기 중인 신청자가 누구인지 확인할 수 있어야 함 -
    /api/users 응답에 note/contact가 그대로 실려야 한다."""
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    bob_client = TestClient(app)
    response = bob_client.post("/signup", json={"username": "bob", "password": "secret456", "note": "영업팀 김철수, QA 결과 확인 목적", "contact": "bob@example.com"})
    assert response.status_code == 200

    users = admin_client.get("/api/users").json()
    bob_entry = next(u for u in users if u["username"] == "bob")
    assert bob_entry["note"] == "영업팀 김철수, QA 결과 확인 목적"
    assert bob_entry["contact"] == "bob@example.com"


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


def test_auth_status_ignores_forwarded_for_header_by_default():
    """TRUST_PROXY_HEADERS가 꺼져 있으면(기본값) X-Forwarded-For를 무조건 신뢰하지 않아야
    함 - 신뢰할 수 있는 리버스 프록시가 없는 한 누구나 이 헤더를 위조해 IP 허용목록을
    우회할 수 있기 때문(_is_https_request와 동일한 원칙)."""
    client = TestClient(app)
    status = client.get("/api/auth/status", headers={"X-Forwarded-For": "203.0.113.42, 10.0.0.1"}).json()
    assert status["client_ip"] != "203.0.113.42"
    assert status["client_ip"] != "10.0.0.1"

    status_no_header = client.get("/api/auth/status").json()
    assert status_no_header["client_ip"]  # 헤더 없으면 request.client.host로 폴백, 빈 값은 아님


def test_auth_status_reports_client_ip_for_ip_allowlist_registration_when_proxy_trusted(monkeypatch):
    """화면 상단(로그아웃 버튼 왼쪽)에 보여주는 "내 접속 IP"가 실제 IP 허용목록 판정에
    쓰이는 값과 정확히 일치해야, 사용자가 그 값을 그대로 등록했을 때 실제로 통과된다.
    TRUST_PROXY_HEADERS가 켜진 경우에만 X-Forwarded-For를 신뢰하며, 그 중에서도 신뢰할 수
    있는 프록시(ALB/Nginx)가 실제로 덧붙인 맨 뒤 값을 쓴다(맨 앞 값은 클라이언트가 스스로
    위조할 수 있음)."""
    monkeypatch.setattr(main_module, "TRUST_PROXY_HEADERS", True)
    client = TestClient(app)
    status = client.get("/api/auth/status", headers={"X-Forwarded-For": "203.0.113.42, 10.0.0.1"}).json()
    assert status["client_ip"] == "10.0.0.1"


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


def test_admin_can_disable_and_re_enable_a_user():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    admin_client.post("/api/users/bob/approve")

    disable_response = admin_client.post("/api/users/bob/disable")
    assert disable_response.status_code == 200

    login_response = bob_client.post("/login", json={"username": "bob", "password": "secret456"})
    assert login_response.status_code == 403
    assert "중지" in login_response.json()["error"]

    enable_response = admin_client.post("/api/users/bob/enable")
    assert enable_response.status_code == 200
    login_response2 = bob_client.post("/login", json={"username": "bob", "password": "secret456"})
    assert login_response2.status_code == 200


def test_disabling_a_user_immediately_kills_their_active_session():
    """이미 로그인된 세션이 있는 상태에서 관리자가 계정을 중지하면, 그 세션으로도 더 이상
    인증된 요청을 할 수 없어야 함(로그아웃 처리 없이 계속 활동하는 구멍을 막기 위함)."""
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    admin_client.post("/api/users/bob/approve")
    bob_client.post("/login", json={"username": "bob", "password": "secret456"})
    assert bob_client.get("/api/auth/status").json()["authenticated"] is True

    admin_client.post("/api/users/bob/disable")

    status_after = bob_client.get("/api/auth/status").json()
    assert status_after["authenticated"] is False


def test_disable_endpoint_is_admin_only():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    bob_client = TestClient(app)
    _signup(bob_client, "bob", "secret456")
    admin_client.post("/api/users/bob/approve")
    bob_client.post("/login", json={"username": "bob", "password": "secret456"})

    assert bob_client.post("/api/users/alice/disable").status_code == 403


def test_last_admin_cannot_be_disabled_via_api():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    response = admin_client.post("/api/users/alice/disable")
    assert response.status_code == 400


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
    bob_client.post("/login", json={"username": "bob", "password": "secret456"})

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
    bob_client.post("/login", json={"username": "bob", "password": "secret456"})

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
