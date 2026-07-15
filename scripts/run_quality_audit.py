"""pytest 전체 스위트를 TXT(콘솔 로그) + JUnit XML + HTML 리포트 3종으로 동시에 산출.

사람이 읽는 로그(txt), CI/CD 연계용 기계 판독 포맷(xml), 브라우저에서 바로 열어보는
시각적 리포트(html)를 한 번의 실행으로 함께 남기고 싶을 때 사용하는 품질 감사 스크립트.
reports/exports/에 타임스탬프가 붙은 파일로 저장됨 - reports/ 전체가 .gitignore 대상이라
저장소에는 커밋되지 않고, 실행할 때마다 그 시점의 실제 테스트 결과로 재생성됨.

사용법: python scripts/run_quality_audit.py
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = PROJECT_ROOT / "reports" / "exports"

_TXT_SUMMARY_RE = re.compile(r"^(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) deselected)? in ", re.MULTILINE)


def _junit_test_count(xml_path: Path) -> int:
    root = ET.parse(xml_path).getroot()
    if root.tag == "testsuite":
        return int(root.attrib.get("tests", 0))
    return sum(int(suite.attrib.get("tests", 0)) for suite in root.findall(".//testsuite"))


def _txt_passed_count(txt_content: str) -> int | None:
    match = _TXT_SUMMARY_RE.search(txt_content)
    return int(match.group(1)) if match else None


def _collected_test_count() -> int | None:
    """--collect-only로 별도 프로세스를 한 번 더 돌려, 실제 실행 결과(JUnit/TXT)와
    독립적으로 "몇 개가 수집됐는가"를 교차검증한다(세 수치가 서로 다르면 감사 자체를
    신뢰할 수 없다는 뜻이므로 실패 처리)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    return int(match.group(1)) if match else None


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or None if result.returncode == 0 else None
    except Exception:
        return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    generated_at = datetime.now().isoformat()
    txt_path = EXPORT_DIR / f"pytest_result_{stamp}.txt"
    xml_path = EXPORT_DIR / f"junit_{stamp}.xml"
    html_path = EXPORT_DIR / f"pytest_report_{stamp}.html"
    manifest_path = EXPORT_DIR / f"audit_manifest_{stamp}.json"

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
    junit_count = None
    txt_count = None
    collected_count = None
    counts_consistent = False

    missing = [p.name for p in (txt_path, xml_path, html_path) if not p.exists() or p.stat().st_size == 0]
    if missing:
        print(f"[quality-audit] 다음 파일이 생성되지 않았거나 비어 있어 감사를 실패 처리합니다: {missing}")
        returncode = returncode or 3

    if returncode == 0:
        try:
            junit_count = _junit_test_count(xml_path)
        except (OSError, ET.ParseError, ValueError):
            print("[quality-audit] JUnit XML을 판독하지 못해 감사를 실패 처리합니다.")
            returncode = 2
        else:
            if junit_count == 0:
                print("[quality-audit] 수집된 테스트가 0건이므로 감사를 실패 처리합니다.")
                returncode = 5
            else:
                txt_count = _txt_passed_count(result.stdout)
                collected_count = _collected_test_count()
                counts_consistent = txt_count == junit_count == collected_count
                print(f"[quality-audit] JUnit tests={junit_count} / TXT passed={txt_count} / collect-only={collected_count}")
                if not counts_consistent:
                    print("[quality-audit] 세 수치가 서로 다릅니다 - 감사를 실패 처리합니다.")
                    returncode = 6

    manifest = {
        "generated_at": generated_at,
        "git_sha": _git_sha(),
        "exit_code": returncode,
        "counts": {"junit_tests": junit_count, "txt_passed": txt_count, "collected": collected_count, "consistent": counts_consistent},
        "files": {
            name: {"filename": p.name, "sha256": _sha256(p) if p.exists() else None}
            for name, p in (("txt", txt_path), ("xml", xml_path), ("html", html_path))
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[quality-audit] TXT:      {txt_path}")
    print(f"[quality-audit] XML:      {xml_path}")
    print(f"[quality-audit] HTML:     {html_path}")
    print(f"[quality-audit] MANIFEST: {manifest_path}")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
