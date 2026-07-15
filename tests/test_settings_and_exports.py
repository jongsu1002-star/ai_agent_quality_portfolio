import time

from fastapi.testclient import TestClient

from app.main import app


def _wait_for_run(client, run_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f'/api/run/{run_id}/status').json()
        if status['status'] in ('done', 'error'):
            return status
        time.sleep(0.1)
    raise AssertionError('run did not finish in time')


def test_load_settings_and_export_downloads():
    client = TestClient(app)
    client.post('/api/settings', json={'jira_base_url': 'https://example.atlassian.net'})
    settings_response = client.get('/api/settings')
    assert settings_response.status_code == 200

    # /api/reports/latest는 실제 실행 결과가 있어야 200을 반환한다(격리된 테스트 환경에는
    # 실행 이력이 비어 있으므로 먼저 파이프라인을 한 번 돌려 최신 결과를 만들어둬야 함).
    run_response = client.post('/api/run', json={'techniques': ['rag']})
    _wait_for_run(client, run_response.json()['run_id'])

    csv_response = client.get('/api/reports/latest?format=csv')
    assert csv_response.status_code == 200

    md_response = client.get('/api/reports/latest?format=md')
    assert md_response.status_code == 200

    json_response = client.get('/api/reports/latest?format=json')
    assert json_response.status_code == 200
