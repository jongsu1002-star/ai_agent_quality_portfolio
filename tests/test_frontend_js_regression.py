"""VOC 폴링 프론트엔드(P0-3) 최소 회귀 테스트.

실제 브라우저 기반(Playwright 등) E2E는 이 프로젝트에 아직 그 인프라(package.json 등)가
없어 이번엔 새로 들이지 않고, 최소 요구사항인 "JS 함수 + HTML 배선" 회귀 테스트로
대응한다: JS 함수 동작은 tests/js/voc_polling_regression.js(Node vm으로 index.html의
해당 코드를 그대로 실행)에, HTML 배선(버튼-함수 연결)은 아래 문자열 검사로 확인한다.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO_ROOT / "app" / "templates" / "index.html"
JS_REGRESSION_SCRIPT = REPO_ROOT / "tests" / "js" / "voc_polling_regression.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node 실행 파일을 찾을 수 없어 JS 회귀 테스트를 건너뜁니다")
def test_voc_polling_js_regression_suite_passes():
    """tests/js/voc_polling_regression.js의 11개 체크(500/404/네트워크예외 재시도,
    최대 실패 후 버튼 복구, 새로고침 복구, sessionStorage 정리, 이전 run 무시,
    취소 실패 표시)가 전부 통과하는지 subprocess로 실행해 확인."""
    result = subprocess.run(
        ["node", str(JS_REGRESSION_SCRIPT)],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, (
        f"voc_polling_regression.js 실패(exit={result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert "0 failed" in result.stdout


def test_voc_run_button_wired_to_run_function():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="voc-run-btn"' in html
    assert 'onclick="runVocAnalysis()"' in html


def test_voc_cancel_button_wired_to_cancel_function():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="voc-cancel-btn"' in html
    assert 'onclick="cancelVocAnalysis()"' in html


def test_voc_polling_bootstrap_calls_resume_on_page_load():
    """새로고침 복구가 실제로 페이지 부팅 시퀀스에 연결돼 있는지 - 함수만 정의돼 있고
    아무도 호출하지 않으면 새로고침 복구는 절대 동작하지 않으므로 별도로 확인."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "_resumeVocRunIfAny();" in html


def test_voc_polling_functions_all_defined_in_index_html():
    """P0-3에서 새로 추가된 함수들이 실제로 정의돼 있는지(오탈자로 조용히 빠지는 것 방지)."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    for fn_name in [
        "function runVocAnalysis",
        "function _pollVocRun",
        "function _resetVocRunButtons",
        "function _handleVocPollFailure",
        "function _resumeVocRunIfAny",
        "function cancelVocAnalysis",
        "function _saveVocRunToStorage",
        "function _clearVocRunStorage",
        "function _loadVocRunFromStorage",
    ]:
        assert fn_name in html, f"{fn_name} 정의를 찾지 못함"
