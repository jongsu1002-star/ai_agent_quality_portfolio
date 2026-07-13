import pytest

import app.main as main_module


@pytest.fixture(autouse=True)
def _reset_active_dataset(monkeypatch):
    """app.main은 import 시점에 실제 reports/.active_dataset.json을 읽어 ACTIVE_DATASET을
    복원한다(서버 재시작 후에도 사용자가 고른 데이터셋이 유지되게 하려는 의도). 하지만 이 때문에
    pytest 프로세스도 그 시점의 실제 활성 데이터셋을 그대로 물려받아, 기본 데모 데이터셋을
    전제로 하는 테스트(파이프라인 결과 구조 등)가 사용자의 실제 데이터셋 내용에 따라 깨질 수 있다.
    모든 테스트를 기본 데모 데이터셋(ACTIVE_DATASET=None) 기준으로 격리한다.

    ACTIVE_TESTCASE(발화문 전용, 데이터셋과 별도 관리)도 같은 이유로 격리 - 라이브 서버에서
    테스트 케이스를 업로드해두면 그 상태를 pytest가 그대로 물려받아 기본 데모 케이스와 id가
    안 맞아 케이스가 전부 걸러지는 문제가 있었다."""
    monkeypatch.setattr(main_module, "ACTIVE_DATASET", None)
    monkeypatch.setattr(main_module, "ACTIVE_DATASET_CASE_COUNT", 0)
    monkeypatch.setattr(main_module, "ACTIVE_TESTCASE", None)
    monkeypatch.setattr(main_module, "ACTIVE_TESTCASE_CASE_COUNT", 0)
