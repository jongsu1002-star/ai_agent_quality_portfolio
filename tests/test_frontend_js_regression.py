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
MONITORING_ADDON_HTML = REPO_ROOT / "app" / "templates" / "monitoring_addon.html"
JS_REGRESSION_SCRIPT = REPO_ROOT / "tests" / "js" / "voc_polling_regression.js"
JS_XVAL_REGRESSION_SCRIPT = REPO_ROOT / "tests" / "js" / "voc_cross_validation_regression.js"
JS_STEP_CHECKLIST_SCRIPT = REPO_ROOT / "tests" / "js" / "step_checklist_regression.js"


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


@pytest.mark.skipif(not _node_available(), reason="node 실행 파일을 찾을 수 없어 JS 회귀 테스트를 건너뜁니다")
def test_voc_cross_validation_js_regression_suite_passes():
    """tests/js/voc_cross_validation_regression.js의 9개 체크(그룹 미선택 시 차단, 선택한
    그룹만 요청에 실림, API 키 trim/null 처리, 성공·실패·네트워크예외 모두에서 키 입력란
    정리, 이력 갱신/토스트, 엑셀 미업로드 차단)가 전부 통과하는지 확인 - HTML 문자열
    존재 여부만 보는 아래 wired 테스트와 달리 실제 함수 로직을 Node vm으로 실행해 검증한다."""
    result = subprocess.run(
        ["node", str(JS_XVAL_REGRESSION_SCRIPT)],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, (
        f"voc_cross_validation_regression.js 실패(exit={result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert "0 failed" in result.stdout


@pytest.mark.skipif(not _node_available(), reason="node 실행 파일을 찾을 수 없어 JS 회귀 테스트를 건너뜁니다")
def test_step_checklist_js_regression_suite_passes():
    """tests/js/step_checklist_regression.js의 6개 체크(진행/완료/대기 상태 표시, 전체
    완료, 실패 단계 표시, HTML 이스케이프, 순서 유지)가 전부 통과하는지 확인 -
    renderStepChecklist는 QA 파이프라인/VOC 분석/교차검증 매트릭스/업로드 3종이 전부
    공유하는 컴포넌트라 다른 슬라이스 테스트들은 이를 스텁으로 대체하고, 이 파일에서
    실제 구현을 직접 검증한다."""
    result = subprocess.run(
        ["node", str(JS_STEP_CHECKLIST_SCRIPT)],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, (
        f"step_checklist_regression.js 실패(exit={result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert "0 failed" in result.stdout


def test_k6_trigger_button_wired_to_progress_and_step_checklist():
    """k6 성능테스트 실행도 다른 실행 버튼들과 동일하게 진행률 바 + 단계 체크리스트를
    보여줘야 함(monitoring_addon.html은 index.html과 별도 문서라 컴포넌트를 자체
    보유 - _renderStepChecklist 등)."""
    html = MONITORING_ADDON_HTML.read_text(encoding="utf-8")
    assert 'onclick="triggerK6Run()"' in html
    assert 'id="k6-progress-track"' in html
    assert 'id="k6-progress-fill"' in html
    assert 'id="k6-trigger-status"' in html
    assert "function _renderStepChecklist" in html
    assert "function _parseK6DurationSeconds" in html
    assert "function _setK6Progress" in html
    assert "_renderStepChecklist(K6_STEP_LIST" in html


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


def test_voc_cross_validation_button_wired_to_run_function():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="voc-xval-btn"' in html
    assert 'onclick="runVocCrossValidation()"' in html
    assert "function runVocCrossValidation" in html
    assert "function _renderVocCrossValidationResult" in html
    assert 'id="voc-xval-result"' in html


def test_voc_cross_validation_group_selection_and_api_key_override_wired():
    """A~D 조합 선택 체크박스와 이번 실행 전용 API 키 입력란이 실제로 요청 본문에
    실려 나가는지 - 마크업만 있고 JS가 안 읽으면 아무 의미가 없으므로 함께 확인."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    for letter in ("A", "B", "C", "D"):
        assert f'class="voc-xval-group-checkbox" value="{letter}"' in html
    assert 'id="voc-xval-openai-key"' in html
    assert 'id="voc-xval-anthropic-key"' in html
    assert "querySelectorAll('.voc-xval-group-checkbox:checked')" in html
    assert "groups: selectedGroups" in html
    assert "openai_api_key: document.getElementById('voc-xval-openai-key')" in html
    assert "anthropic_api_key: document.getElementById('voc-xval-anthropic-key')" in html


def test_voc_cross_validation_history_list_wired():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="voc-xval-history-list"' in html
    assert "function loadVocCrossValidationHistory" in html
    assert "function loadVocCrossValidationRecord" in html
    assert "function deleteVocCrossValidation" in html
    assert "onclick=\"loadVocCrossValidationRecord(" in html
    assert "onclick=\"event.stopPropagation(); deleteVocCrossValidation(" in html


def test_voc_cross_validation_history_loaded_on_tab_switch_and_after_run():
    """이력 목록이 실제로 (1) VOC 탭 진입 시, (2) 매트릭스 실행 완료 직후 갱신되는지 확인 -
    함수만 정의돼 있고 아무도 호출하지 않으면 목록이 절대 새로고침되지 않으므로 별도 확인."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert html.count("loadVocCrossValidationHistory();") >= 2


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
