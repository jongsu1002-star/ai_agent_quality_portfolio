"""pytest 전체 스위트를 TXT(콘솔 로그) + JUnit XML + HTML 리포트 3종으로 동시에 산출.

사람이 읽는 로그(txt), CI/CD 연계용 기계 판독 포맷(xml), 브라우저에서 바로 열어보는
시각적 리포트(html)를 한 번의 실행으로 함께 남기고 싶을 때 사용하는 품질 감사 스크립트.
reports/exports/에 타임스탬프가 붙은 파일로 저장됨 - reports/ 전체가 .gitignore 대상이라
저장소에는 커밋되지 않고, 실행할 때마다 그 시점의 실제 테스트 결과로 재생성됨.

사용법: python scripts/run_quality_audit.py
"""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = PROJECT_ROOT / "reports" / "exports"


def _junit_test_count(xml_path: Path) -> int:
    root = ET.parse(xml_path).getroot()
    if root.tag == "testsuite":
        return int(root.attrib.get("tests", 0))
    return sum(int(suite.attrib.get("tests", 0)) for suite in root.findall(".//testsuite"))


def main() -> int:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = EXPORT_DIR / f"pytest_result_{stamp}.txt"
    xml_path = EXPORT_DIR / f"junit_{stamp}.xml"
    html_path = EXPORT_DIR / f"pytest_report_{stamp}.html"

    cmd = [
        sys.executable, "-m", "pytest", "-q",
        f"--junitxml={xml_path}",
        f"--html={html_path}", "--self-contained-html",
    ]
    print(f"[quality-audit] 실행: {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    txt_path.write_text(result.stdout, encoding="utf-8")
    print(result.stdout)

    returncode = result.returncode
    if returncode == 0:
        try:
            test_count = _junit_test_count(xml_path)
        except (OSError, ET.ParseError, ValueError):
            print("[quality-audit] JUnit XML을 판독하지 못해 감사를 실패 처리합니다.")
            returncode = 2
        else:
            if test_count == 0:
                print("[quality-audit] 수집된 테스트가 0건이므로 감사를 실패 처리합니다.")
                returncode = 5
            else:
                print(f"[quality-audit] 검증된 테스트 수: {test_count}")

    print(f"[quality-audit] TXT:  {txt_path}")
    print(f"[quality-audit] XML:  {xml_path}")
    print(f"[quality-audit] HTML: {html_path}")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
