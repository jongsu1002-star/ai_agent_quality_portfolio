from fastapi.testclient import TestClient

from app.main import app
from qa_agent.models import CaseResult, RunReport
from qa_agent.reporter import write_reports


def test_report_writer_creates_csv_and_markdown(tmp_path):
    report = RunReport(run_id="demo", cases=[CaseResult(case_id="TC-001", overall_pass=True)])
    write_reports(report, reports_dir=str(tmp_path))

    assert (tmp_path / "run_demo.json").exists()
    assert (tmp_path / "run_demo.csv").exists()
    assert (tmp_path / "final_quality_report.md").exists()


def test_app_serves_dashboard_page():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "AI Agent 품질관리·운영 모니터링 플랫폼" in response.text
