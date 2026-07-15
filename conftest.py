"""프로젝트 전체 pytest 설정 - 테스트 격리(비밀값 제거)와 테스트 결과 문서 자동생성 담당."""

from __future__ import annotations

import os

import pytest

from quality.test_report import write_test_results_doc

# app.main이 import 시점에 load_dotenv()를 호출하므로, 개발자의 .env에 실제 값이 있으면
# 테스트가 그걸 그대로 집어 쓸 수 있음 - 그래서 테스트마다 강제로 비워서 격리시킴
_SECRET_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "LLM_PROVIDER",
    "LLM_KEY_NAME",
    "LLM_KEY_VALUE",
    "LLM_ENDPOINT",
    "LLM_MODEL",
    "SLACK_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
    "TEAMS_WEBHOOK_URL",
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_TOKEN",
    "JIRA_PROJECT",
)


@pytest.fixture(autouse=True)
def _restore_os_environ_after_test(monkeypatch):
    """app/main.py::save_settings()는 요청 본문의 값을 os.environ[KEY] = value로 직접
    반영한다(monkeypatch를 거치지 않는 실제 애플리케이션 동작). monkeypatch는 자신이 직접
    바꾼 값만 되돌리므로, 이런 앱 코드의 직접 mutation은 테스트가 끝나도 그대로 남아 다음
    테스트로 새어나갈 수 있다. os.environ 객체 자체를 테스트 시작 시점의 얕은 복사본으로
    바꿔치기해두면, 그 안에서 일어나는 모든 mutation(monkeypatch를 거치든 앱 코드가 직접
    하든)이 복사본에만 남고, 테스트가 끝나면 monkeypatch가 원본 os.environ으로 자동
    복원한다."""
    monkeypatch.setattr(os, "environ", os.environ.copy())


@pytest.fixture(autouse=True)
def _no_real_secrets_in_tests(monkeypatch):
    """테스트는 개발자의 실제 .env 비밀값에 의존해서는 안 됨.

    app.main이 import 시점에 load_dotenv()를 호출하기 때문에, 이 픽스처가 없으면
    테스트 스위트가 진짜 OPENAI_API_KEY를 집어서 실제 OpenAI에 네트워크 호출을
    시작해버립니다 - 느리고, 불안정하고, 자칫하면 과금까지 될 수 있습니다. 특정
    키가 정말 필요한 개별 테스트는 자기 안에서 직접 monkeypatch로 설정하면 됩니다.
    """
    for key in _SECRET_ENV_VARS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _isolate_user_store(tmp_path, monkeypatch):
    """app.main.USER_STORE는 기본적으로 실서비스 data/users.db를 가리키는 싱글턴이라,
    이 픽스처 없이 /signup·/login 등을 TestClient로 두드리면 실제 계정 DB에 테스트용
    가입자가 그대로 쌓임 - 매 테스트마다 임시 DB로 바꿔치기해서 격리."""
    try:
        import app.main as main_module
        from qa_agent.users import UserStore
    except Exception:
        return
    isolated_store = UserStore(path=str(tmp_path / "users.db"))
    monkeypatch.setattr(main_module, "USER_STORE", isolated_store)
    yield
    isolated_store.close_thread_connection()


@pytest.fixture(autouse=True)
def _isolate_shared_reports_and_settings(tmp_path, monkeypatch):
    """SETTINGS_PATH/SHARED_REPORTS_ROOT/USER_DATA_ROOT/EXTERNAL_MONITOR/결함보고서 출력 위치를
    tmp_path로 격리.

    이 픽스처가 없으면 "shared" 버킷(로그인 안 한 요청·계정 시스템 꺼짐)이 실제
    reports/settings.json·reports/run_*.json·reports/exports/·docs/결함보고서.md를,
    "alice" 등 테스트에서 쓰는 실명 계정이 실제 reports/users/{username}/을 직접
    덮어쓴다 - 실서비스 계정과 겹치는 이름을 테스트가 우연히 재사용하면(예: alice) 진짜
    사용자 데이터가 테스트 산출물로 뒤섞인다. USER_STORE/BOARD_STORE와 같은 이유로
    모듈 전역 상수라 monkeypatch로 바꿔치기해야 하며, 값을 참조하는 함수(_user_reports_dir/
    _defect_report_docs_dir)는 모두 호출 시점에 이 상수를 다시 읽으므로 상수만 바꿔도
    전부 격리된다(코드 자체를 바꿀 필요 없음)."""
    try:
        import app.main as main_module
        from monitoring.external_monitor import ExternalMonitorRegistry
    except Exception:
        return
    tmp_settings_path = tmp_path / "reports" / "settings.json"
    tmp_settings_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main_module, "SETTINGS_PATH", tmp_settings_path)
    monkeypatch.setattr(main_module, "SHARED_REPORTS_ROOT", tmp_path / "reports")
    monkeypatch.setattr(main_module, "USER_DATA_ROOT", tmp_path / "reports" / "users")
    monkeypatch.setattr(main_module, "EXTERNAL_MONITOR", ExternalMonitorRegistry(path=str(tmp_path / "reports" / "monitoring_targets.json")))
    monkeypatch.setattr(main_module, "_defect_report_docs_dir", lambda username: str(tmp_path / "docs"))


@pytest.fixture(autouse=True)
def _isolate_board_store(tmp_path, monkeypatch):
    """BOARD_STORE와 VOC 분석 산출물도 같은 이유로 격리.

    app/routers/board.py·voc_analysis.py는 app.main이 import 시점에 한 번 호출한
    configure()로 받은 스토어 참조를 자기 모듈의 _state 딕셔너리에 따로 들고 있어서,
    main_module.BOARD_STORE만 monkeypatch해서는 라우터가 여전히 원래(실서비스) 스토어를
    바라봄 - 라우터의 configure()를 다시 호출해 격리된 스토어로 바꿔치기해야 함. VOC 분석
    결과 파일 경로(reports/voc_analysis/)도 모듈 상수라 같은 이유로 tmp_path로 바꿔치기."""
    try:
        import app.main as main_module
        from app.routers import board as board_module
        from app.routers import voc_analysis as voc_analysis_module
        from qa_agent.board import BoardStore
    except Exception:
        return
    isolated_store = BoardStore(path=str(tmp_path / "board.db"))
    monkeypatch.setattr(main_module, "BOARD_STORE", isolated_store)
    board_module.configure(isolated_store, main_module._current_username, main_module._is_admin_effective)
    voc_analysis_module.configure(isolated_store, main_module._current_username, main_module._load_settings_dict, main_module._llm_judge_kwargs, main_module._independent_judge_kwargs, main_module._is_admin_effective)
    monkeypatch.setattr(voc_analysis_module, "VOC_ANALYSIS_DIR", tmp_path / "voc_analysis")
    monkeypatch.setattr(voc_analysis_module, "VOC_UPLOAD_DIR", tmp_path / "voc_analysis" / "uploads")
    yield
    isolated_store.close_thread_connection()


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """전체 스위트 실행만 종합 결과 문서를 갱신한다.

    특정 파일/테스트만 실행하거나 0건이 수집된 실행이 마지막 종합 결과를 덮어쓰면, 좁은 검증을
    전체 통과로 오해할 수 있다. 선택 실행 결과는 터미널에만 남기고 문서는 보존한다.
    """
    stats = terminalreporter.stats
    total = sum(len(stats.get(key, [])) for key in ("passed", "failed", "skipped", "error"))
    if total == 0:
        terminalreporter.write_line("[test-report] 수집된 테스트가 없어 docs/테스트_결과.md를 갱신하지 않습니다.")
        return

    try:
        file_or_dir = list(config.getoption("file_or_dir") or [])
    except (ValueError, AttributeError):
        file_or_dir = []
    full_path_args = not file_or_dir or all(str(path).replace("\\", "/").rstrip("/") in (".", "tests") for path in file_or_dir)
    raw_args = tuple(str(arg) for arg in config.invocation_params.args)
    selection_flags = ("-k", "-m", "--lf", "--last-failed", "--ff", "--failed-first", "--sw", "--stepwise", "--collect-only")
    has_selection_filter = any(arg == flag or arg.startswith(flag + "=") for arg in raw_args for flag in selection_flags)
    if not full_path_args or has_selection_filter:
        terminalreporter.write_line("[test-report] 선택 실행이므로 docs/테스트_결과.md를 갱신하지 않습니다.")
        return
    write_test_results_doc(stats, exitstatus)
