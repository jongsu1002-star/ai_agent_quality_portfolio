from pathlib import Path

from qa_agent.config_loader import Config
from qa_agent.models import GoldenCase
from qa_agent.pipeline import PipelineOrchestrator


def _passing_case() -> GoldenCase:
    return GoldenCase(
        id="TC-001",
        category="COM",
        question="How do I reset my password?",
        golden_answer="Use the password reset link",
        relevant_doc_ids=["DOC-1"],
        existing_answer="Use the password reset link",
        existing_doc_ids=["DOC-1"],
        existing_contexts=["Use the password reset link on the account settings page."],
    )


def test_pipeline_generates_report_for_simple_case(tmp_path):
    orchestrator = PipelineOrchestrator(Config(reports_dir=str(tmp_path)))
    report = orchestrator.run([_passing_case()], techniques=["rag", "llm_quality"], run_id="test-run")

    payload = report.to_dict()
    assert payload["overall_pass_rate"] == 1.0
    assert payload["cases"][0]["overall_pass"] is True
    assert payload["cases"][0]["retrieval"]["passed"] is True
    assert payload["category_stats"]["COM"] == {"total": 1, "passed": 1}
    assert (tmp_path / "run_test-run.json").exists()


def test_pipeline_aggregates_test_type_stats_and_groups_missing_type_as_uncategorized(tmp_path):
    typed_case = GoldenCase(
        id="TC-010",
        category="COM",
        question="q1",
        golden_answer="a1",
        test_type="regression",
        existing_answer="a1",
    )
    untyped_case = GoldenCase(id="TC-011", category="COM", question="q2", golden_answer="a2", existing_answer="a2")

    orchestrator = PipelineOrchestrator(Config(reports_dir=str(tmp_path)))
    report = orchestrator.run([typed_case, untyped_case], techniques=["rag"], run_id="test-run-type")

    payload = report.to_dict()
    assert payload["test_type_stats"]["regression"]["total"] == 1
    assert payload["test_type_stats"]["미분류"]["total"] == 1


def test_pipeline_fails_case_with_missing_retrieval_docs(tmp_path):
    case = GoldenCase(
        id="TC-002",
        category="COM",
        question="How do I reset my password?",
        golden_answer="Use the password reset link",
        relevant_doc_ids=["DOC-1"],
        existing_answer="Use the password reset link",
        # no existing_doc_ids -> retrieval finds nothing relevant
    )

    orchestrator = PipelineOrchestrator(Config(reports_dir=str(tmp_path)))
    report = orchestrator.run([case], techniques=["rag"], run_id="test-run-fail")

    payload = report.to_dict()
    assert payload["overall_pass_rate"] == 0.0
    assert payload["cases"][0]["retrieval"]["passed"] is False


def test_pipeline_dataset_only_missing_existing_answer_errors(tmp_path):
    case = GoldenCase(id="TC-003", category="COM", question="Q", golden_answer="A")

    orchestrator = PipelineOrchestrator(Config(reports_dir=str(tmp_path)))
    report = orchestrator.run([case], techniques=["rag"], run_id="test-run-error")

    payload = report.to_dict()
    assert payload["cases"][0]["overall_pass"] is False
    assert payload["cases"][0]["errors"]


def test_pipeline_functional_technique_runs_connector_contract_probes(tmp_path):
    orchestrator = PipelineOrchestrator(Config(reports_dir=str(tmp_path)))
    report = orchestrator.run([_passing_case()], techniques=["rag", "functional"], run_id="test-run-func")

    assert report.functional_test["total"] == 4
    assert report.functional_test["passed"] == report.functional_test["total"]
