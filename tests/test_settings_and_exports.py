from fastapi.testclient import TestClient

from app.main import app


def test_load_settings_and_export_downloads():
    client = TestClient(app)
    client.post('/api/settings', json={'jira_base_url': 'https://example.atlassian.net'})
    settings_response = client.get('/api/settings')
    assert settings_response.status_code == 200

    csv_response = client.get('/api/reports/latest?format=csv')
    assert csv_response.status_code == 200

    md_response = client.get('/api/reports/latest?format=md')
    assert md_response.status_code == 200

    json_response = client.get('/api/reports/latest?format=json')
    assert json_response.status_code == 200
