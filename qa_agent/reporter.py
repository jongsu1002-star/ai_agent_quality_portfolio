"""실행 결과를 JSON/CSV/마크다운 리포트로 저장하고, 실행 이력을 조회하는 모듈.

`write_reports()`가 매 실행마다 run_{id}.json/.csv, latest.json, 최종 품질 리포트를
만들고, `write_defect_report_doc()`은 그 중 결함 관련 부분만 뽑아 docs/결함보고서.md로
따로 재생성합니다(둘 다 자동 생성 - 수동 편집 금지).
"""

from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path
from typing import Any, Dict, List

from .models import RunReport


def _failing_criteria(case: Dict[str, Any]) -> List[str]:
    """케이스 하나가 어떤 항목(들) 때문에 실패했는지 나열."""
    reasons = []
    if case.get("retrieval") and not case["retrieval"]["passed"]:
        reasons.append("retrieval")
    if case.get("groundedness") and not case["groundedness"]["final_pass"]:
        reasons.append("groundedness")
    if case.get("context_relevance") and not case["context_relevance"]["final_pass"]:
        reasons.append("context_relevance")
    if case.get("llm_judge") and case["llm_judge"]["passed"] is False:
        reasons.append("llm_judge")
    if case.get("rubric") and not case["rubric"]["passed"]:
        reasons.append("rubric")
    if case.get("regression") and not case["regression"]["final_pass"]:
        reasons.append("regression")
    if case.get("toxicity") and not case["toxicity"]["final_pass"]:
        reasons.append("toxicity")
    if not reasons and case.get("errors"):
        reasons.append("connector_error")
    return reasons


_KOREAN_CRITERIA_LABELS = {
    "retrieval": "검색품질",
    "groundedness": "근거성",
    "context_relevance": "컨텍스트 관련성",
    "llm_judge": "LLM 판정",
    "rubric": "루브릭",
    "regression": "회귀",
    "toxicity": "유해성",
    "connector_error": "커넥터 오류",
    "unknown": "알 수 없음",
}


def _korean_criteria(criteria: List[str]) -> str:
    """_failing_criteria()가 반환한 영문 토큰들을 사람이 읽을 한글 라벨로 변환."""
    return ", ".join(_KOREAN_CRITERIA_LABELS.get(c, c) for c in criteria) or "알 수 없음"


def _build_defects_section(report: Dict[str, Any]) -> str:
    """final_quality_report.md의 "결함" 섹션 - 실패한 케이스들을 나열."""
    failing = [case for case in report.get("cases", []) if not case.get("overall_pass")]
    if not failing:
        return "- 이번 실행에서 실패한 케이스가 없습니다.\n"
    lines = []
    for case in failing:
        criteria = _korean_criteria(_failing_criteria(case))
        lines.append(f"- `{case['case_id']}`: {criteria} 항목에서 실패")
    return "\n".join(lines) + "\n"


def _build_suggestions(report: Dict[str, Any]) -> str:
    """실제 리포트 수치를 바탕으로 개선 제안을 계산해서 만듦 (고정 텍스트가 아님)."""
    suggestions = []
    for category, stats in (report.get("category_stats") or {}).items():
        total = stats.get("total", 0)
        passed = stats.get("passed", 0)
        if total and (1 - passed / total) >= 0.2:
            suggestions.append(f"- 카테고리 `{category}`의 실패율이 {(1 - passed / total):.0%}입니다. 골든 답변과 검색 소스를 점검하세요.")
    if report.get("regressions_detected"):
        suggestions.append(f"- 이전 실행 대비 {report['regressions_detected']}건이 회귀했습니다. 배포 전에 반드시 확인하세요.")
    mismatches = report.get("mismatch_cases") or []
    if mismatches:
        suggestions.append(f"- {len(mismatches)}건에서 룰 기반과 LLM 판정이 불일치합니다. 임계값 조정 또는 수동 검토를 고려하세요.")
    functional = report.get("functional_test") or {}
    if functional and functional.get("failed"):
        suggestions.append(f"- 커넥터 계약 검사 {functional['failed']}건이 실패했습니다. 커넥터의 예외 처리를 보강하세요.")
    if not suggestions:
        suggestions.append("- 특별한 개선 제안이 없습니다. 품질 지표가 양호합니다.")
    return "\n".join(suggestions) + "\n"


def _build_overall_opinion(report: Dict[str, Any]) -> str:
    """전체 통과율 구간에 따라 배포 가능 여부에 대한 종합 의견 한 줄을 만듦."""
    pass_rate = report.get("overall_pass_rate", 0.0)
    if pass_rate >= 0.95:
        return "전체 품질이 우수하여 안전하게 배포를 진행할 수 있습니다."
    if pass_rate >= 0.8:
        return "전체 품질은 양호하나 눈에 띄는 격차가 있습니다. 배포 전에 실패 케이스를 검토하세요."
    return "전체 품질이 허용 기준에 미달합니다. 실패 케이스를 해결하기 전까지 배포하지 마세요."


def _render_markdown(report: Dict[str, Any]) -> str:
    """final_quality_report.md 전체 본문을 조립."""
    lines = [
        "# 최종 품질 리포트",
        "",
        f"- 실행 ID: {report.get('run_id')}",
        f"- 전체 통과율: {report.get('overall_pass_rate', 0.0):.2%}",
        f"- 회귀 탐지 건수: {report.get('regressions_detected', 0)}",
        "",
        "## 결함",
        "",
        _build_defects_section(report),
        "## 개선 제안",
        "",
        _build_suggestions(report),
        "## 종합 의견",
        "",
        _build_overall_opinion(report),
        "",
    ]
    return "\n".join(lines)


def write_reports(report: RunReport, reports_dir: str | None = None) -> None:
    """실행 결과를 reports_dir와 reports_dir/exports 양쪽에 JSON/CSV/마크다운으로 저장."""
    reports_dir = Path(reports_dir or "reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    exports_dir = reports_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    payload = report.to_dict()

    report_path = reports_dir / f"run_{report.run_id}.json"
    with report_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    for target_dir in (reports_dir, exports_dir):
        csv_path = target_dir / f"run_{report.run_id}.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["case_id", "overall_pass", "failing_criteria"])
            for case in payload["cases"]:
                writer.writerow([case["case_id"], case["overall_pass"], ", ".join(_failing_criteria(case))])

    markdown = _render_markdown(payload)
    for target_dir in (reports_dir, exports_dir):
        (target_dir / "final_quality_report.md").write_text(markdown, encoding="utf-8")

    for target_dir in (reports_dir, exports_dir):
        with (target_dir / "latest.json").open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)


def _render_defect_report_markdown(report: Dict[str, Any]) -> str:
    """docs/결함보고서.md 전체 본문을 조립 (final_quality_report.md보다 더 상세한 버전)."""
    failing = [case for case in report.get("cases", []) if not case.get("overall_pass")]

    lines = [
        "# 결함보고서",
        "",
        "> 이 문서는 QA 파이프라인 실행이 완료될 때마다 `qa_agent/reporter.py`가 자동으로 재생성합니다. 수동으로 편집하지 마세요.",
        "",
        f"- 실행 ID: {report.get('run_id')}",
        f"- 생성 시각: {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"- 전체 통과율: {report.get('overall_pass_rate', 0.0):.2%}",
        f"- 회귀 탐지 건수: {report.get('regressions_detected', 0)}",
        f"- 결함 케이스 수: {len(failing)} / {len(report.get('cases', []))}",
        "",
        "## 카테고리별 현황",
        "",
        "| 카테고리 | 전체 | 통과 | 실패 | 실패율 |",
        "|---|---|---|---|---|",
    ]
    for category, stats in (report.get("category_stats") or {}).items():
        total = stats.get("total", 0)
        passed = stats.get("passed", 0)
        fail_rate = (1 - passed / total) if total else 0.0
        lines.append(f"| `{category}` | {total} | {passed} | {total - passed} | {fail_rate:.0%} |")

    lines += ["", "## 결함 목록", ""]
    if not failing:
        lines.append("이번 실행에서 발견된 결함이 없습니다.")
    else:
        lines += ["| 케이스 ID | 실패 항목 | 오류 메시지 |", "|---|---|---|"]
        for case in failing:
            criteria = _korean_criteria(_failing_criteria(case))
            errors = "; ".join(case.get("errors") or []) or "-"
            lines.append(f"| `{case['case_id']}` | {criteria} | {errors} |")

    mismatches = report.get("mismatch_cases") or []
    lines += ["", "## 룰-LLM 불일치 케이스 (Mismatch)", ""]
    if not mismatches:
        lines.append("불일치 케이스가 없습니다.")
    else:
        lines += ["| 케이스 ID | 룰 판정 | LLM 판정 |", "|---|---|---|"]
        for mismatch in mismatches:
            lines.append(f"| `{mismatch.get('case_id')}` | {mismatch.get('rule_passed')} | {mismatch.get('llm_passed')} |")

    functional = report.get("functional_test") or {}
    lines += ["", "## Functional Test (커넥터 계약 검사)", ""]
    if not functional:
        lines.append("이번 실행에서는 functional 기법이 선택되지 않았습니다.")
    else:
        lines.append(f"- 전체 {functional.get('total', 0)}건 중 {functional.get('passed', 0)}건 통과, {functional.get('failed', 0)}건 실패")
        for probe in functional.get("probes", []):
            status = "통과" if probe.get("passed") else "실패"
            lines.append(f"  - `{probe.get('probe')}`: {status} ({probe.get('detail')})")

    lines += ["", "## 종합 의견", "", _build_overall_opinion(report), ""]
    return "\n".join(lines)


def write_defect_report_doc(report: Dict[str, Any], docs_dir: str = "docs") -> Path:
    """docs/결함보고서.md를 생성/갱신. app/main.py가 파이프라인 실행 완료 시마다 호출."""
    docs_path = Path(docs_dir)
    docs_path.mkdir(parents=True, exist_ok=True)
    output_path = docs_path / "결함보고서.md"
    output_path.write_text(_render_defect_report_markdown(report), encoding="utf-8")
    return output_path


def list_run_history(reports_dir: str | None = None) -> List[dict]:
    """실행 이력 요약 목록(run_id, overall_pass_rate)을 시간순으로 반환."""
    reports_dir = Path(reports_dir or "reports")
    runs = []
    # 파일명이 아니라 수정 시각 기준으로 정렬 - 파일명 정렬이면 "run_10"이 "run_2"보다
    # 앞에 와버려서, 실행 횟수가 10회를 넘으면 대시보드의 "최근 실행"이 엉뚱한 걸 가리키게 됨
    for path in sorted(reports_dir.glob("run_*.json"), key=lambda p: p.stat().st_mtime):
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            runs.append({
                "run_id": data.get("run_id"),
                "overall_pass_rate": data.get("overall_pass_rate"),
                "dataset_path": data.get("dataset_path"),
                "dataset_case_count": data.get("dataset_case_count"),
                "testcase_path": data.get("testcase_path"),
                "testcase_case_count": data.get("testcase_case_count"),
            })
        except Exception:
            continue
    return runs
