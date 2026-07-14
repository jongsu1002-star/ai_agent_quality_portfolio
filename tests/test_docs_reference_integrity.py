"""Guards the hand-maintained docs (사용자_매뉴얼/설계서/프로세스_명세서) against drift.

These three documents are not auto-generated (unlike 테스트_결과.md and 결함보고서.md),
so nothing stops them from silently going stale as the code changes. This test
checks that the files/classes/functions/API paths they call out by name still
exist; a failure here means someone renamed/removed something without updating
the corresponding doc.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_CODE_SPAN = re.compile(r"`([^`]*)`")


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def _unescaped_pipe_code_spans(line: str) -> list[str]:
    """Code spans on a table row containing a bare "|" -- breaks table parsing
    in standard tools (VS Code/GitHub markdownlint counts it as an extra cell),
    not just a hand-rolled renderer. Escaped ("\\|") is fine; bare is not.
    """
    return [span.group(1) for span in _CODE_SPAN.finditer(line) if re.search(r"(?<!\\)\|", span.group(1))]


DESIGN_SPEC_REFERENCES = [
    ("qa_agent/models.py", "class GoldenCase"),
    ("qa_agent/models.py", "class ChatbotResponse"),
    ("qa_agent/models.py", "class RunReport"),
    ("qa_agent/models.py", "class ValidationResult"),
    ("qa_agent/config_loader.py", "class ConnectorConfig"),
    ("qa_agent/config_loader.py", "class Thresholds"),
    ("qa_agent/config_loader.py", "def load_config"),
    ("qa_agent/evaluators.py", "class RetrievalEvaluator"),
    ("qa_agent/evaluators.py", "class GroundednessEvaluator"),
    ("qa_agent/evaluators.py", "class ContextRelevanceEvaluator"),
    ("qa_agent/evaluators.py", "class LLMJudgeEvaluator"),
    ("qa_agent/evaluators.py", "class RubricEvaluator"),
    ("qa_agent/evaluators.py", "class RegressionEvaluator"),
    ("qa_agent/evaluators.py", "class ToxicityEvaluator"),
    ("qa_agent/evaluators.py", "def apply_pass_policy"),
    ("qa_agent/evaluators.py", "def compute_agreement"),
    ("qa_agent/pipeline.py", "class PipelineOrchestrator"),
    ("qa_agent/pipeline.py", "def _evaluate_case"),
    ("qa_agent/pipeline.py", "def _evaluate_dual_compare"),
    ("qa_agent/pipeline.py", "def _run_functional_test"),
    ("qa_agent/pipeline.py", "def _load_previous_pass_map"),
    ("qa_agent/pipeline.py", "def _build_report"),
    ("qa_agent/pipeline.py", "ALL_TECHNIQUES"),
    ("qa_agent/reporter.py", "def write_reports"),
    ("qa_agent/reporter.py", "def write_defect_report_doc"),
    ("qa_agent/jira_notifier.py", "class JiraNotifier"),
    ("qa_agent/llm_client.py", "class OpenAIJudgeClient"),
    ("app/main.py", "def run_pipeline"),
    ("app/main.py", "def _execute_run"),
    ("app/main.py", "RUN_REGISTRY"),
    ("app/main.py", "_restore_active_dataset"),
    ("app/main.py", "_require_login"),
    ("qa_agent/auth.py", "def hash_password"),
    ("qa_agent/users.py", "class UserStore"),
]

PROCESS_SPEC_REFERENCES = [
    ("app/main.py", "def run_pipeline"),
    ("app/main.py", "def _execute_run"),
    ("app/main.py", "_default_cases"),
    ("qa_agent/pipeline.py", "def _evaluate_case"),
    ("qa_agent/pipeline.py", "def _load_previous_pass_map"),
    ("qa_agent/pipeline.py", "def _run_functional_test"),
    ("qa_agent/pipeline.py", "def _build_report"),
    ("qa_agent/pipeline.py", "def _evaluate_dual_compare"),
    ("qa_agent/reporter.py", "def write_reports"),
    ("qa_agent/reporter.py", "def write_defect_report_doc"),
    ("qa_agent/jira_notifier.py", "class JiraNotifier"),
    ("conftest.py", "_no_real_secrets_in_tests"),
    ("conftest.py", "pytest_terminal_summary"),
    ("quality/test_report.py", "def write_test_results_doc"),
]

USER_MANUAL_REFERENCES = [
    ("app/main.py", '"/api/dataset/upload"'),
    ("app/main.py", '"/api/run"'),
    ("app/main.py", '"/api/run/{run_id}/status"'),
    ("app/main.py", '"/api/run/{run_id}/result"'),
    ("app/main.py", '"/api/runs"'),
    ("app/main.py", '"/api/jira/tickets"'),
    ("app/main.py", '"/api/settings"'),
    ("app/main.py", '"/api/reports/latest"'),
    ("app/main.py", '"/health"'),
    ("app/main.py", '"/login"'),
    ("app/main.py", '"/api/auth/status"'),
    ("qa_agent/config_loader.py", "def load_config"),
    (".env.example", "OPENAI_API_KEY"),
    (".env.example", "create_admin.py"),
]

NETWORK_GUIDE_REFERENCES = [
    ("app/main.py", "def get_local_ip"),
    ("app/main.py", "_print_network_access_info"),
    ("Dockerfile", "EXPOSE 8000"),
    ("docker-compose.yml", "8000:8000"),
]

DOCS_MUST_EXIST = [
    "docs/사용자_매뉴얼.md",
    "docs/설계서.md",
    "docs/프로세스_명세서.md",
    "docs/팀원용_접속가이드.md",
]


@pytest.mark.parametrize("doc_path", DOCS_MUST_EXIST)
def test_static_doc_exists(doc_path):
    assert (ROOT / doc_path).exists()


@pytest.mark.parametrize("doc_path", DOCS_MUST_EXIST)
def test_no_unescaped_pipe_inside_code_span_on_table_rows(doc_path):
    for line_no, line in enumerate(_read(doc_path).splitlines(), start=1):
        if not line.strip().startswith("|"):
            continue
        offenders = _unescaped_pipe_code_spans(line)
        assert not offenders, (
            f"{doc_path}:{line_no} has a code span with an unescaped `|` on a table row {offenders!r} "
            "-- this breaks table parsing in standard Markdown tools (VS Code/GitHub), not just the "
            "in-app renderer. Escape it as \\| or restructure the row (e.g. a list) to avoid a pipe inside a code span."
        )


@pytest.mark.parametrize("target_file,symbol", DESIGN_SPEC_REFERENCES)
def test_design_spec_reference_still_exists(target_file, symbol):
    assert symbol in _read(target_file), f"설계서.md가 참조하는 `{symbol}`이(가) {target_file}에서 사라졌습니다 — 설계서.md 갱신 필요"


@pytest.mark.parametrize("target_file,symbol", PROCESS_SPEC_REFERENCES)
def test_process_spec_reference_still_exists(target_file, symbol):
    assert symbol in _read(target_file), f"프로세스_명세서.md가 참조하는 `{symbol}`이(가) {target_file}에서 사라졌습니다 — 프로세스_명세서.md 갱신 필요"


@pytest.mark.parametrize("target_file,symbol", USER_MANUAL_REFERENCES)
def test_user_manual_reference_still_exists(target_file, symbol):
    assert symbol in _read(target_file), f"사용자_매뉴얼.md가 참조하는 `{symbol}`이(가) {target_file}에서 사라졌습니다 — 사용자_매뉴얼.md 갱신 필요"


@pytest.mark.parametrize("target_file,symbol", NETWORK_GUIDE_REFERENCES)
def test_network_guide_reference_still_exists(target_file, symbol):
    assert symbol in _read(target_file), f"팀원용_접속가이드.md가 참조하는 `{symbol}`이(가) {target_file}에서 사라졌습니다 — 팀원용_접속가이드.md 갱신 필요"
