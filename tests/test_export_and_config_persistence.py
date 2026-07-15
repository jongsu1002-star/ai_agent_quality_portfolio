import time
from pathlib import Path

from app.main import app
from fastapi.testclient import TestClient


def _wait_for_run(client, run_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f'/api/run/{run_id}/status').json()
        if status['status'] in ('done', 'error'):
            return status
        time.sleep(0.1)
    raise AssertionError('run did not finish in time')


def test_settings_persistence_and_export_routes(tmp_path):
    client = TestClient(app)
    response = client.post('/api/settings', json={'jira_base_url': 'https://example.atlassian.net', 'slack_webhook_url': 'https://example.com'})
    assert response.status_code == 200

    settings_response = client.get('/api/settings')
    assert settings_response.status_code == 200

    # 격리된 테스트 환경에는 실행 이력이 비어 있으므로 export 라우트를 확인하기 전에
    # 파이프라인을 한 번 실행해 최신 결과를 만들어둬야 한다.
    run_response = client.post('/api/run', json={'techniques': ['rag']})
    _wait_for_run(client, run_response.json()['run_id'])

    export_response = client.get('/api/reports/latest?format=csv')
    assert export_response.status_code == 200
