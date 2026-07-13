import os

from fastapi.testclient import TestClient

from app.main import app


def test_env_settings_are_loaded_and_downloads_are_named():
    os.environ['SLACK_WEBHOOK_URL'] = 'https://example.com/slack'
    client = TestClient(app)

    settings_response = client.get('/api/settings')
    assert settings_response.status_code == 200
    assert settings_response.json().get('slack_webhook_url') == 'https://example.com/slack'

    csv_response = client.get('/api/reports/latest?format=csv')
    assert csv_response.status_code == 200
    assert 'attachment; filename=latest_report.csv' in csv_response.headers.get('content-disposition', '')
