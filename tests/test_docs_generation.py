from qa_agent.reporter import write_defect_report_doc
from quality.test_report import render_test_results_markdown, write_test_results_doc


class _FakeReport:
    def __init__(self, nodeid):
        self.nodeid = nodeid


def test_write_defect_report_doc_lists_failing_cases(tmp_path):
    report = {
        "run_id": "run_9",
        "overall_pass_rate": 0.5,
        "regressions_detected": 1,
        "category_stats": {"COM": {"total": 2, "passed": 1}},
        "mismatch_cases": [{"case_id": "TC-2", "rule_passed": True, "llm_passed": False}],
        "functional_test": {"total": 4, "passed": 3, "failed": 1, "probes": [{"probe": "empty_question", "passed": False, "detail": "crashed"}]},
        "cases": [
            {"case_id": "TC-1", "overall_pass": True, "errors": []},
            {"case_id": "TC-2", "overall_pass": False, "errors": [], "retrieval": {"passed": False}},
        ],
    }

    path = write_defect_report_doc(report, docs_dir=str(tmp_path))
    content = path.read_text(encoding="utf-8")

    assert "결함보고서" in content
    assert "TC-2" in content
    assert "검색품질" in content  # translated criteria label, not the raw "retrieval" token
    assert "COM" in content


def test_write_defect_report_doc_lists_test_type_breakdown(tmp_path):
    report = {
        "run_id": "run_10",
        "overall_pass_rate": 1.0,
        "regressions_detected": 0,
        "category_stats": {"COM": {"total": 2, "passed": 2}},
        "test_type_stats": {"regression": {"total": 1, "passed": 1}, "미분류": {"total": 1, "passed": 1}},
        "mismatch_cases": [],
        "functional_test": {},
        "cases": [
            {"case_id": "TC-1", "overall_pass": True, "errors": []},
            {"case_id": "TC-2", "overall_pass": True, "errors": []},
        ],
    }

    path = write_defect_report_doc(report, docs_dir=str(tmp_path))
    content = path.read_text(encoding="utf-8")

    assert "테스트 유형별 현황" in content
    assert "`regression`" in content
    assert "`미분류`" in content


def test_write_defect_report_doc_notes_missing_test_type(tmp_path):
    report = {"run_id": "run_11", "overall_pass_rate": 1.0, "regressions_detected": 0, "cases": []}

    path = write_defect_report_doc(report, docs_dir=str(tmp_path))
    content = path.read_text(encoding="utf-8")

    assert "테스트 유형(`test_type`)이 지정되지 않았습니다" in content


def test_render_test_results_markdown_summarizes_pass_fail():
    stats = {
        "passed": [_FakeReport("tests/test_a.py::test_one"), _FakeReport("tests/test_a.py::test_two")],
        "failed": [_FakeReport("tests/test_b.py::test_three")],
        "skipped": [],
        "error": [],
    }

    markdown = render_test_results_markdown(stats, exitstatus=1)

    assert "총 테스트 수: 3" in markdown
    assert "통과: 2" in markdown
    assert "실패: 1" in markdown
    assert "tests/test_b.py::test_three" in markdown


def test_write_test_results_doc_creates_file(tmp_path):
    stats = {"passed": [_FakeReport("tests/test_a.py::test_one")], "failed": [], "skipped": [], "error": []}
    path = write_test_results_doc(stats, exitstatus=0, docs_dir=str(tmp_path))
    assert path.exists()
    assert "모든 테스트가 통과했습니다" in path.read_text(encoding="utf-8")
