import pytest

import app.main as main_module


@pytest.fixture(autouse=True)
def _reset_active_dataset(monkeypatch):
    """app.main은 import 시점에 실제 reports/.active_dataset.json을 읽어 "shared" 버킷의
    활성 데이터셋을 복원한다(서버 재시작 후에도 사용자가 고른 데이터셋이 유지되게 하려는 의도).
    하지만 이 때문에 pytest 프로세스도 그 시점의 실제 활성 데이터셋을 그대로 물려받아, 기본
    데모 데이터셋을 전제로 하는 테스트(파이프라인 결과 구조 등)가 사용자의 실제 데이터셋
    내용에 따라 깨질 수 있다. "shared" 버킷만 기본 데모 데이터셋 기준으로 재설정한다 -
    ACTIVE_DATASET 등은 이제 사용자별 dict라서, 전체를 통째로 덮어쓰면(예전 방식) 로그인
    계정별 격리를 검증하는 테스트(tests/test_auth.py)가 깨진다.

    ACTIVE_TESTCASE(발화문 전용, 데이터셋과 별도 관리)도 같은 이유로 "shared" 버킷만 격리 -
    라이브 서버에서 테스트 케이스를 업로드해두면 그 상태를 pytest가 그대로 물려받아 기본
    데모 케이스와 id가 안 맞아 케이스가 전부 걸러지는 문제가 있었다."""
    monkeypatch.setitem(main_module.ACTIVE_DATASET, main_module._SHARED_BUCKET, None)
    monkeypatch.setitem(main_module.ACTIVE_DATASET_CASE_COUNT, main_module._SHARED_BUCKET, 0)
    monkeypatch.setitem(main_module.ACTIVE_TESTCASE, main_module._SHARED_BUCKET, None)
    monkeypatch.setitem(main_module.ACTIVE_TESTCASE_CASE_COUNT, main_module._SHARED_BUCKET, 0)
