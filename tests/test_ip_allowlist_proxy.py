"""app/main.py의 /grafana-proxy, /prometheus-proxy IP 허용목록 게이트 - 실제 Grafana/
Prometheus 컨테이너 없이도 requests.request를 모킹해 프록시 로직만 검증한다."""

from fastapi.testclient import TestClient

from app.main import app


def _signup(client: TestClient, username: str, password: str):
    return client.post("/signup", json={"username": username, "password": password})


class _FakeUpstreamResponse:
    def __init__(self, content: bytes = b"<html>ok</html>", status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": "text/html"}


def test_grafana_proxy_denies_unlisted_ip_when_accounts_exist():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    response = admin_client.get("/grafana-proxy/d/some-dash", headers={"X-Forwarded-For": "203.0.113.99"})
    assert response.status_code == 403


def test_prometheus_proxy_denies_unlisted_ip_when_accounts_exist():
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    response = admin_client.get("/prometheus-proxy/api/v1/query", headers={"X-Forwarded-For": "203.0.113.99"})
    assert response.status_code == 403


def test_grafana_proxy_denies_when_allowlist_is_empty():
    """계정 모드에서 목록이 비어있으면 기본값은 거부 - 등록 전까지는 아무도 통과 못함."""
    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")

    response = admin_client.get("/grafana-proxy/d/some-dash", headers={"X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 403


def test_grafana_proxy_allows_listed_ip_and_forwards_with_prefix(monkeypatch):
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

    admin_client = TestClient(app)
    _signup(admin_client, "alice", "secret123")
    admin_client.post("/api/ip-allowlist", json={"network": "203.0.113.5"})

    def _raise(*args, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr("requests.request", _raise)

    response = admin_client.get("/grafana-proxy/d/some-dash", headers={"X-Forwarded-For": "203.0.113.5"})
    assert response.status_code == 502
