import requests

from scripts import verify_deployment


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def json(self):
        return self._json_body


def _fake_subprocess_run_no_docker(*args, **kwargs):
    raise FileNotFoundError("docker not found")


def test_main_returns_0_when_health_ok_and_git_sha_matches(monkeypatch):
    monkeypatch.setattr(
        verify_deployment.requests, "get",
        lambda url, timeout: _FakeResponse(200, {"status": "ok", "git_sha": "abc123"}),
    )
    monkeypatch.setattr(verify_deployment.subprocess, "run", _fake_subprocess_run_no_docker)
    assert verify_deployment.main(["--expected-git-sha", "abc123"]) == 0


def test_main_returns_1_when_git_sha_mismatches(monkeypatch, capsys):
    monkeypatch.setattr(
        verify_deployment.requests, "get",
        lambda url, timeout: _FakeResponse(200, {"status": "ok", "git_sha": "old-commit"}),
    )
    monkeypatch.setattr(verify_deployment.subprocess, "run", _fake_subprocess_run_no_docker)
    exit_code = verify_deployment.main(["--expected-git-sha", "new-commit"])
    assert exit_code == 1
    assert "git_sha 불일치" in capsys.readouterr().err


def test_main_skips_git_check_when_flag_set(monkeypatch):
    monkeypatch.setattr(
        verify_deployment.requests, "get",
        lambda url, timeout: _FakeResponse(200, {"status": "ok", "git_sha": "whatever"}),
    )
    monkeypatch.setattr(verify_deployment.subprocess, "run", _fake_subprocess_run_no_docker)
    assert verify_deployment.main(["--no-git-check"]) == 0


def test_main_fails_when_status_field_is_degraded_or_worse(monkeypatch, capsys):
    monkeypatch.setattr(
        verify_deployment.requests, "get",
        lambda url, timeout: _FakeResponse(200, {"status": "error", "git_sha": "abc"}),
    )
    monkeypatch.setattr(verify_deployment.subprocess, "run", _fake_subprocess_run_no_docker)
    exit_code = verify_deployment.main(["--no-git-check"])
    assert exit_code == 1
    assert "status" in capsys.readouterr().err


def test_main_retries_then_fails_when_server_unreachable(monkeypatch):
    call_count = {"n": 0}

    def _always_fail(url, timeout):
        call_count["n"] += 1
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(verify_deployment.requests, "get", _always_fail)
    monkeypatch.setattr(verify_deployment.subprocess, "run", _fake_subprocess_run_no_docker)
    monkeypatch.setattr(verify_deployment.time, "sleep", lambda _seconds: None)  # 테스트 속도를 위해 실제 대기 생략

    exit_code = verify_deployment.main(["--no-git-check", "--retries", "3", "--retry-interval", "0.01"])
    assert exit_code == 1
    assert call_count["n"] == 3


def test_main_recovers_after_transient_failures(monkeypatch):
    """처음 몇 번은 실패하다가 나중에 성공하면(컨테이너가 막 떠서 준비 중이던 경우) 전체
    결과는 성공이어야 한다."""
    call_count = {"n": 0}

    def _fail_twice_then_succeed(url, timeout):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise requests.ConnectionError("not ready yet")
        return _FakeResponse(200, {"status": "ok", "git_sha": "abc"})

    monkeypatch.setattr(verify_deployment.requests, "get", _fail_twice_then_succeed)
    monkeypatch.setattr(verify_deployment.subprocess, "run", _fake_subprocess_run_no_docker)
    monkeypatch.setattr(verify_deployment.time, "sleep", lambda _seconds: None)

    exit_code = verify_deployment.main(["--expected-git-sha", "abc", "--retries", "5", "--retry-interval", "0.01"])
    assert exit_code == 0
    assert call_count["n"] == 3


def test_docker_health_warning_does_not_fail_the_run(monkeypatch, capsys):
    """Docker 레벨 헬스체크 확인은 best-effort 경고일 뿐 - HTTP 헬스체크가 정상이면
    전체 결과는 여전히 성공해야 한다(docker CLI 부재/프로젝트명 불일치 등 오탐 가능성이
    있어 하드 실패로 다루지 않음)."""
    monkeypatch.setattr(
        verify_deployment.requests, "get",
        lambda url, timeout: _FakeResponse(200, {"status": "ok", "git_sha": "abc"}),
    )

    class _FakeCompletedProcess:
        returncode = 0
        stdout = "qa-platform\tunhealthy\nprometheus\thealthy\n"

    monkeypatch.setattr(verify_deployment.subprocess, "run", lambda *a, **k: _FakeCompletedProcess())

    exit_code = verify_deployment.main(["--expected-git-sha", "abc"])
    assert exit_code == 0
    assert "unhealthy" in capsys.readouterr().err


def test_detect_local_git_sha_returns_none_when_git_unavailable(monkeypatch):
    monkeypatch.setattr(verify_deployment.subprocess, "run", _fake_subprocess_run_no_docker)
    assert verify_deployment._detect_local_git_sha() is None
