from pathlib import Path

from app.main import app
from fastapi.testclient import TestClient


def test_settings_persistence_and_export_routes(tmp_path):
    client = TestClient(app)
    response = client.post('/api/settings', json={'jira_base_url': 'https://example.atlassian.net', 'slack_webhook_url': 'https://example.com'})
    assert response.status_code == 200

    settings_response = client.get('/api/settings')
    assert settings_response.status_code == 200

    export_response = client.get('/api/reports/latest?format=csv')
    assert export_response.status_code == 200
