"""VOC 폴링 프론트엔드(P0-3) 최소 회귀 테스트.

실제 브라우저 기반(Playwright 등) E2E는 이 프로젝트에 아직 그 인프라(package.json 등)가
없어 이번엔 새로 들이지 않고, 최소 요구사항인 "JS 함수 + HTML 배선" 회귀 테스트로
대응한다: JS 함수 동작은 tests/js/voc_polling_regression.js(Node vm으로 index.html의
해당 코드를 그대로 실행)에, HTML 배선(버튼-함수 연결)은 아래 문자열 검사로 확인한다.
"""

from __future__ import annotations

import json
import re
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


def test_voc_results_tab_wired_separately_from_voc_analysis_tab():
    """VOC 분석 실행(폼)과 결과 조회(이력/차트/보고서)를 별도 탭으로 분리 - 실행 탭에는
    더 이상 이력/차트/보고서 마크업이 남아있지 않아야 하고, 새 탭에 전부 있어야 한다."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'data-tab="voc-results" onclick="showTab(\'voc-results\')"' in html
    assert 'id="tab-voc-results"' in html

    run_tab_start = html.index('id="tab-voc-analysis"')
    run_tab_end = html.index('id="tab-voc-results"')
    run_tab_html = html[run_tab_start:run_tab_end]
    results_tab_html = html[run_tab_end:]

    for moved_id in ("voc-history-list", "voc-xval-history-list", "voc-quality-dashboard", "voc-quality-report", "voc-defect-report"):
        assert f'id="{moved_id}"' not in run_tab_html, f"{moved_id}가 여전히 VOC 분석(실행) 탭에 남아있음"
        assert f'id="{moved_id}"' in results_tab_html, f"{moved_id}가 새 VOC 분석 결과 탭에 없음"

    assert "if (name === 'voc-results') {" in html
    assert "loadVocHistory();" in html
    assert "loadVocQualityDashboard();" in html


def test_index_and_addon_nav_tabs_stay_in_sync():
    """monitoring_addon.html은 index.html과 별도 문서라 상단 탭 메뉴를 통째로 복제해서
    쓴다 - index.html에 새 탭(data-tab)을 추가할 때 이 복제본에 반영하는 걸 잊으면,
    모니터링 애드온 화면에서만 그 탭으로 가는 메뉴가 안 보이는 채로 조용히 어긋난다
    (실사용자가 "모니터링 애드온 탭에서 VOC 분석 결과 메뉴가 안 보인다"고 신고해 발견).
    index.html의 모든 data-tab 값이 monitoring_addon.html의 탭 메뉴에도 '/#{tab}'
    링크로 존재하는지 확인해 이 클래스의 회귀를 잡는다."""
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    addon_html = MONITORING_ADDON_HTML.read_text(encoding="utf-8")

    tab_names = re.findall(r'data-tab="([a-z-]+)"', index_html)
    assert tab_names, "index.html에서 data-tab 탭을 찾지 못함 - 정규식이 깨졌을 가능성"

    for tab_name in tab_names:
        assert f"/#{tab_name}'" in addon_html or f'/#{tab_name}"' in addon_html, (
            f'monitoring_addon.html 탭 메뉴에 "{tab_name}" 탭으로 가는 링크가 없음'
        )


def test_voc_quality_chart_full_page_link_present():
    """모니터링 애드온의 Prometheus/Grafana(임베딩 차트 + 새 창 링크)와 동일한 형식 -
    차트 카드 안에 새 창으로 여는 전용 페이지 링크가 있어야 한다."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert '<a href="/voc-quality-chart" target="_blank">' in html
    assert "차트 크게 보기" in html


def test_voc_grafana_card_wired_in_index_and_standalone_page():
    """VOC 판정/게이트 분포를 실제 Prometheus/Grafana로 보여주는 카드 - 모니터링 애드온과
    동일하게 GRAFANA_LINK_ENABLED 플래그로 노출 여부를 제어하고, 같은 Grafana 대시보드
    (qa-platform-voc)를 임베드+링크 두 곳 모두에서 가리켜야 한다."""
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    voc_chart_html = (REPO_ROOT / "app" / "templates" / "voc_quality_chart.html").read_text(encoding="utf-8")

    for html in (index_html, voc_chart_html):
        assert 'id="voc-grafana-card"' in html
        assert "const GRAFANA_LINK_ENABLED = __GRAFANA_LINK_ENABLED__;" in html
        assert "function loadVocPrometheusChart" in html
        assert "qa_platform_voc_judge_pass" in html
        assert "qa_platform_voc_judge_fail" in html
        assert "d-solo/qa-platform-voc/qa-platform-voc-analysis-quality" in html
        assert "id=\"voc-prometheus-link\"" in html
        assert "id=\"voc-grafana-link\"" in html


def test_grafana_prometheus_cards_use_ip_gated_proxy_not_direct_ports():
    """3000/9090으로 직접 접속하던 것을 이 세션에서 /grafana-proxy·/prometheus-proxy(IP
    허용목록 게이트)로 전환했다 - 직접 포트 패턴이 다시 섞여 들어오는 회귀를 잡는다."""
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    voc_chart_html = (REPO_ROOT / "app" / "templates" / "voc_quality_chart.html").read_text(encoding="utf-8")
    addon_html = MONITORING_ADDON_HTML.read_text(encoding="utf-8")

    for html in (index_html, voc_chart_html, addon_html):
        assert ":3000" not in html, "Grafana 직접 포트(3000) 참조가 남아있음 - /grafana-proxy를 써야 함"
        assert ":9090" not in html, "Prometheus 직접 포트(9090) 참조가 남아있음 - /prometheus-proxy를 써야 함"
        assert "addonHost" not in html, "더 이상 쓰이지 않는 addonHost 변수가 남아있음"
        assert "/grafana-proxy/" in html
        assert "/prometheus-proxy/" in html


def test_ip_allowlist_admin_tab_wired():
    """관리자 전용 '접근 허용 IP' 탭 - 사용자 관리/오류 로그 탭과 동일한 관리자 전용
    노출 패턴(is_admin일 때만 표시) + CRUD(등록/조회/삭제/수정) 함수가 모두 배선돼 있는지."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="ip-allowlist-tab-btn"' in html
    assert 'id="tab-ip-allowlist"' in html
    assert "document.getElementById('ip-allowlist-tab-btn').style.display = _isAdmin" in html
    assert "if (name === 'ip-allowlist') {" in html
    assert "loadIpAllowlist();" in html
    for fn in ("async function loadIpAllowlist", "async function addIpAllowlistEntry", "async function deleteIpAllowlistEntry", "function startEditIpAllowlistEntry", "async function saveIpAllowlistEntry"):
        assert fn in html


def test_my_ip_display_wired_next_to_logout_button_everywhere():
    """모든 화면(index.html, monitoring_addon.html, voc_quality_chart.html)의 로그아웃
    버튼 왼쪽에 접속 IP를 보여줘야 한다 - 사용자가 "접근 허용 IP" 탭에 무엇을 등록해야
    하는지 직접 알 수 있게 하기 위함(/api/auth/status의 client_ip를 그대로 표시)."""
    voc_chart_html = (REPO_ROOT / "app" / "templates" / "voc_quality_chart.html").read_text(encoding="utf-8")
    for html in (INDEX_HTML.read_text(encoding="utf-8"), MONITORING_ADDON_HTML.read_text(encoding="utf-8"), voc_chart_html):
        assert 'id="my-ip-display"' in html
        assert "data.client_ip" in html
        # 마크업 순서상 my-ip-display가 logout-btn보다 먼저 나와야("왼쪽") 함
        assert html.index('id="my-ip-display"') < html.index('id="logout-btn"')


def test_voc_grafana_dashboard_json_is_valid_and_matches_exported_metrics():
    """Grafana가 자동 프로비저닝하는 대시보드 JSON이 유효하고, 패널들이 실제로
    /metrics-addon이 내보내는 지표 이름을 그대로 쿼리하는지 확인(오타로 빈 패널이 되는
    것을 방지)."""
    dashboard_path = REPO_ROOT / "infra" / "grafana" / "dashboards" / "qa-platform-voc.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    assert dashboard["uid"] == "qa-platform-voc"

    exprs = " ".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )
    for metric in (
        "qa_platform_voc_total_runs",
        "qa_platform_voc_test_passed",
        "qa_platform_voc_test_total",
        "qa_platform_voc_judge_pass",
        "qa_platform_voc_judge_fail",
        "qa_platform_voc_gate_approved",
        "qa_platform_voc_gate_rejected",
    ):
        assert metric in exprs, f"대시보드 어떤 패널도 {metric}을 쿼리하지 않음"


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
