"""프로젝트 전체 pytest 설정 - 테스트 격리(비밀값 제거)와 테스트 결과 문서 자동생성 담당."""

from __future__ import annotations

import pytest

from quality.test_report import write_test_results_doc

# app.main이 import 시점에 load_dotenv()를 호출하므로, 개발자의 .env에 실제 값이 있으면
# 테스트가 그걸 그대로 집어 쓸 수 있음 - 그래서 테스트마다 강제로 비워서 격리시킴
_SECRET_ENV_VARS = (
    "APP_PASSWORD",
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
def _no_real_secrets_in_tests(monkeypatch):
    """테스트는 개발자의 실제 .env 비밀값에 의존해서는 안 됨.

    app.main이 import 시점에 load_dotenv()를 호출하기 때문에, 이 픽스처가 없으면
    테스트 스위트가 진짜 OPENAI_API_KEY를 집어서 실제 OpenAI에 네트워크 호출을
    시작해버립니다 - 느리고, 불안정하고, 자칫하면 과금까지 될 수 있습니다. 특정
    키가 정말 필요한 개별 테스트는 자기 안에서 직접 monkeypatch로 설정하면 됩니다.
    """
    for key in _SECRET_ENV_VARS:
        monkeypatch.delenv(key, raising=False)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """pytest 실행이 끝날 때마다 자동으로 호출되는 훅 - docs/테스트_결과.md를 재생성."""
    write_test_results_doc(terminalreporter.stats, exitstatus)
