"""docs/테스트_결과.md 자동 생성 로직 - conftest.py의 pytest 훅이 이 모듈을 호출."""

from __future__ import annotations

import datetime
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from .test_descriptions import TEST_DESCRIPTIONS

_OUTCOME_LABELS = {"passed": "통과", "failed": "실패", "skipped": "건너뜀", "error": "오류"}
_PYTEST_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _decode_pytest_id(name: str) -> str:
    """parametrize ID 안의 한글 등 비ASCII 문자를 pytest가 `\\uXXXX`로 이스케이프해두는 것을
    실제 글자로 되돌림 - 안 그러면 "테스트 이름" 칸이 사람이 못 읽는 이스케이프 문자열로 남음.
    """
    return _PYTEST_UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), name)


def render_test_results_markdown(stats: Dict[str, List[Any]], exitstatus: int) -> str:
    """pytest의 `terminalreporter.stats`를 한글 상태 리포트 문자열로 변환.

    실제 pytest 세션 없이도 단위 테스트할 수 있도록, 렌더링 로직을 훅 호출부와
    분리해 뒀습니다.
    """
    passed = stats.get("passed", [])
    failed = stats.get("failed", [])
    skipped = stats.get("skipped", [])
    errors = stats.get("error", [])

    # 테스트 1건당 한 줄 - 파일/테스트이름/설명(test_descriptions.py 조회)/결과를 함께 보여줌
    rows: List[Dict[str, str]] = []
    for outcome, reports in (("passed", passed), ("failed", failed), ("skipped", skipped), ("error", errors)):
        for report in reports:
            file_path, _, raw_test_name = report.nodeid.partition("::")
            test_name = _decode_pytest_id(raw_test_name)
            base_name = test_name.split("[")[0]  # parametrize 대괄호를 떼어내야 설명 테이블과 매칭됨
            rows.append(
                {
                    "file": file_path,
                    "name": test_name,
                    "description": TEST_DESCRIPTIONS.get(base_name, "-"),
                    "outcome": _OUTCOME_LABELS[outcome],
                }
            )
    # parametrize로 여러 번 도는 테스트는 파일+설명+결과가 똑같은 행이 반복되므로
    # 하나로 묶고 건수만 세어줌 (예: 문서 4개 x 참조 심볼마다 도는 테스트 -> 한 줄 + 건수)
    grouped = Counter((row["file"], row["description"], row["outcome"]) for row in rows)
    grouped_rows = [
        {"file": file_path, "description": description, "count": count, "outcome": outcome}
        for (file_path, description, outcome), count in grouped.items()
    ]
    grouped_rows.sort(key=lambda r: (r["file"], r["description"], r["outcome"]))

    total = len(passed) + len(failed) + len(skipped) + len(errors)

    lines = [
        "# 테스트 결과",
        "",
        "> 이 문서는 `conftest.py`의 `pytest_terminal_summary` 훅이 `pytest` 실행 시마다 자동으로 재생성합니다. 수동으로 편집하지 마세요.",
        "> 테스트를 일부만 선택해 실행한 경우, 이 문서에는 그 실행 범위만 반영됩니다.",
        "",
        f"- 최종 실행 시각: {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"- 종료 코드: {exitstatus} ({'성공' if exitstatus == 0 else '실패 있음'})",
        f"- 총 테스트 수: {total}",
        f"- 통과: {len(passed)}",
        f"- 실패: {len(failed)}",
        f"- 건너뜀: {len(skipped)}",
        f"- 오류: {len(errors)}",
        "",
        "## 파일별 결과",
        "",
        "| 테스트 파일 | 설명 | 건수 | 결과 |",
        "|---|---|---|---|",
    ]
    for row in grouped_rows:
        lines.append(f"| `{row['file']}` | {row['description']} | {row['count']} | {row['outcome']} |")

    if failed or errors:
        lines += ["", "## 실패/오류 상세", ""]
        for report in failed + errors:
            lines.append(f"- `{report.nodeid}`")
    else:
        lines += ["", "모든 테스트가 통과했습니다.", ""]

    lines.append("")
    return "\n".join(lines)


def write_test_results_doc(stats: Dict[str, List[Any]], exitstatus: int, docs_dir: str = "docs") -> Path:
    """docs/테스트_결과.md를 생성/갱신."""
    docs_path = Path(docs_dir)
    docs_path.mkdir(parents=True, exist_ok=True)
    output_path = docs_path / "테스트_결과.md"
    output_path.write_text(render_test_results_markdown(stats, exitstatus), encoding="utf-8")
    return output_path
