"""배포 직후 실행하는 검증 스크립트 - "컨테이너는 떠 있지만 실제로는 재배포가 반영 안 된"
사고를 잡아낸다(예: build 실패를 못 보고 지나쳐서 이전 이미지로 그냥 재시작된 경우,
헬스체크는 통과하지만 코드는 옛날 버전인 상태).

기존 app/main.py의 GET /health가 이미 `git_sha`(빌드 시 주입된 커밋)를 응답에 싣고 있으므로
(Dockerfile의 GIT_SHA 빌드 인자 참고), 이 값을 "지금 이 체크아웃의 HEAD"와 비교하는 것만으로
새 코드가 실제로 반영됐는지 확인할 수 있다 - 별도 버전 관리 체계를 새로 만들 필요가 없음.

실행 예시(운영, 재배포 직후):
    export GIT_SHA=$(git rev-parse HEAD)
    docker compose -f docker-compose.yml -f docker-compose.prod.yml build --build-arg GIT_SHA=$GIT_SHA qa-platform
    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
    python scripts/verify_deployment.py --expected-git-sha "$GIT_SHA"

종료 코드: 0=전부 통과, 1=하나라도 실패(배포 스크립트/CI가 이 값으로 롤백 여부를 판단할 것).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from typing import Optional

import requests

DEFAULT_BASE_URL = "http://localhost:8000"
DOCKER_HEALTHCHECK_CONTAINERS = [
    "qa-platform",
    "prometheus",
    "grafana",
]  # docker-compose.yml 서비스명 - docker compose ps로 실제 컨테이너 이름을 찾는다(프로젝트명이 달라도 동작)


def _detect_local_git_sha() -> Optional[str]:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True)
        return result.stdout.strip()
    except Exception:
        return None


def _check_health_endpoint(base_url: str, expected_git_sha: Optional[str], timeout: float, retries: int, retry_interval: float) -> list[str]:
    """/health가 200을 반환하고(+ git_sha가 기대값과 일치하는지)를 재시도하며 확인.

    컨테이너가 막 떠서 아직 준비 중일 수 있으므로 재시도 루프로 감싼다(즉시 1회만
    확인하면 정상 배포도 타이밍상 실패로 오판될 수 있음)."""
    errors: list[str] = []
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(f"{base_url}/health", timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(retry_interval)
            continue
        if response.status_code != 200:
            last_exc = RuntimeError(f"HTTP {response.status_code}")
            time.sleep(retry_interval)
            continue
        body = response.json()
        if body.get("status") not in ("ok", "degraded"):
            errors.append(f"/health status 필드가 비정상입니다: {body.get('status')!r}")
            return errors
        if expected_git_sha:
            actual = body.get("git_sha")
            if actual != expected_git_sha:
                errors.append(
                    f"git_sha 불일치 - 기대값(지금 체크아웃) {expected_git_sha!r} vs 서버 응답 {actual!r} "
                    "(이미지를 새로 빌드하지 않고 재시작했거나, 빌드 시 --build-arg GIT_SHA를 안 넘겼을 가능성)"
                )
        return errors
    errors.append(f"/health에 {retries}번 재시도했지만 응답을 받지 못했습니다: {last_exc}")
    return errors


def _check_docker_container_health() -> list[str]:
    """docker compose ps로 각 서비스의 실제 컨테이너 health 상태를 확인(선택적 - docker
    CLI가 없거나 이 호스트에서 compose로 안 띄운 환경이면 조용히 건너뜀, 필수 체크는
    위 HTTP 헬스체크임)."""
    warnings: list[str] = []
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Service}}\t{{.Health}}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return warnings  # docker CLI 자체가 없는 환경(로컬 개발 등) - 건너뜀
    if result.returncode != 0:
        return warnings
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        service, health = parts
        if service in DOCKER_HEALTHCHECK_CONTAINERS and health and health not in ("healthy", ""):
            warnings.append(f"컨테이너 '{service}'의 Docker 헬스체크 상태가 '{health}'입니다(healthy 아님)")
    return warnings


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"확인할 서버 주소 (기본: {DEFAULT_BASE_URL})")
    parser.add_argument("--expected-git-sha", default=None, help="이 값과 /health의 git_sha가 일치해야 함 (기본: 현재 체크아웃의 git rev-parse HEAD를 자동 사용)")
    parser.add_argument("--no-git-check", action="store_true", help="git_sha 비교를 건너뜀(체크아웃 밖에서 이미지만 빌드했을 때 등)")
    parser.add_argument("--retries", type=int, default=10, help="헬스체크 재시도 횟수 (기본 10)")
    parser.add_argument("--retry-interval", type=float, default=3.0, help="재시도 간격(초) (기본 3.0)")
    parser.add_argument("--timeout", type=float, default=5.0, help="요청 1건당 타임아웃(초) (기본 5.0)")
    args = parser.parse_args(argv)

    expected_git_sha = None if args.no_git_check else (args.expected_git_sha or _detect_local_git_sha())

    errors = _check_health_endpoint(args.base_url, expected_git_sha, args.timeout, args.retries, args.retry_interval)
    warnings = _check_docker_container_health()

    for warning in warnings:
        print(f"[verify_deployment] 경고: {warning}", file=sys.stderr)

    if errors:
        for error in errors:
            print(f"[verify_deployment] 실패: {error}", file=sys.stderr)
        return 1

    print(f"[verify_deployment] 통과: {args.base_url}/health 정상" + (f" (git_sha={expected_git_sha})" if expected_git_sha else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
