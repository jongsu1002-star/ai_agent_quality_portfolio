from qa_agent.auth import hash_password, verify_password


def test_hash_password_roundtrip_verifies_correctly():
    hashed = hash_password("hunter2")
    assert verify_password("hunter2", hashed) is True


def test_hash_password_rejects_wrong_password():
    hashed = hash_password("hunter2")
    assert verify_password("wrong-guess", hashed) is False


def test_hash_password_produces_a_different_salt_each_time():
    first = hash_password("hunter2")
    second = hash_password("hunter2")
    assert first != second  # 매번 새 salt를 쓰므로 같은 비밀번호라도 해시 문자열은 달라야 함
    assert verify_password("hunter2", first) is True
    assert verify_password("hunter2", second) is True


def test_verify_password_rejects_malformed_stored_hash():
    assert verify_password("hunter2", "not-a-valid-hash") is False
    assert verify_password("hunter2", "") is False
