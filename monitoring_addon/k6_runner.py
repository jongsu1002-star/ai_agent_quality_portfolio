"""웹 UI에서 k6 성능테스트를 직접 실행하는 기능.

대상 URL은 이 서버 자신이나 사내망(사설 IP 대역)으로만 제한합니다 - 안 그러면 이 웹 버튼이
임의의 외부(공인) 주소를 대상으로 부하를 거는 도구가 되어버리기 때문입니다. 동시사용자수/
지속시간도 상한을 둬서 실수로(혹은 악의적으로) 과도한 부하테스트가 걸리는 것을 막습니다.

실행은 백그라운드 스레드 + 전역 락으로 관리해 한 번에 하나만 돌게 합니다(동시 실행 시
결과가 서로 섞이거나 이 서버 자체에 과부하가 걸리는 것을 방지).
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
from urllib.parse import urlparse

MAX_VUS = 50
MAX_DURATION_SECONDS = 120  # 웹 트리거는 최대 2분까지만 - CLI로 직접 돌리는 건 이 제한이 없음
_DURATION_RE = re.compile(r"^(\d+)(s|m)$")

ROOT = Path(__file__).resolve().parents[1]
K6_SCRIPT = ROOT / "tests" / "k6" / "load_test.js"


def _is_private_host(hostname: str) -> bool:
    """localhost 또는 사설/루프백 IP 리터럴만 허용.

    도메인 이름(IP 리터럴이 아닌 것)은 DNS 조회 없이 사설망인지 안전하게 판단할 수 없으므로
    (SSRF 우회 위험) 무조건 거부합니다 - "localhost"만 이름으로 허용하는 특례입니다.
    """
    if hostname.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def validate_target_url(url: str) -> Optional[str]:
    """유효하지 않으면 에러 메시지를 반환, 유효하면 None."""
    if not url:
        return "대상 URL을 입력해주세요"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "URL은 http:// 또는 https://로 시작해야 합니다"
    if not parsed.hostname:
        return "URL에 호스트가 없습니다"
    if not _is_private_host(parsed.hostname):
        return "대상은 localhost 또는 사내망 IP(10.x/172.16-31.x/192.168.x)만 허용됩니다 - 외부 인터넷 주소는 거부됩니다"
    return None


def validate_duration(duration: str) -> Optional[str]:
    match = _DURATION_RE.match((duration or "").strip())
    if not match:
        return "지속시간은 '30s' 또는 '2m' 같은 형식이어야 합니다"
    value, unit = int(match.group(1)), match.group(2)
    seconds = value * 60 if unit == "m" else value
    if seconds <= 0 or seconds > MAX_DURATION_SECONDS:
        return f"지속시간은 1초~{MAX_DURATION_SECONDS}초(2분) 사이여야 합니다"
    return None


def validate_vus(vus: int) -> Optional[str]:
    if vus < 1 or vus > MAX_VUS:
        return f"동시사용자수는 1~{MAX_VUS} 사이여야 합니다"
    return None


def validate_path(path: str) -> Optional[str]:
    """대상 URL 뒤에 붙일 요청 경로 - 기본값 /는 대부분의 서버에서 응답하지만, 이 플랫폼
    자신을 테스트할 때는 더 가벼운 /health를 써도 됨. 다른 내부 서비스를 테스트하려면
    그 서비스가 실제로 갖고 있는 경로를 지정해야 함."""
    if not path:
        return "경로를 입력해주세요 (예: /, /health)"
    if not path.startswith("/"):
        return "경로는 '/'로 시작해야 합니다"
    if any(ch in path for ch in (" ", "\n", "\r", "\t")):
        return "경로에 공백이나 줄바꿈을 포함할 수 없습니다"
    return None


MAX_UTTERANCE_LEN = 2000


def validate_utterance(utterance: str) -> Optional[str]:
    """비어있으면 기존처럼 단순 GET 체크 - 값이 있으면 그 값을 POST 바디로 실어 보내
    실제 챗봇 질의응답 엔드포인트를 부하테스트함."""
    if utterance and len(utterance) > MAX_UTTERANCE_LEN:
        return f"발화문은 {MAX_UTTERANCE_LEN}자를 넘을 수 없습니다"
    return None


class K6RunManager:
    """단일 k6 실행만 허용(동시 실행 방지) - 백그라운드 스레드로 실행하고 상태를 추적."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state: Dict[str, Any] = {"status": "idle"}

    def is_running(self) -> bool:
        return self._state.get("status") == "running"

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(
        self,
        target_url: str,
        vus: int,
        duration: str,
        path: str = "/",
        utterance: str = "",
        request_field: str = "message",
    ) -> Optional[str]:
        """실행을 시작. 이미 실행 중이면 에러 메시지를 반환(시작 안 함), 성공하면 None."""
        with self._lock:
            if self.is_running():
                return "이미 실행 중인 성능테스트가 있습니다 - 끝난 뒤 다시 시도하세요"
            k6_path = shutil.which("k6")
            if not k6_path:
                return "이 서버에서 k6 실행 파일을 찾을 수 없습니다 (PATH에 k6가 설치되어 있어야 함)"
            self._state = {
                "status": "running",
                "started_at": time.time(),
                "target_url": target_url,
                "path": path,
                "utterance": utterance or None,
                "vus": vus,
                "duration": duration,
                "output_tail": "",
                "error": None,
            }

        thread = threading.Thread(
            target=self._run, args=(k6_path, target_url, vus, duration, path, utterance, request_field), daemon=True
        )
        thread.start()
        return None

    def _run(
        self,
        k6_path: str,
        target_url: str,
        vus: int,
        duration: str,
        path: str = "/",
        utterance: str = "",
        request_field: str = "message",
    ) -> None:
        env = dict(os.environ)
        env["LOAD_TARGET_URL"] = target_url
        env["LOAD_VUS"] = str(vus)
        env["LOAD_DURATION"] = duration
        env["LOAD_PATH"] = path
        env["LOAD_UTTERANCE"] = utterance or ""
        env["LOAD_REQUEST_FIELD"] = request_field or "message"
        try:
            result = subprocess.run(
                [k6_path, "run", str(K6_SCRIPT)],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=MAX_DURATION_SECONDS + 60,  # k6 자체 종료 유예시간까지 감안한 여유
            )
            with self._lock:
                self._state["status"] = "done"
                self._state["exit_code"] = result.returncode
                self._state["output_tail"] = ((result.stdout or "") + "\n" + (result.stderr or ""))[-2000:]
        except Exception as exc:
            with self._lock:
                self._state["status"] = "error"
                self._state["error"] = str(exc)


K6_RUN_MANAGER = K6RunManager()  # 프로세스 전체가 공유하는 싱글턴 (동시 실행 방지용 락 포함)
