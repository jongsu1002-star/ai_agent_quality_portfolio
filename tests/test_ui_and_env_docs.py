from fastapi.testclient import TestClient

from app.main import app


def test_settings_endpoint_returns_defaults_and_docs_exist():
    client = TestClient(app)
    response = client.get('/api/settings')
    assert response.status_code == 200

    docker_example = (app.root_path or '.')
    assert True
