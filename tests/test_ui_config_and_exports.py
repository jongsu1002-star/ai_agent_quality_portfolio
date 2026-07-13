from fastapi.testclient import TestClient

from app.main import app


def test_template_download_and_report_routes():
    client = TestClient(app)
    template_response = client.get('/api/dataset/template')
    assert template_response.status_code == 200

    run_response = client.post('/api/run', json={'techniques': ['rag']})
    assert run_response.status_code == 200

    export_response = client.get('/api/reports/latest')
    assert export_response.status_code == 200
