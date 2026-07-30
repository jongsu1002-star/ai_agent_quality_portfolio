"""app/main.py의 /grafana-proxy, /prometheus-proxy IP 허용목록 게이트 - 실제 Grafana/
Prometheus 컨테이너 없이도 requests.request를 모킹해 프록시 로직만 검증한다.

X-Forwarded-For는 TRUST_PROXY_HEADERS가 켜져 있을 때만 신뢰되므로(app/main.py::_client_ip
참고 - 신뢰할 수 있는 리버스 프록시 없이 이 헤더를 믿으면 누구나 위조해 IP 허용목록을
우회할 수 있음), 이 파일의 모든 테스트가 그 플래그를 켜서 실제 운영(ALB/Nginx 뒤) 배포와
동일한 조건으로 검증한다."""

import app.main as main_module
from fastapi.testclient import TestClient

from app.main import app


def _signup(client: TestClient, username: str, password: str):
    return client.post("/signup", json={"username": username, "password": password, "note": "테스트 신청", "contact": "test@example.com"})


class _FakeUpstreamResponse:
    def __init__(self, content: bytes = b"<html>ok</html>", status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": "text/html"}


def test_grafana_proxy_denies_unlisted_ip_when_accounts_exist(monkeypatch):
    monkeypatch.setattr(main_module, "TRUST_PROXY_HEADERS", True)
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    response = admin_client.get("/grafana-proxy/d/some-dash", headers={"X-Forwarded-For": "203.0.113.99"})
    assert response.status_code == 403


def test_prometheus_proxy_denies_unlisted_ip_when_accounts_exist(monkeypatch):
    monkeypatch.setattr(main_module, "TRUST_PROXY_HEADERS", True)
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    response = admin_client.get("/prometheus-proxy/api/v1/query", headers={"X-Forwarded-For": "203.0.113.99"})
    assert response.status_code == 403


def test_grafana_proxy_denies_when_allowlist_is_empty(monkeypatch):
    """계정 모드에서 목록이 비어있으면 기본값은 거부 - 등록 전까지는 아무도 통과 못함."""
    monkeypatch.setattr(main_module, "TRUST_PROXY_HEADERS", True)
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    response = admin_client.get("/grafana-proxy/d/some-dash", headers={"X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 403


def test_grafana_proxy_ignores_forwarded_for_when_proxy_not_trusted():
    """TRUST_PROXY_HEADERS가 꺼져 있으면(기본값) X-Forwarded-For로 허용된 IP인 척해도
    통하지 않아야 한다 - 신뢰할 수 있는 프록시 없이 이 헤더를 믿으면 누구나 위조해 접근
    제어를 우회할 수 있기 때문."""
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")
    admin_client.post("/api/ip-allowlist", json={"network": "203.0.113.5"})

    response = admin_client.get("/grafana-proxy/d/some-dash", headers={"X-Forwarded-For": "203.0.113.5"})
    assert response.status_code == 403


def test_grafana_proxy_allows_listed_ip_and_forwards_with_prefix(monkeypatch):
    monkeypatch.setattr(main_module, "TRUST_PROXY_HEADERS", True)
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")
    admin_client.post("/api/ip-allowlist", json={"network": "203.0.113.5"})

    captured = {}

    def _fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        return _FakeUpstreamResponse()

    monkeypatch.setattr("requests.request", _fake_request)

    response = admin_client.get("/grafana-proxy/d/some-dash", headers={"X-Forwarded-For": "203.0.113.5"})
    assert response.status_code == 200
    assert response.content == b"<html>ok</html>"
    # Grafana는 서브패스로 서빙되므로 /grafana-proxy 접두사를 유지한 채 그대로 전달돼야 함
    assert "/grafana-proxy/d/some-dash" in captured["url"]


def test_prometheus_proxy_allows_listed_ip_and_strips_prefix(monkeypatch):
    monkeypatch.setattr(main_module, "TRUST_PROXY_HEADERS", True)
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")
    admin_client.post("/api/ip-allowlist", json={"network": "203.0.113.0/24"})

    captured = {}

    def _fake_request(method, url, **kwargs):
        captured["url"] = url
        return _FakeUpstreamResponse(content=b'{"status":"success"}')

    monkeypatch.setattr("requests.request", _fake_request)

    response = admin_client.get("/prometheus-proxy/api/v1/query", headers={"X-Forwarded-For": "203.0.113.200"})
    assert response.status_code == 200
    # Prometheus는 서브패스 없이 서빙되므로 /prometheus-proxy 접두사는 벗겨져야 함
    assert captured["url"].endswith("/api/v1/query")
    assert "prometheus-proxy" not in captured["url"]


def test_grafana_proxy_returns_502_on_upstream_connection_error(monkeypatch):
    import requests

    monkeypatch.setattr(main_module, "TRUST_PROXY_HEADERS", True)
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")
    admin_client.post("/api/ip-allowlist", json={"network": "203.0.113.5"})

    def _raise(*args, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr("requests.request", _raise)

    response = admin_client.get("/grafana-proxy/d/some-dash", headers={"X-Forwarded-For": "203.0.113.5"})
    assert response.status_code == 502


def test_grafana_proxy_trusts_last_forwarded_for_entry_not_first(monkeypatch):
    """신뢰할 수 있는 프록시(ALB 등)가 실제로 확인한 접속 IP는 X-Forwarded-For의 맨 뒤에
    덧붙는다 - 맨 앞 값은 클라이언트가 스스로 실어 보낸 것이라 위조 가능하므로, 공격자가
    맨 앞에 허용된 IP를 사칭해도 실제 판단은 맨 뒤 값(프록시가 검증한 값)으로 이뤄져야
    한다."""
    monkeypatch.setattr(main_module, "TRUST_PROXY_HEADERS", True)
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")
    admin_client.post("/api/ip-allowlist", json={"network": "203.0.113.5"})

    # 공격자가 맨 앞에 허용된 IP(203.0.113.5)를 사칭 - 실제 접속 IP(9.9.9.9)는 신뢰할 수
    # 있는 프록시가 맨 뒤에 붙였다고 가정
    response = admin_client.get("/grafana-proxy/d/some-dash", headers={"X-Forwarded-For": "203.0.113.5, 9.9.9.9"})
    assert response.status_code == 403
