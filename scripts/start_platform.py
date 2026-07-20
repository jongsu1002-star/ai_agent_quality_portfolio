"""플랫폼 전체를 한 번에 띄우는 편의 스크립트 - 모니터링 애드온의 Prometheus/Grafana를
Docker로 먼저 기동한 뒤, 기존 FastAPI 앱을 uvicorn으로 실행합니다.

`python -m uvicorn app.main:app ...`를 대체하는 게 아니라 "한 번에 같이 뜨면 편한" 사람을
위한 선택 사항입니다 - Docker CLI가 없거나 데몬이 꺼져 있어도 이 스크립트는 계속 진행해서
FastAPI 앱은 정상적으로 띄웁니다(Prometheus/Grafana만 조용히 건너뜀). 즉, 이 스크립트가
실패해도 기존 서비스 실행에는 영향이 없습니다 - 애드온 전체의 "기존 서비스 무영향" 원칙과
동일합니다.

실행: python scripts/start_platform.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 콘솔의 기본 코드페이지는 한글을 표현하지 못해 아래 print()들이 깨져(�) 보이는
# 문제가 있었음 - 이 프로세스 자신의 출력도 고치고, PYTHONUTF8=1을 자식 uvicorn
# 프로세스에도 물려줘서(subprocess.run은 기본적으로 현재 환경변수를 상속함) 그쪽 로그도
# 같은 문제를 겪지 않게 한다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).resolve().parents[1]


def _start_monitoring_stack() -> None:
    """Prometheus/Grafana를 best-effort로 기동 - 실패해도 예외를 밖으로 던지지 않음."""
    if shutil.which("docker") is None:
        print("[start] docker CLI를 찾을 수 없어 Prometheus/Grafana 기동을 건너뜁니다.", flush=True)
        return

    compose_file = ROOT / "infra" / "docker-compose.monitoring.yml"
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "up", "-d"],
            cwd=str(ROOT),
            timeout=60,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("[start] Prometheus/Grafana 기동 완료 (http://localhost:9090, http://localhost:3000)", flush=True)
        else:
            print(f"[start] Prometheus/Grafana 기동 실패 - 계속 진행합니다: {result.stderr.strip()[:300]}", flush=True)
    except Exception as exc:
        print(f"[start] Prometheus/Grafana 기동 중 오류 - 계속 진행합니다: {exc}", flush=True)


def main() -> None:
    _start_monitoring_stack()

    host, port = "0.0.0.0", "8000"
    print(f"[start] FastAPI 앱을 http://{host}:{port} 에서 시작합니다 (Ctrl+C로 종료)...", flush=True)
    subprocess.run([sys.executable, "-m", "uvicorn", "app.main:app", "--host", host, "--port", port])


if __name__ == "__main__":
    main()
