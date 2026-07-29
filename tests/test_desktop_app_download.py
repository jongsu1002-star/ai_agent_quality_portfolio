from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


def test_desktop_app_info_reports_unavailable_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "DESKTOP_APP_EXE_PATH", tmp_path / "missing.exe")
    client = TestClient(app)
    response = client.get("/api/desktop-app/info")
    assert response.json() == {"available": False}


def test_desktop_app_info_reports_size_and_modified_date(tmp_path, monkeypatch):
    exe_path = tmp_path / "AI_Agent_품질관리.exe"
    exe_path.write_bytes(b"x" * 2048)
    monkeypatch.setattr(main_module, "DESKTOP_APP_EXE_PATH", exe_path)
    client = TestClient(app)
    response = client.get("/api/desktop-app/info")
    data = response.json()
    assert data["available"] is True
    assert data["filename"] == "AI_Agent_품질관리.exe"
    assert data["size_bytes"] == 2048
    assert data["size_display"] == "2.0KB"
    assert "modified_at" in data


def test_desktop_app_download_returns_file_content(tmp_path, monkeypatch):
    exe_path = tmp_path / "AI_Agent_품질관리.exe"
    exe_path.write_bytes(b"fake-exe-bytes")
    monkeypatch.setattr(main_module, "DESKTOP_APP_EXE_PATH", exe_path)
    client = TestClient(app)
    response = client.get("/api/desktop-app/download")
    assert response.status_code == 200
    assert response.content == b"fake-exe-bytes"
    assert "attachment" in response.headers.get("content-disposition", "").lower()


def test_desktop_app_download_returns_404_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "DESKTOP_APP_EXE_PATH", tmp_path / "missing.exe")
    client = TestClient(app)
    response = client.get("/api/desktop-app/download")
    assert response.status_code == 404


def test_desktop_app_download_requires_login_when_accounts_exist():
    client = TestClient(app)
    client.post("/signup", json={"username": "alice_dl", "password": "secret123", "note": "테스트 신청", "contact": "test@example.com"})
    anon_client = TestClient(app)
    response = anon_client.get("/api/desktop-app/download")
    assert response.status_code == 401
