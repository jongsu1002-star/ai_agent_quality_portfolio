"""FastAPI 웹앱 진입점 - qa_agent 코어 엔진을 REST API + 대시보드로 노출.

여기서는 새 채점 로직을 만들지 않습니다 - 전부 qa_agent.pipeline.PipelineOrchestrator에
위임하고, 이 파일은 HTTP 요청/응답 변환과 실행 상태 관리(RUN_REGISTRY)만 담당합니다.
"""

import dataclasses
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Dict, List, Optional, Tuple

# Windows 콘솔의 기본 코드페이지는 한글을 표현하지 못해, uvicorn/앱 로그의 한글
# 메시지가 문자 단위로 깨져(�) 출력되는 문제가 있었음(파일 자체는 항상 UTF-8이라
# 소스/문서는 멀쩡함 - 콘솔에 "쓸 때"만 깨짐). uvicorn 기본 로그 핸들러도 이
# sys.stdout/stderr 객체를 그대로 재사용하므로, 앱이 로드되는 시점에 한 번만 맞춰두면
# 이후 모든 로그에 적용된다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

load_dotenv()  # 로컬 실행 시 .env를 읽어 os.environ에 반영 (Docker는 env_file로 동일한 역할을 함)

from monitoring.external_monitor import ExternalMonitorRegistry, run_monitor_loop
from monitoring.healthcheck import HealthChecker
from monitoring.metrics_collector import MetricsCollector

try:
    # 모니터링 애드온(k6/SQLite 이력/Prometheus)의 feature flag - 이 import 자체가 깨져도
    # (예: 패키지 손상) 기존 서비스는 애드온이 그냥 꺼진 것처럼 계속 정상 기동해야 함
    from monitoring_addon.config import (
        GRAFANA_LINK_ENABLED,
        K6_HISTORY_ENABLED,
        MONITORING_ADDON_DB_ENABLED,
        MONITORING_ADDON_ENABLED,
        PROMETHEUS_ADDON_ENABLED,
    )
except Exception as _addon_config_error:
    print(f"[monitoring-addon] config load failed, addon disabled: {_addon_config_error}")
    MONITORING_ADDON_ENABLED = False
    MONITORING_ADDON_DB_ENABLED = False
    PROMETHEUS_ADDON_ENABLED = False
    K6_HISTORY_ENABLED = False
    GRAFANA_LINK_ENABLED = False

from qa_agent import error_log
from qa_agent.board import BoardStore
from qa_agent.config_loader import Config
from qa_agent.excel_io import build_template_workbook, build_testcase_template_workbook, load_dataset, load_testcase
from qa_agent.jira_notifier import JiraNotifier
from qa_agent.models import GoldenCase
from qa_agent.pipeline import ALL_TECHNIQUES, PipelineOrchestrator
from qa_agent.reporter import list_run_history, write_defect_report_doc, write_reports
from qa_agent.slack_notifier import DiscordNotifier, SlackNotifier, TeamsNotifier
from qa_agent.users import UserStore


def get_local_ip() -> Optional[str]:
    """Best-effort LAN-facing IP, for telling teammates which address to use.

    Opens a UDP "connection" (no packets sent, just picks the outbound route)
    rather than parsing interface lists, so it works the same on Windows/macOS/Linux.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def _print_network_access_info() -> None:
    """서버 시작 시 콘솔에 접속 가능한 주소(로컬/네트워크)를 출력."""
    local_ip = get_local_ip()
    print("=" * 60)
    print("AI Agent 품질관리·운영 모니터링 플랫폼")
    print("  Local:   http://localhost:8000       (이 PC에서 접속)")
    if local_ip:
        print(f"  Network: http://{local_ip}:8000    (다른 PC에서 접속 시 사용)")
    else:
        print("  Network: 로컬 IP 확인 실패 - 'ipconfig'(Windows) / 'ip addr'(Linux)로 직접 확인하세요")
    print("  자세한 안내: docs/팀원용_접속가이드.md")
    print("=" * 60)


_external_monitor_thread: Optional[Thread] = None
_external_monitor_stop_event = Event()


def _ensure_external_monitor_thread_started() -> None:
    """외부 대상 체크 백그라운드 스레드를 (한 번만) 시작.

    lifespan은 테스트에서 TestClient(app)을 여러 번 만들 때마다 다시 호출될 수 있어서,
    is_alive() 체크로 중복 스레드가 쌓이지 않게 막습니다.
    """
    global _external_monitor_thread
    if _external_monitor_thread is not None and _external_monitor_thread.is_alive():
        return
    _external_monitor_thread = Thread(target=run_monitor_loop, args=(EXTERNAL_MONITOR, _external_monitor_stop_event), daemon=True)
    _external_monitor_thread.start()


_monitoring_addon_thread: Optional[Thread] = None
_monitoring_addon_stop_event = Event()


def _ensure_monitoring_addon_thread_started() -> None:
    """모니터링 애드온의 스냅샷 저장 스레드를 (한 번만) 시작 - 애드온이 꺼져 있으면 아무것도 안 함.

    external_monitor와 동일한 is_alive() 가드 패턴. 애드온 자체가 문제를 일으켜도 기존 서비스가
    영향받지 않도록 전체를 try/except로 감싸고, 실패하면 조용히 건너뜁니다.
    """
    if not MONITORING_ADDON_ENABLED or not MONITORING_ADDON_DB_ENABLED:
        return
    global _monitoring_addon_thread
    try:
        if _monitoring_addon_thread is not None and _monitoring_addon_thread.is_alive():
            return
        from monitoring_addon.snapshot_service import run_snapshot_loop

        _monitoring_addon_thread = Thread(
            target=run_snapshot_loop,
            args=(MONITORING_ADDON_DB, METRICS, HealthChecker(), _monitoring_addon_stop_event),
            daemon=True,
        )
        _monitoring_addon_thread.start()
    except Exception as e:
        print(f"[monitoring-addon] snapshot thread start skipped: {e}")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """서버가 실제로 요청을 받기 시작할 때(startup) 접속 안내 출력 + 백그라운드 스레드 시작."""
    _print_network_access_info()
    _ensure_external_monitor_thread_started()
    _ensure_monitoring_addon_thread_started()
    yield


def _detect_git_sha() -> Optional[str]:
    """빌드/배포 시점의 커밋을 추적용으로 기록 - 실패해도(git 없음/얕은 clone 등) 서버 기동을
    막지 않도록 예외를 전부 삼키고 None을 반환한다.

    Docker 이미지는 .dockerignore가 .git을 빌드 컨텍스트에서 제외하므로 컨테이너 안에서는
    git 명령 자체가 항상 실패한다 - Dockerfile이 빌드 시점에 호스트의 커밋을 GIT_SHA
    환경변수로 구워 넣으므로 그 값을 최우선으로 쓰고, 없을 때만(로컬 개발 실행) git
    서브프로세스로 폴백한다."""
    env_value = os.environ.get("GIT_SHA")
    if env_value and env_value != "unknown":
        return env_value
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.stdout.strip() or None if result.returncode == 0 else None
    except Exception:
        return None


SERVER_STARTED_AT = datetime.now().isoformat()  # 이 프로세스가 기동된 시각 - "이 결과가 최신 코드로
                                                  # 만들어졌는가"를 판단하려면 결과 생성 시각과 비교 필요
GIT_SHA = _detect_git_sha()  # 이 프로세스가 구동 중인 소스의 커밋 - 결과가 어느 코드 버전에서
                              # 나온 것인지 사후에 추적하기 위함(reports/voc_analysis/*.json에도 함께 기록)

app = FastAPI(title="AI Agent Quality Platform", lifespan=_lifespan)
METRICS = MetricsCollector()  # 모니터링 탭이 조회하는 서버 운영 지표(요청수/응답시간/에러율) 싱글턴
EXTERNAL_MONITOR = ExternalMonitorRegistry(path=str(Path("reports") / "monitoring_targets.json"))  # 외부 URL 합성 모니터링 대상 저장소
USER_STORE = UserStore(path=str(Path("data") / "users.db"))  # 계정(가입/승인/역할) 저장소 - monitoring_addon과 같은 SQLite 패턴
BOARD_STORE = BoardStore(path=str(Path("data") / "board.db"))  # 게시판(일반/FAQ/VOC) + 댓글 저장소

MONITORING_ADDON_DB = None  # 모니터링 애드온 전용 SQLite (기존 어떤 저장소도 대체하지 않음)
if MONITORING_ADDON_ENABLED and MONITORING_ADDON_DB_ENABLED:
    try:
        from monitoring_addon.db import MonitoringAddonDB

        MONITORING_ADDON_DB = MonitoringAddonDB(path=str(Path("data") / "monitoring_addon.db"))
    except Exception as e:
        print(f"[monitoring-addon] DB init skipped: {e}")


@app.middleware("http")
async def _record_request_metrics(request: Request, call_next):
    """모든 HTTP 요청 1건의 소요시간/상태코드를 METRICS에 기록.

    기존 QA 파이프라인/리포트 기능과는 완전히 별개인 부가 관측 기능이므로, 기록 자체가
    실패하더라도(예: 예상 밖 예외) 실제 응답에는 절대 영향을 주면 안 됨 - 그래서 기록
    실패는 조용히 무시하고 원래 응답을 그대로 돌려줍니다.
    """
    started = time.perf_counter()
    response = await call_next(request)
    try:
        duration_ms = (time.perf_counter() - started) * 1000
        METRICS.record(request.method, request.url.path, response.status_code, duration_ms)
    except Exception:
        pass
    return response


SESSION_COOKIE_NAME = "qa_session"
_ACTIVE_SESSIONS: Dict[str, str] = {}  # 세션 토큰 -> username. 메모리 저장이라 서버 재시작 시 전부 무효화(재로그인 필요)
_SHARED_BUCKET = "shared"  # 계정이 하나도 없는(=로그인이 꺼진) 상태에서 모두가 공유하는 고정 버킷 - 기존 단일사용자 동작과 완전히 동일한 경로를 그대로 씀
_AUTH_EXEMPT_PATHS = {"/login", "/logout", "/signup", "/health", "/metrics-addon", "/api/auth/status", "/api/signup/requires-setup-code"}  # 로그인 없이 항상 허용
_SAFE_NEXT_PATTERN = re.compile(r"^/[A-Za-z0-9/_\-]*$")
_SAFE_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")

LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300  # 5분
_LOGIN_ATTEMPTS: Dict[str, Dict[str, float]] = {}  # 클라이언트 IP -> {"count", "locked_until"}. 브루트포스 방어용, 세션과 같은 이유로 프로세스 메모리에만 둠
_LOGIN_ATTEMPTS_LOCK = Lock()


def _login_rate_limit_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_login_rate_limit(request: Request) -> Optional[JSONResponse]:
    """실패가 누적된 IP를 잠금 - 아이디 유무와 무관하게 IP 기준이라 계정 열거 시도도 함께 막음."""
    key = _login_rate_limit_key(request)
    with _LOGIN_ATTEMPTS_LOCK:
        entry = _LOGIN_ATTEMPTS.get(key)
        if entry and entry["locked_until"] > time.time():
            retry_after = int(entry["locked_until"] - time.time()) + 1
            return JSONResponse(
                {"error": f"로그인 시도가 너무 많습니다. {retry_after}초 후 다시 시도하세요"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
    return None


def _record_login_failure(request: Request) -> None:
    key = _login_rate_limit_key(request)
    with _LOGIN_ATTEMPTS_LOCK:
        entry = _LOGIN_ATTEMPTS.setdefault(key, {"count": 0.0, "locked_until": 0.0})
        entry["count"] += 1
        if entry["count"] >= LOGIN_MAX_ATTEMPTS:
            entry["locked_until"] = time.time() + LOGIN_LOCKOUT_SECONDS
            entry["count"] = 0.0


def _record_login_success(request: Request) -> None:
    with _LOGIN_ATTEMPTS_LOCK:
        _LOGIN_ATTEMPTS.pop(_login_rate_limit_key(request), None)


def _current_username(request: Request) -> str:
    """로그인 세션에 매인 아이디, 없으면(또는 계정 시스템 자체가 꺼져있으면) 공용 버킷."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and token in _ACTIVE_SESSIONS:
        return _ACTIVE_SESSIONS[token]
    return _SHARED_BUCKET


def _is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return bool(token) and token in _ACTIVE_SESSIONS


def _is_admin(request: Request) -> bool:
    username = _current_username(request)
    if username == _SHARED_BUCKET:
        return False
    user = USER_STORE.get_user(username)
    return bool(user and user["role"] == "admin")


def _is_admin_effective(request: Request) -> bool:
    """게시판 삭제 등 "관리자만" 판단에 쓰는 실질 관리자 체크.

    _require_admin과 동일한 이유로, 계정이 하나도 없는 LAN 모드에서는 접속자 전원이 사실상
    단일 운영자이므로 True를 돌려줌 - 그렇지 않으면 그 모드에서 아무도 게시글을 삭제할 수
    없는 회귀가 생김."""
    if not USER_STORE.has_any_users():
        return True
    return _is_admin(request)


def _require_admin(request: Request) -> Optional[JSONResponse]:
    """관리자 전용 API 맨 앞에서 호출 - 문제 없으면 None, 막아야 하면 바로 반환할 응답.

    계정 시스템 자체가 꺼져있는 LAN 모드(가입자 0명)에서는 로그인도 강제하지 않는
    것과 같은 이유로 관리자 체크도 건너뜀(그 모드에선 접속자 전원이 사실상 단일 운영자)."""
    if not USER_STORE.has_any_users():
        return None
    if not _is_admin(request):
        return JSONResponse({"error": "관리자만 접근할 수 있습니다"}, status_code=403)
    return None


def _safe_next_path(value: str) -> str:
    """로그인 후 돌아갈 경로 - 오픈리다이렉트/XSS 방지를 위해 "/"로 시작하는 단순 상대 경로만 허용."""
    value = value or "/"
    if value.startswith("//") or not _SAFE_NEXT_PATTERN.match(value):
        return "/"
    return value


@app.middleware("http")
async def _require_login(request: Request, call_next):
    """계정이 하나도 없으면(기본값, LAN 모드) 이 미들웨어는 통과만 시킴. 계정이 하나라도
    생기는 순간(=누군가 회원가입)부터 전체 로그인이 강제됨."""
    if not USER_STORE.has_any_users():
        return await call_next(request)
    path = request.url.path
    if path in _AUTH_EXEMPT_PATHS or _is_authenticated(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "로그인이 필요합니다"}, status_code=401)
    next_path = _safe_next_path(path + (f"?{request.url.query}" if request.url.query else ""))
    return RedirectResponse(url=f"/login?next={next_path}")


RUN_REGISTRY: Dict[str, Dict[str, Dict[str, Any]]] = {}  # username -> run_id -> {status, progress, result, error, jira_tickets}
RUN_LOCK = Lock()  # 백그라운드 스레드에서 RUN_REGISTRY를 건드릴 때 쓰는 락
ACTIVE_DATASET: Dict[str, Optional[str]] = {}  # username -> 현재 선택된 데이터셋 파일 경로 (없으면 기본 데모 케이스 사용)
ACTIVE_DATASET_CASE_COUNT: Dict[str, int] = {}
SETTINGS_PATH = Path("reports") / "settings.json"  # 알림/Jira/LLM 설정은 계정별로 안 나누고 전역 공유(사용자 요청)
SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
SHARED_REPORTS_ROOT = Path("reports")  # "shared" 버킷(계정 시스템 꺼짐/미로그인)이 쓰는 기존 reports/ 최상위 - 테스트가 이 상수 하나만 monkeypatch하면 전체 격리됨
USER_DATA_ROOT = Path("reports") / "users"  # 실명 계정별 데이터셋/테스트케이스/실행이력 루트. "shared" 버킷은 여기 안 쓰고 SHARED_REPORTS_ROOT를 그대로 씀
DATASET_HISTORY_LIMIT = 30  # 이력이 무한정 커지지 않도록 최근 N건만 유지

# 테스트 케이스(발화문) - 데이터셋(정답)과 완전히 별도로 관리되는 두 번째 파일 세트.
# 데이터셋 = category/golden_answer 등 "기준값", 테스트 케이스 = id/question만 있는 "질문".
# 둘 다 안 올리면 기존처럼 데이터셋 파일 자체의 question이 그대로 쓰임(하위호환) - 테스트
# 케이스를 올리면 id가 일치하는 데이터셋 케이스의 question만 그 발화문으로 덮어써서 실행됨.
ACTIVE_TESTCASE: Dict[str, Optional[str]] = {}
ACTIVE_TESTCASE_CASE_COUNT: Dict[str, int] = {}
TESTCASE_HISTORY_LIMIT = 30


def _user_reports_dir(username: str) -> Path:
    """이 사용자의 데이터셋/테스트케이스/실행이력이 저장되는 루트.

    "shared"(계정 시스템이 꺼져있거나 아무도 로그인 안 한 상태)는 기존과 완전히 같은
    reports/ 최상위를 그대로 쓰고, 실명 계정만 reports/users/{username}/ 아래로 격리됨 -
    그래야 계정을 하나도 안 쓰는 기존 배포(이 리포지토리 자체 포함)가 회귀 없이 그대로 동작함.
    """
    if username == _SHARED_BUCKET:
        path = SHARED_REPORTS_ROOT
    else:
        path = USER_DATA_ROOT / username
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dataset_dir(username: str) -> Path:
    path = _user_reports_dir(username) / "datasets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _testcase_dir(username: str) -> Path:
    path = _user_reports_dir(username) / "testcases"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dataset_history_path(username: str) -> Path:
    return _dataset_dir(username) / ".history.json"


def _testcase_history_path(username: str) -> Path:
    return _testcase_dir(username) / ".history.json"


def _active_dataset_pointer(username: str) -> Path:
    return _user_reports_dir(username) / ".active_dataset.json"


def _active_testcase_pointer(username: str) -> Path:
    return _user_reports_dir(username) / ".active_testcase.json"


def _normalize_stored_path(raw: str) -> str:
    """저장된 경로 문자열을 항상 '/' 구분자로 정규화.

    이 값들은 pointer/history 파일(JSON)에 그대로 저장되는데, Windows에서 네이티브로 돌릴 때
    쓰인 값(예: "reports\\datasets\\x.json")을 나중에 Docker(Linux) 컨테이너에서 그대로 읽으면
    백슬래시가 경로 구분자로 해석되지 않아 파일을 못 찾는다(반대 방향도 마찬가지). '/'는 Windows
    Python에서도 정상적으로 경로 구분자로 인식되므로, 어느 쪽에서 쓰였든 항상 '/'로 통일해두면
    호스트 OS와 무관하게 안전하게 읽고 쓸 수 있다.
    """
    return raw.replace("\\", "/")


def _restore_active_dataset(username: str) -> None:
    """서버가 (재)시작될 때 이전에 선택돼 있던 데이터셋을 이 사용자의 포인터 파일에서 복원."""
    pointer_path = _active_dataset_pointer(username)
    if not pointer_path.exists():
        return
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        path = pointer.get("path")
        if path:
            path = _normalize_stored_path(path)
        if path and Path(path).exists():
            ACTIVE_DATASET[username] = path
            ACTIVE_DATASET_CASE_COUNT[username] = len(load_dataset(path))
    except Exception:
        pass


def _restore_active_testcase(username: str) -> None:
    """서버가 (재)시작될 때 이전에 선택돼 있던 테스트 케이스를 이 사용자의 포인터 파일에서 복원."""
    pointer_path = _active_testcase_pointer(username)
    if not pointer_path.exists():
        return
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        path = pointer.get("path")
        if path:
            path = _normalize_stored_path(path)
        if path and Path(path).exists():
            ACTIVE_TESTCASE[username] = path
            ACTIVE_TESTCASE_CASE_COUNT[username] = len(load_testcase(path))
    except Exception:
        pass


def _known_usernames() -> List[str]:
    """지금까지 데이터가 있었던 사용자 버킷 이름 목록 - 서버 기동 시 각자의 활성
    데이터셋/테스트케이스를 복원하기 위해 순회하는 용도."""
    names = {_SHARED_BUCKET}
    if USER_DATA_ROOT.exists():
        names.update(p.name for p in USER_DATA_ROOT.iterdir() if p.is_dir())
    return sorted(names)


for _username in _known_usernames():
    _restore_active_dataset(_username)
    _restore_active_testcase(_username)


def _load_dataset_history(username: str) -> List[Dict[str, Any]]:
    """지금까지 업로드된 데이터셋 이력을 읽어옴 (없으면 빈 리스트)."""
    history_path = _dataset_history_path(username)
    if not history_path.exists():
        return []
    try:
        return json.loads(history_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_dataset_history(username: str, entry: Dict[str, Any]) -> None:
    """업로드 이력에 항목 1건을 추가하고 최근 DATASET_HISTORY_LIMIT건만 남김."""
    history = _load_dataset_history(username)
    history.append(entry)
    _dataset_history_path(username).write_text(json.dumps(history[-DATASET_HISTORY_LIMIT:], indent=2, ensure_ascii=False), encoding="utf-8")


def _set_active_dataset(username: str, path: Path, case_count: int) -> None:
    """현재 활성 데이터셋을 바꾸고, 서버 재시작에도 유지되도록 포인터 파일에 기록."""
    normalized = _normalize_stored_path(str(path))
    ACTIVE_DATASET[username] = normalized
    ACTIVE_DATASET_CASE_COUNT[username] = case_count
    _active_dataset_pointer(username).write_text(json.dumps({"path": normalized}), encoding="utf-8")


def _load_testcase_history(username: str) -> List[Dict[str, Any]]:
    history_path = _testcase_history_path(username)
    if not history_path.exists():
        return []
    try:
        return json.loads(history_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_testcase_history(username: str, entry: Dict[str, Any]) -> None:
    history = _load_testcase_history(username)
    history.append(entry)
    _testcase_history_path(username).write_text(json.dumps(history[-TESTCASE_HISTORY_LIMIT:], indent=2, ensure_ascii=False), encoding="utf-8")


def _set_active_testcase(username: str, path: Path, case_count: int) -> None:
    normalized = _normalize_stored_path(str(path))
    ACTIVE_TESTCASE[username] = normalized
    ACTIVE_TESTCASE_CASE_COUNT[username] = case_count
    _active_testcase_pointer(username).write_text(json.dumps({"path": normalized}), encoding="utf-8")


def _admin_setup_code() -> str:
    """ADMIN_SETUP_CODE 환경변수(선택) - 설정해두면 서비스 최초 가입 시 이 코드를 아는 사람만
    자동 관리자가 될 수 있음. 공개 도메인 배포 전 계정이 하나도 없는 틈을 타 외부인이 먼저
    가입해 관리자를 선점하는 것을 막기 위한 용도. 비워두면(기본값) 기존과 동일하게 동작."""
    return os.getenv("ADMIN_SETUP_CODE", "").strip()


@app.get("/signup", response_class=HTMLResponse)
def signup_page() -> HTMLResponse:
    html_path = Path(__file__).with_name("templates").joinpath("signup.html")
    return HTMLResponse(html_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/api/signup/requires-setup-code")
def signup_requires_setup_code() -> JSONResponse:
    """가입 화면이 '관리자 설정 코드' 입력란을 보여줘야 하는지 - ADMIN_SETUP_CODE가 설정돼
    있고 아직 계정이 하나도 없을 때(=지금 가입하면 자동 관리자가 될 때)만 true."""
    required = bool(_admin_setup_code()) and not USER_STORE.has_any_users()
    return JSONResponse({"required": required})


@app.post("/signup")
def signup_submit(payload: Dict[str, Any]) -> JSONResponse:
    """가입 신청 - 서비스에 계정이 하나도 없을 때(최초 가입)만 자동으로 관리자 승인되어
    바로 로그인 상태가 되고, 그 외에는 항상 대기 상태로 시작해 관리자 승인을 거침.

    ADMIN_SETUP_CODE가 설정된 경우, 최초 가입은 코드가 일치해야만 진행됨(계정 선점 방지) -
    나머지(두 번째 가입자부터)는 원래대로 대기 상태로 시작하므로 코드가 필요 없음."""
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not _SAFE_USERNAME_PATTERN.match(username):
        return JSONResponse({"error": "아이디는 영문/숫자/_/- 3~32자여야 합니다"}, status_code=400)
    if len(password) < 4:
        return JSONResponse({"error": "비밀번호는 4자 이상이어야 합니다"}, status_code=400)
    setup_code = _admin_setup_code()
    if setup_code and not USER_STORE.has_any_users():
        submitted_code = str(payload.get("setup_code", ""))
        if not secrets.compare_digest(submitted_code, setup_code):
            return JSONResponse({"error": "관리자 설정 코드가 올바르지 않습니다"}, status_code=403)
    user = USER_STORE.create_user(username, password)
    if not user:
        return JSONResponse({"error": "이미 사용 중인 아이디입니다"}, status_code=409)
    if user["status"] != "approved":
        return JSONResponse({"ok": True, "status": "pending"})
    token = secrets.token_urlsafe(32)
    _ACTIVE_SESSIONS[token] = username
    response = JSONResponse({"ok": True, "status": "approved"})
    response.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/") -> HTMLResponse:
    """로그인 화면 - 계정 시스템이 꺼져있어도(가입자 0명) 항상 조회는 가능하지만, 그
    경우 미들웨어가 애초에 이 경로로 리다이렉트하지 않으므로 사실상 안 쓰임."""
    html_path = Path(__file__).with_name("templates").joinpath("login.html")
    html = html_path.read_text(encoding="utf-8").replace("__NEXT__", _safe_next_path(next))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.post("/login")
def login_submit(payload: Dict[str, Any], request: Request) -> JSONResponse:
    if not USER_STORE.has_any_users():
        return JSONResponse({"ok": True})
    limited = _check_login_rate_limit(request)
    if limited:
        return limited
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    user = USER_STORE.get_user(username)
    if not user:
        _record_login_failure(request)
        return JSONResponse({"error": "존재하지 않는 아이디입니다"}, status_code=401)
    if user["status"] == "pending":
        return JSONResponse({"error": "아직 관리자 승인 대기 중입니다"}, status_code=403)
    if not USER_STORE.verify_login(username, password):
        _record_login_failure(request)
        return JSONResponse({"error": "비밀번호가 올바르지 않습니다"}, status_code=401)
    _record_login_success(request)
    token = secrets.token_urlsafe(32)
    _ACTIVE_SESSIONS[token] = username
    response = JSONResponse({"ok": True})
    response.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


@app.post("/logout")
def logout(request: Request) -> JSONResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        _ACTIVE_SESSIONS.pop(token, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/api/auth/status")
def auth_status(request: Request) -> JSONResponse:
    """대시보드가 로그아웃 버튼/사용자 관리 탭을 보여줄지 판단하는 용도(인증 자체는 미들웨어가 이미 강제함)."""
    enabled = USER_STORE.has_any_users()
    authenticated = (not enabled) or _is_authenticated(request)
    username = _current_username(request) if enabled and authenticated else None
    return JSONResponse({"enabled": enabled, "authenticated": authenticated, "username": username, "is_admin": _is_admin(request)})


@app.get("/api/users")
def list_users(request: Request) -> JSONResponse:
    """가입/승인 대기 목록 - 관리자 전용."""
    error = _require_admin(request)
    if error:
        return error
    return JSONResponse(USER_STORE.list_all())


@app.get("/api/error-log")
def get_error_log(request: Request) -> JSONResponse:
    """QA 파이프라인/VOC 분석/교차검증 매트릭스 실행 실패의 실제 원인(예외 메시지·스택트레이스)을
    최근 순으로 반환 - 관리자 전용. 화면에는 "LLM 설정과 서버 로그를 확인하세요" 같은 안내
    문구만 노출하고(내부 경로/설정값 유출 방지), 실제 원인은 이 API로만 확인 가능하다."""
    error = _require_admin(request)
    if error:
        return error
    return JSONResponse({"entries": error_log.list_errors()})


@app.post("/api/users/{username}/approve")
def approve_user(username: str, request: Request) -> JSONResponse:
    error = _require_admin(request)
    if error:
        return error
    if not USER_STORE.approve_user(username):
        return JSONResponse({"error": "승인 대기 중인 계정을 찾을 수 없습니다"}, status_code=404)
    return JSONResponse({"approved": True})


@app.post("/api/users/{username}/reject")
def reject_user(username: str, request: Request) -> JSONResponse:
    error = _require_admin(request)
    if error:
        return error
    if not USER_STORE.reject_user(username):
        return JSONResponse({"error": "승인 대기 중인 계정을 찾을 수 없습니다"}, status_code=404)
    return JSONResponse({"rejected": True})


@app.post("/api/users/{username}/role")
def set_user_role(username: str, payload: Dict[str, Any], request: Request) -> JSONResponse:
    """관리자 권한 부여/회수(양도) - 마지막 남은 관리자는 스스로도 강등할 수 없음(락아웃 방지)."""
    error = _require_admin(request)
    if error:
        return error
    role = str(payload.get("role", ""))
    if not USER_STORE.set_role(username, role):
        return JSONResponse({"error": "역할을 바꿀 수 없습니다(마지막 관리자는 강등 불가, 또는 존재하지 않거나 미승인된 계정)"}, status_code=400)
    return JSONResponse({"username": username, "role": role})


@app.delete("/api/users/{username}")
def delete_user(username: str, request: Request) -> JSONResponse:
    error = _require_admin(request)
    if error:
        return error
    if not USER_STORE.delete_user(username):
        return JSONResponse({"error": "삭제할 수 없습니다(존재하지 않거나 마지막 관리자)"}, status_code=400)
    return JSONResponse({"deleted": True})


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """대시보드 HTML을 그대로 반환 - 매 요청마다 파일을 새로 읽으므로 재시작 없이 수정사항이 반영됨.

    no-store를 안 붙이면 브라우저가 이 HTML을 디스크 캐시에 담아두고 새로고침(F5)에도
    캐시를 그대로 써버려서, 서버는 최신 파일을 읽어도 화면은 예전 코드로 남는 문제가 있었음."""
    html_path = Path(__file__).with_name("templates").joinpath("index.html")
    return HTMLResponse(html_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/voc-quality-chart", response_class=HTMLResponse)
def voc_quality_chart_page() -> HTMLResponse:
    """VOC 분석 품질 지표 차트를 새 창에서 크게 보기 위한 전용 페이지 - 모니터링 애드온의
    Prometheus/Grafana(임베딩 차트 + 새 창 링크)와 동일한 형식을 이 앱 내부 데이터에도
    적용한 것. index.html과 별도 문서라 /api/voc-analysis/quality-dashboard를 직접
    호출해 같은 차트를 렌더링한다(로그인 미들웨어는 이 경로도 그대로 적용됨)."""
    html_path = Path(__file__).with_name("templates").joinpath("voc_quality_chart.html")
    return HTMLResponse(html_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/health")
def healthcheck() -> JSONResponse:
    """헬스체크 - monitoring.HealthChecker에 위임.

    server_started_at/git_sha는 "지금 응답 중인 이 프로세스가 어느 시점의 어느 커밋으로
    기동됐는가"를 판단하기 위한 필드 - 재배포 없이 오래된 프로세스가 떠 있는 상태를
    구분하는 데 씀."""
    status = HealthChecker().check()
    return JSONResponse({
        "service": status.service,
        "status": status.status,
        "details": status.details,
        "server_started_at": SERVER_STARTED_AT,
        "git_sha": GIT_SHA,
    })


@app.get("/api/monitoring/summary")
def monitoring_summary() -> JSONResponse:
    """모니터링 탭이 사용하는 서버 운영 지표 - 요청수/응답시간/에러율 + 최신 헬스체크."""
    summary = METRICS.summary()
    health = HealthChecker().check()
    summary["health"] = {"status": health.status, "details": health.details}
    return JSONResponse(summary)


@app.get("/api/monitoring/targets")
def list_monitoring_targets() -> JSONResponse:
    """등록된 외부 대상(다른 서버의 챗봇/API 등) 목록과 최근 체크 결과 요약."""
    return JSONResponse(EXTERNAL_MONITOR.summary())


@app.post("/api/monitoring/targets")
def add_monitoring_target(payload: Dict[str, Any]) -> JSONResponse:
    """외부 모니터링 대상을 새로 등록 - 등록 직후 백그라운드 스레드가 곧바로 1회 체크함."""
    url = str(payload.get("url", "")).strip()
    if not url.lower().startswith(("http://", "https://")):
        return JSONResponse({"error": "url must start with http:// or https://"}, status_code=400)
    target = EXTERNAL_MONITOR.add(
        name=payload.get("name", ""),
        url=url,
        method=payload.get("method", "GET"),
        interval_seconds=payload.get("interval_seconds", 60),
        timeout_seconds=payload.get("timeout_seconds", 10),
    )
    return JSONResponse(target.to_dict())


@app.delete("/api/monitoring/targets/{target_id}")
def remove_monitoring_target(target_id: str) -> JSONResponse:
    """외부 모니터링 대상을 삭제 (체크 이력도 함께 제거)."""
    if not EXTERNAL_MONITOR.remove(target_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"removed": True})


@app.get("/api/config/connector-defaults")
def connector_defaults() -> JSONResponse:
    """대시보드에서 커넥터 모드/엔드포인트 입력란의 기본값을 채우기 위한 조회용."""
    connector = Config().connector
    return JSONResponse({"mode": connector.mode, "api_endpoint": connector.api_endpoint})


# 설정 탭 "문서" 카드 + 대시보드 탭에서 조회 가능한 문서 화이트리스트 (임의 경로 조회 방지)
DOC_FILES = {
    "user_manual": "사용자_매뉴얼.md",
    "design_spec": "설계서.md",
    "process_spec": "프로세스_명세서.md",
    "network_guide": "팀원용_접속가이드.md",
    "test_results": "테스트_결과.md",
    "scenario_test_report": "테스트_시나리오_보고서.md",
    "defect_report": "결함보고서.md",
    "readme": "README.md",
    "voc_quality_report": "VOC_분석_파이프라인_품질평가_보고서.md",
    "voc_defect_report": "VOC_분석_파이프라인_결함보고서.md",
}
# readme는 docs/가 아니라 프로젝트 최상위에 있는 파일이라 별도 경로로 서빙
DOC_ROOT_KEYS = {"readme"}


@app.get("/api/docs/{doc_key}")
def get_doc(doc_key: str, request: Request) -> JSONResponse:
    """문서 하나를 매 요청마다 새로 읽어서 반환 - 항상 최신 내용을 보장.

    결함보고서(defect_report)만 예외: 실명 계정으로 로그인한 사용자는 전역 docs/ 파일이
    아니라 자기 실행 이력에서 생성된 개인 결함보고서를 봄 - 안 그러면 A의 실행 결과가
    B의 화면에도 그대로 노출되는 정보 유출이 생김(_execute_run 참고)."""
    filename = DOC_FILES.get(doc_key)
    if not filename:
        return JSONResponse({"error": "unknown document"}, status_code=404)
    username = _current_username(request)
    if doc_key == "defect_report" and username != _SHARED_BUCKET:
        path = _user_reports_dir(username) / filename
    else:
        base_dir = Path(".") if doc_key in DOC_ROOT_KEYS else Path("docs")
        path = base_dir / filename
    if not path.exists():
        return JSONResponse({"error": "document not found"}, status_code=404)
    return JSONResponse({"name": filename, "content": path.read_text(encoding="utf-8")})


@app.post("/api/dataset/upload")
async def upload_dataset(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    """데이터셋 파일(JSON/Excel)을 업로드하고 즉시 활성 데이터셋으로 지정."""
    username = _current_username(request)
    content = await file.read()
    # 파일명 앞에 타임스탬프를 붙여서, 같은 이름으로 재업로드해도 이전 업로드를 덮어쓰지 않음
    # - 그래야 각 업로드가 /api/dataset/history에서 계속 선택 가능하게 남음
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = _dataset_dir(username) / f"{stamp}_{file.filename}"
    path.write_bytes(content)

    try:
        cases = load_dataset(path)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    _set_active_dataset(username, path, len(cases))
    _append_dataset_history(username, {
        "path": _normalize_stored_path(str(path)),
        "filename": file.filename,
        "case_count": len(cases),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "uploaded_by": username,
    })
    return JSONResponse({
        "dataset_path": _normalize_stored_path(str(path)),
        "case_count": len(cases),
        "missing_existing_answer_count": sum(1 for case in cases if not case.existing_answer),
    })


@app.get("/api/dataset/current")
def current_dataset(request: Request) -> JSONResponse:
    """현재 활성 데이터셋 정보를 반환 (대시보드 상단 지표에 표시)."""
    username = _current_username(request)
    active = ACTIVE_DATASET.get(username)
    return JSONResponse({
        "path": active,
        "filename": Path(active).name if active else None,
        "is_default": not active,
        "case_count": ACTIVE_DATASET_CASE_COUNT.get(username, 0),
        "error": None,
    })


@app.post("/api/dataset/reset")
def reset_dataset(request: Request) -> JSONResponse:
    """Clears the active dataset selection (back to the built-in demo case).

    Deliberately does not touch upload history or the files on disk -- a user
    can always pick a previous upload again via /api/dataset/select afterward.
    """
    username = _current_username(request)
    ACTIVE_DATASET[username] = None
    ACTIVE_DATASET_CASE_COUNT[username] = 0
    pointer = _active_dataset_pointer(username)
    if pointer.exists():
        pointer.unlink()
    return JSONResponse({"reset": True})


@app.get("/api/dataset/history")
def dataset_history(request: Request) -> JSONResponse:
    """업로드 이력을 최신순으로 반환, 각 항목에 파일 존재 여부/현재 활성 여부를 함께 표시."""
    username = _current_username(request)
    active = ACTIVE_DATASET.get(username)
    history = _load_dataset_history(username)
    enriched = []
    for entry in reversed(history):
        norm_path = _normalize_stored_path(entry["path"])
        enriched.append({**entry, "path": norm_path, "exists": Path(norm_path).exists(), "active": norm_path == active})
    return JSONResponse(enriched)


@app.post("/api/dataset/select")
def select_dataset(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """Re-activates a previously uploaded dataset from history without re-uploading it."""
    username = _current_username(request)
    raw_path = str(payload.get("path", ""))
    resolved = _resolve_within(raw_path, _dataset_dir(username))
    if not resolved:
        return JSONResponse({"error": "invalid dataset path"}, status_code=400)
    if not resolved.exists():
        return JSONResponse({"error": "dataset file not found"}, status_code=404)

    try:
        cases = load_dataset(resolved)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # store the caller-supplied (relative) form, not the resolved absolute path --
    # history entries are recorded as relative paths, and dataset_history()'s
    # "active" flag compares by string equality against ACTIVE_DATASET
    path = Path(_normalize_stored_path(raw_path))
    _set_active_dataset(username, path, len(cases))
    return JSONResponse({"dataset_path": _normalize_stored_path(str(path)), "case_count": len(cases)})


@app.post("/api/dataset/delete")
def delete_dataset(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """업로드 이력에서 데이터셋 파일 1건 또는 여러 건을 완전히 삭제(디스크 파일 + 이력 항목).

    payload에 "path"(단일) 또는 "paths"(배열, 체크박스 다중 삭제용) 중 하나를 받음. 이력 목록은
    한 번만 읽고 한 번만 다시 씀 - 여러 건을 개별 요청으로 나눠 보내면 각 요청이 서로의 변경을
    덮어쓰는 경합이 생길 수 있어(모두 같은 이력 파일을 읽고 쓰기 때문), 반드시 이렇게 한 번에 처리.

    삭제는 그 파일을 업로드한 본인만 가능 - "owner" 필드로 다른 사용자의 아이디를 지정하면
    관리자만 그 사용자 대신 삭제할 수 있음(그 외에는 403).

    지금 활성 데이터셋이 삭제 대상에 포함되면 활성 선택은 자동으로 해제됨(기본 데모 케이스로
    되돌아감) - 존재하지 않는 파일을 계속 활성 데이터셋으로 가리키고 있으면 다음 실행이 실패하기 때문.
    """
    username = _current_username(request)
    owner = str(payload.get("owner") or username)
    if owner != username and not _is_admin(request):
        return JSONResponse({"error": "다른 사용자의 데이터셋은 관리자만 삭제할 수 있습니다"}, status_code=403)

    raw_paths = payload.get("paths")
    if raw_paths is None:
        raw_paths = [payload.get("path", "")]
    targets = {_normalize_stored_path(str(p)) for p in raw_paths if p}
    if not targets:
        return JSONResponse({"error": "삭제할 경로가 없습니다"}, status_code=400)

    dataset_dir = _dataset_dir(owner)
    deleted, invalid = [], []
    for target in targets:
        resolved = _resolve_within(target, dataset_dir)
        if not resolved:
            invalid.append(target)
            continue
        if resolved.exists():
            resolved.unlink()
        deleted.append(target)

    history = _load_dataset_history(owner)
    history = [entry for entry in history if _normalize_stored_path(entry["path"]) not in deleted]
    _dataset_history_path(owner).write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    was_active = ACTIVE_DATASET.get(owner) in deleted
    if was_active:
        ACTIVE_DATASET[owner] = None
        ACTIVE_DATASET_CASE_COUNT[owner] = 0
        pointer = _active_dataset_pointer(owner)
        if pointer.exists():
            pointer.unlink()

    return JSONResponse({"deleted": deleted, "invalid": invalid, "was_active": was_active})


@app.get("/api/dataset/template")
def dataset_template() -> Response:
    """빈 Excel 양식(예시 2행 포함)을 다운로드."""
    workbook = build_template_workbook()
    return StreamingResponse(iter([workbook.getvalue()]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=qa_template.xlsx"})


_PREVIEW_ROW_LIMIT = 200  # 미리보기는 용량이 큰 파일도 화면이 느려지지 않도록 앞부분만 보여줌


def _resolve_within(raw_path: str, base_dir: Path) -> Optional[Path]:
    """base_dir 안에 있는 경로인지 확인(경로 조작 방지)하고 정규화된 Path를 반환, 아니면 None.

    미리보기/다운로드는 '지금 활성인 파일'뿐 아니라 이력의 임의 행을 대상으로도 호출되므로,
    매번 이 검증을 거쳐야 함(select/delete와 동일한 안전 검사).
    """
    if not raw_path:
        return None
    path = Path(_normalize_stored_path(raw_path))
    try:
        resolved = path.resolve()
        base_resolved = base_dir.resolve()
    except OSError:
        return None
    if resolved != base_resolved and base_resolved not in resolved.parents:
        return None
    return resolved


def _download_response(path: Path) -> Response:
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if path.suffix.lower() in (".xlsx", ".xls") else "application/json"
    filename = path.name.split("_", 3)[-1] if path.name.count("_") >= 3 else path.name  # 타임스탬프 접두어 제거
    return StreamingResponse(iter([path.read_bytes()]), media_type=media_type, headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.get("/api/dataset/preview")
def dataset_preview(request: Request, path: Optional[str] = None) -> JSONResponse:
    """데이터셋 내용을 표로 미리보기 (앞 200행까지). path 지정 시 이력의 그 파일, 없으면 활성 데이터셋."""
    username = _current_username(request)
    active = ACTIVE_DATASET.get(username)
    target = _resolve_within(path, _dataset_dir(username)) if path else (Path(active) if active else None)
    if not target:
        return JSONResponse({"error": "잘못된 경로이거나 활성 데이터셋이 없습니다"}, status_code=404 if not path else 400)
    if not target.exists():
        return JSONResponse({"error": "데이터셋 파일을 찾을 수 없습니다"}, status_code=404)
    try:
        cases = load_dataset(target)
    except (ValueError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    rows = [dataclasses.asdict(case) for case in cases[:_PREVIEW_ROW_LIMIT]]
    return JSONResponse({"filename": target.name, "total": len(cases), "shown": len(rows), "rows": rows})


@app.get("/api/dataset/download")
def dataset_download(request: Request, path: Optional[str] = None) -> Response:
    """데이터셋 원본 파일을 그대로 다운로드. path 지정 시 이력의 그 파일, 없으면 활성 데이터셋."""
    username = _current_username(request)
    active = ACTIVE_DATASET.get(username)
    target = _resolve_within(path, _dataset_dir(username)) if path else (Path(active) if active else None)
    if not target or not target.exists():
        return JSONResponse({"error": "잘못된 경로이거나 파일을 찾을 수 없습니다"}, status_code=404)
    return _download_response(target)


# ==================== 테스트 케이스(발화문) - 데이터셋과 별도 관리 ====================

@app.post("/api/testcase/upload")
async def upload_testcase(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    """테스트 케이스 파일(id+question만, 정답 없음)을 업로드하고 즉시 활성화."""
    username = _current_username(request)
    content = await file.read()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = _testcase_dir(username) / f"{stamp}_{file.filename}"
    path.write_bytes(content)

    try:
        testcase_map = load_testcase(path)
    except (ValueError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not testcase_map:
        return JSONResponse({"error": "유효한 id/question 행이 없습니다"}, status_code=400)

    _set_active_testcase(username, path, len(testcase_map))
    _append_testcase_history(username, {
        "path": _normalize_stored_path(str(path)),
        "filename": file.filename,
        "case_count": len(testcase_map),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "uploaded_by": username,
    })
    return JSONResponse({"testcase_path": _normalize_stored_path(str(path)), "case_count": len(testcase_map)})


@app.get("/api/testcase/current")
def current_testcase(request: Request) -> JSONResponse:
    """현재 활성 테스트 케이스 정보를 반환."""
    username = _current_username(request)
    active = ACTIVE_TESTCASE.get(username)
    return JSONResponse({
        "path": active,
        "filename": Path(active).name if active else None,
        "is_default": not active,
        "case_count": ACTIVE_TESTCASE_CASE_COUNT.get(username, 0),
        "error": None,
    })


@app.post("/api/testcase/reset")
def reset_testcase(request: Request) -> JSONResponse:
    """활성 테스트 케이스를 해제 - 이후 실행은 데이터셋 자체의 question을 그대로 씀(하위호환)."""
    username = _current_username(request)
    ACTIVE_TESTCASE[username] = None
    ACTIVE_TESTCASE_CASE_COUNT[username] = 0
    pointer = _active_testcase_pointer(username)
    if pointer.exists():
        pointer.unlink()
    return JSONResponse({"reset": True})


@app.get("/api/testcase/history")
def testcase_history(request: Request) -> JSONResponse:
    username = _current_username(request)
    active = ACTIVE_TESTCASE.get(username)
    history = _load_testcase_history(username)
    enriched = []
    for entry in reversed(history):
        norm_path = _normalize_stored_path(entry["path"])
        enriched.append({**entry, "path": norm_path, "exists": Path(norm_path).exists(), "active": norm_path == active})
    return JSONResponse(enriched)


@app.post("/api/testcase/select")
def select_testcase(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """이력에서 이전 테스트 케이스 파일을 다시 활성화."""
    username = _current_username(request)
    raw_path = str(payload.get("path", ""))
    resolved = _resolve_within(raw_path, _testcase_dir(username))
    if not resolved:
        return JSONResponse({"error": "invalid testcase path"}, status_code=400)
    if not resolved.exists():
        return JSONResponse({"error": "testcase file not found"}, status_code=404)

    try:
        testcase_map = load_testcase(resolved)
    except (ValueError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    path = Path(_normalize_stored_path(raw_path))
    _set_active_testcase(username, path, len(testcase_map))
    return JSONResponse({"testcase_path": _normalize_stored_path(str(path)), "case_count": len(testcase_map)})


@app.post("/api/testcase/delete")
def delete_testcase(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """업로드 이력에서 테스트 케이스 파일 1건 또는 여러 건을 완전히 삭제(디스크 파일 + 이력 항목).

    payload에 "path"(단일) 또는 "paths"(배열, 체크박스 다중 삭제용) 중 하나를 받음 - dataset/delete와
    동일한 이유로 이력 파일은 한 번만 읽고 한 번만 다시 씀. 삭제 권한도 dataset/delete와 동일
    (업로드한 본인, 또는 "owner" 필드로 지정한 사용자 대신 삭제하려는 관리자만 가능)."""
    username = _current_username(request)
    owner = str(payload.get("owner") or username)
    if owner != username and not _is_admin(request):
        return JSONResponse({"error": "다른 사용자의 테스트 케이스는 관리자만 삭제할 수 있습니다"}, status_code=403)

    raw_paths = payload.get("paths")
    if raw_paths is None:
        raw_paths = [payload.get("path", "")]
    targets = {_normalize_stored_path(str(p)) for p in raw_paths if p}
    if not targets:
        return JSONResponse({"error": "삭제할 경로가 없습니다"}, status_code=400)

    testcase_dir = _testcase_dir(owner)
    deleted, invalid = [], []
    for target in targets:
        resolved = _resolve_within(target, testcase_dir)
        if not resolved:
            invalid.append(target)
            continue
        if resolved.exists():
            resolved.unlink()
        deleted.append(target)

    history = _load_testcase_history(owner)
    history = [entry for entry in history if _normalize_stored_path(entry["path"]) not in deleted]
    _testcase_history_path(owner).write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    was_active = ACTIVE_TESTCASE.get(owner) in deleted
    if was_active:
        ACTIVE_TESTCASE[owner] = None
        ACTIVE_TESTCASE_CASE_COUNT[owner] = 0
        pointer = _active_testcase_pointer(owner)
        if pointer.exists():
            pointer.unlink()

    return JSONResponse({"deleted": deleted, "invalid": invalid, "was_active": was_active})


@app.get("/api/testcase/template")
def testcase_template() -> Response:
    """빈 테스트 케이스 양식(id/question 2열)을 다운로드."""
    workbook = build_testcase_template_workbook()
    return StreamingResponse(iter([workbook.getvalue()]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=testcase_template.xlsx"})


@app.get("/api/testcase/preview")
def testcase_preview(request: Request, path: Optional[str] = None) -> JSONResponse:
    """테스트 케이스 내용을 표로 미리보기 (앞 200행까지). path 지정 시 이력의 그 파일, 없으면 활성 테스트 케이스."""
    username = _current_username(request)
    active = ACTIVE_TESTCASE.get(username)
    target = _resolve_within(path, _testcase_dir(username)) if path else (Path(active) if active else None)
    if not target:
        return JSONResponse({"error": "잘못된 경로이거나 활성 테스트 케이스가 없습니다"}, status_code=404 if not path else 400)
    if not target.exists():
        return JSONResponse({"error": "테스트 케이스 파일을 찾을 수 없습니다"}, status_code=404)
    try:
        testcase_map = load_testcase(target)
    except (ValueError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    rows = [{"id": k, "question": v} for k, v in list(testcase_map.items())[:_PREVIEW_ROW_LIMIT]]
    return JSONResponse({"filename": target.name, "total": len(testcase_map), "shown": len(rows), "rows": rows})


@app.get("/api/testcase/download")
def testcase_download(request: Request, path: Optional[str] = None) -> Response:
    """테스트 케이스 원본 파일을 그대로 다운로드. path 지정 시 이력의 그 파일, 없으면 활성 테스트 케이스."""
    username = _current_username(request)
    active = ACTIVE_TESTCASE.get(username)
    target = _resolve_within(path, _testcase_dir(username)) if path else (Path(active) if active else None)
    if not target or not target.exists():
        return JSONResponse({"error": "잘못된 경로이거나 파일을 찾을 수 없습니다"}, status_code=404)
    return _download_response(target)


def _default_cases() -> List[GoldenCase]:
    """활성 데이터셋이 없을 때 쓰는 데모용 케이스 1건."""
    return [
        GoldenCase(
            id="TC-001",
            category="COM",
            question="How do I reset my password?",
            golden_answer="Use the password reset link",
            relevant_doc_ids=["DOC-1"],
            existing_answer="Use the password reset link",
            existing_doc_ids=["DOC-1"],
            existing_contexts=["Use the password reset link on the account settings page."],
        )
    ]


def _defect_report_docs_dir(username: str) -> str:
    """결함보고서.md를 저장/조회할 위치 - "shared"는 기존과 완전히 같은 docs/ 최상위를 그대로
    쓰고, 실명 계정은 자기 폴더로 격리됨(get_doc()의 defect_report 분기와 짝을 이룸 - 안 그러면
    A의 실행 결과가 B의 대시보드에도 그대로 보이는 정보 유출이 생김)."""
    return "docs" if username == _SHARED_BUCKET else str(_user_reports_dir(username))


def _llm_judge_kwargs(payload: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 연동 provider별로 설정 필드가 달라서(custom=key_name/base_url 직접 지정,
    anthropic/openai=키만 지정) 분기 처리. "none"이면 아무 키도 안 넣어 완전히 비활성화됨.

    OpenAIJudgeClient(**kwargs) 생성자에 그대로 넣을 수 있는 형태(provider/api_key/model/
    base_url/key_name)로 반환 - _execute_run(QA 파이프라인)과 VOC 분석 라우터가 동일하게
    재사용."""
    provider = (payload.get("llm_provider") or "openai").lower()
    kwargs: Dict[str, Any] = {"provider": provider}
    if provider == "custom":
        kwargs["api_key"] = payload.get("llm_key_value") or ""
        kwargs["key_name"] = payload.get("llm_key_name") or "Authorization"
        kwargs["base_url"] = payload.get("llm_endpoint") or ""
    elif provider == "anthropic":
        api_key = payload.get("llm_key_value") or payload.get("anthropic_api_key")
        if api_key:
            kwargs["api_key"] = api_key
    elif provider != "none":
        api_key = payload.get("llm_key_value") or payload.get("openai_api_key")
        if api_key:
            kwargs["api_key"] = api_key
    if payload.get("llm_model"):
        kwargs["model"] = payload["llm_model"]
    return kwargs


def _independent_judge_kwargs(settings: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """VOC 분석 결과를 검증할 "독립 Judge" 클라이언트 설정 - 생성에 쓴 provider와 일부러
    다른 provider를 골라 자기평가 편향(생성 모델이 자기 산출물을 관대하게 채점하는 문제)을
    막음. openai/anthropic 키가 둘 다 있어야 진짜 교차 검증(cross_model=True)이 되고,
    하나만 있으면 같은 provider로 폴백(cross_model=False로 그 사실을 그대로 노출 - 교차
    검증이 안 됐는데 됐다고 위장하지 않음).

    Tuple[kwargs, cross_model]을 반환 - kwargs는 _llm_judge_kwargs와 동일하게
    OpenAIJudgeClient(**kwargs) 생성자에 바로 넣을 수 있는 형태."""
    primary_provider = (settings.get("llm_provider") or "openai").lower()
    has_openai = bool(settings.get("openai_api_key"))
    has_anthropic = bool(settings.get("anthropic_api_key"))

    if primary_provider == "openai" and has_anthropic:
        judge_provider = "anthropic"
    elif primary_provider == "anthropic" and has_openai:
        judge_provider = "openai"
    elif primary_provider not in ("openai", "anthropic") and has_anthropic:
        judge_provider = "anthropic"
    elif primary_provider not in ("openai", "anthropic") and has_openai:
        judge_provider = "openai"
    else:
        judge_provider = primary_provider

    synthetic_settings = dict(settings)
    synthetic_settings["llm_provider"] = judge_provider
    # 버그였던 지점: llm_model/llm_key_value/llm_key_name/llm_endpoint는 "현재 선택된(주로
    # 생성용) provider"에 종속된 값인데, 이걸 그대로 들고 있으면 독립 Judge가 반대 provider로
    # 바뀌어도 예전 provider의 모델명/키가 그대로 흘러 들어감 - 실제로 primary=openai(모델
    # gpt-4o-mini)일 때 judge_provider=anthropic으로 바뀌었는데도 model="gpt-4o-mini"가
    # 그대로 전달되어 Anthropic API가 404(모델 없음)를 반환하는 실제 장애로 나타났었음.
    # provider가 실제로 바뀌는 경우엔 이 오염 가능성이 있는 필드들을 전부 지워서, provider별
    # 전용 키(openai_api_key/anthropic_api_key)와 그 provider의 기본 모델(env var 폴백)만
    # 쓰이도록 강제한다.
    if judge_provider != primary_provider:
        for stale_key in ("llm_model", "llm_key_value", "llm_key_name", "llm_endpoint"):
            synthetic_settings.pop(stale_key, None)
    kwargs = _llm_judge_kwargs(synthetic_settings)
    return kwargs, judge_provider != primary_provider


def _execute_run(username: str, run_id: str, techniques: List[str], category_filter: Optional[List[str]], payload: Dict[str, Any]) -> None:
    """백그라운드 스레드에서 실제 파이프라인을 실행 (POST /api/run이 이 함수를 스레드로 띄움).

    예외가 나도 여기서 잡아 RUN_REGISTRY에 status="error"로 기록합니다 - 스레드가 죽어도
    폴링하는 클라이언트에게는 정상적으로 실패 상태가 보여야 하기 때문입니다.
    """
    with RUN_LOCK:
        RUN_REGISTRY[username][run_id]["status"] = "running"

    def _set_stage(stage: str) -> None:
        with RUN_LOCK:
            RUN_REGISTRY[username][run_id]["stage"] = stage

    _set_stage("데이터 준비 중")
    try:
        # 클라이언트가 dataset_path/testcase_path 키를 명시적으로 보냈다면(화면에 보이는 활성
        # 값을 그대로 실어 보낸 것) 그 값을 그대로 신뢰 - 서버의 ACTIVE_* 값은 다른 탭에서
        # 그 사이에 바꿨을 수 있어서, 화면에 보이던 것과 실제로 채점된 값이 어긋날 수 있음.
        # 키 자체가 없는 예전 클라이언트/외부 API 호출은 기존처럼 서버의 현재 활성 값을 씀.
        no_dataset = bool(payload.get("no_dataset"))
        testcase_path = payload["testcase_path"] if "testcase_path" in payload else ACTIVE_TESTCASE.get(username)
        if testcase_path:
            testcase_path = _normalize_stored_path(testcase_path)
        testcase_map = load_testcase(testcase_path) if testcase_path else {}

        if no_dataset:
            # 데이터셋(정답) 비교 없이 테스트 케이스(발화문)만으로 품질 테스트 진행 - category는
            # 기본값(COM), golden_answer는 빈 문자열로 둬서 llm_quality/rubric 평가자가 자동으로
            # "정답과 비교" 대신 "질문에 대한 응답으로서 타당한지" 절대평가 모드로 채점하게 함.
            dataset_path = None
            if testcase_map:
                cases = [GoldenCase(id=cid, category="COM", question=q, golden_answer="") for cid, q in testcase_map.items()]
            else:
                cases = [dataclasses.replace(c, golden_answer="") for c in _default_cases()]
        else:
            dataset_path = payload["dataset_path"] if "dataset_path" in payload else ACTIVE_DATASET.get(username)
            if dataset_path:
                dataset_path = _normalize_stored_path(dataset_path)
            cases = load_dataset(dataset_path) if dataset_path else []
            if not cases:
                cases = _default_cases()
            if testcase_map:
                # 테스트 케이스(발화문)가 활성화돼 있으면 id가 일치하는 데이터셋 케이스의 question만
                # 그 발화문으로 덮어씀 - id가 테스트 케이스에 없는 데이터셋 케이스는 이번 실행에서 제외(스킵)
                cases = [dataclasses.replace(c, question=testcase_map[c.id]) for c in cases if c.id in testcase_map]

        config = Config(reports_dir=str(_user_reports_dir(username)))
        connector_payload = payload.get("connector") or {}
        if payload.get("mode"):
            connector_payload.setdefault("mode", payload["mode"])
        if payload.get("api_endpoint"):
            connector_payload.setdefault("api_endpoint", payload["api_endpoint"])
        for key, value in connector_payload.items():
            if hasattr(config.connector, key) and value not in (None, ""):
                setattr(config.connector, key, value)
        config.llm_judge.update(_llm_judge_kwargs(payload))

        def on_progress(done: int, total: int) -> None:
            with RUN_LOCK:
                RUN_REGISTRY[username][run_id]["progress"] = {"completed": done, "total": total}

        _set_stage("평가 실행 중")
        report = PipelineOrchestrator(config).run(cases, category_filter=category_filter, techniques=techniques, run_id=run_id, on_progress=on_progress, dataset_path=dataset_path, testcase_path=testcase_path)
        report.executed_by = username
        _set_stage("리포트 저장 중")
        write_reports(report, reports_dir=str(_user_reports_dir(username)))
        write_defect_report_doc(report.to_dict(), docs_dir=_defect_report_docs_dir(username))

        jira_tickets: List[Dict[str, Any]] = []
        if not payload.get("no_jira"):
            _set_stage("Jira 티켓 생성 중")
            jira_config = payload.get("jira_config") or {}
            jira_tickets = JiraNotifier({"enabled": bool(jira_config.get("base_url") or jira_config.get("email") or jira_config.get("api_token")), **jira_config}).notify(report.to_dict())
            if jira_tickets:
                (_user_reports_dir(username) / f"jira_{run_id}.json").write_text(json.dumps(jira_tickets, indent=2), encoding="utf-8")

        if payload.get("slack_webhook_url") or payload.get("discord_webhook_url") or payload.get("teams_webhook_url"):
            _set_stage("알림 발송 중")
        if payload.get("slack_webhook_url"):
            SlackNotifier(payload["slack_webhook_url"]).notify(report.to_dict())
        if payload.get("discord_webhook_url"):
            DiscordNotifier(payload["discord_webhook_url"]).notify(report.to_dict())
        if payload.get("teams_webhook_url"):
            TeamsNotifier(payload["teams_webhook_url"]).notify(report.to_dict())

        with RUN_LOCK:
            RUN_REGISTRY[username][run_id]["status"] = "done"
            RUN_REGISTRY[username][run_id]["stage"] = "완료"
            RUN_REGISTRY[username][run_id]["result"] = report.to_dict()
            RUN_REGISTRY[username][run_id]["jira_tickets"] = jira_tickets
    except Exception as exc:
        # stage는 일부러 덮어쓰지 않음 - 실패 시점에 마지막으로 기록됐던 단계 이름을 그대로
        # 남겨둬야, 폴링하는 화면이 "어느 단계에서 실패했는지"를 정확히 표시할 수 있다.
        error_log.record_error("qa_pipeline", exc, username=username, run_id=run_id)
        with RUN_LOCK:
            RUN_REGISTRY[username][run_id]["status"] = "error"
            RUN_REGISTRY[username][run_id]["error"] = str(exc)


@app.post("/api/run")
def run_pipeline(request: Request, payload: Optional[Dict[str, Any]] = None) -> JSONResponse:
    """파이프라인 실행을 시작 - 백그라운드 스레드로 실행하고 즉시 run_id를 반환.

    진행 상황/결과는 GET /api/run/{run_id}/status, /result로 폴링해서 조회.
    """
    username = _current_username(request)
    payload = payload or {}
    techniques = payload.get("techniques")
    if techniques is not None:
        if len(techniques) == 0:
            return JSONResponse({"error": "at least one technique must be selected"}, status_code=400)
        unknown = [t for t in techniques if t not in ALL_TECHNIQUES]
        if unknown:
            return JSONResponse({"error": f"unknown technique(s): {unknown}"}, status_code=400)
    else:
        techniques = ["rag", "llm_quality"]

    # "category"는 리스트(다중 선택, OR 조건)가 정식 형태지만, 문자열 하나만 보내는 예전 방식
    # 호출부(외부 스크립트 등)도 계속 동작하도록 여기서 정규화 - PipelineOrchestrator.run()도
    # 같은 이유로 한 번 더 방어함(직접 호출하는 다른 진입점 대비)
    raw_category = payload.get("category")
    if isinstance(raw_category, list):
        category_filter = [str(c) for c in raw_category if c] or None
    elif raw_category:
        category_filter = [str(raw_category)]
    else:
        category_filter = None

    with RUN_LOCK:
        user_runs = RUN_REGISTRY.setdefault(username, {})
        run_id = f"run_{len(user_runs) + 1}"
        user_runs[run_id] = {"status": "queued", "stage": "대기 중", "progress": {"completed": 0, "total": 1}, "result": None, "error": None, "jira_tickets": []}

    thread = Thread(target=_execute_run, args=(username, run_id, techniques, category_filter, payload), daemon=True)
    thread.start()

    return JSONResponse({"run_id": run_id, "status": RUN_REGISTRY[username][run_id]["status"]})


@app.get("/api/run/{run_id}/status")
def run_status(run_id: str, request: Request) -> JSONResponse:
    """실행 상태/진행률 폴링용 (queued/running/done/error). 결과 본문은 안 담아 응답을 가볍게 유지."""
    username = _current_username(request)
    entry = RUN_REGISTRY.get(username, {}).get(run_id, {"status": "error", "progress": {"completed": 0, "total": 1}, "error": "not found"})
    entry = dict(entry)
    entry.setdefault("run_id", run_id)
    entry.pop("result", None)
    return JSONResponse(entry)


@app.get("/api/run/{run_id}/result")
def run_result(run_id: str, request: Request) -> JSONResponse:
    """완료된 실행의 전체 리포트를 조회 (아직 안 끝났거나 없으면 404)."""
    username = _current_username(request)
    entry = RUN_REGISTRY.get(username, {}).get(run_id)
    if not entry or entry.get("result") is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(entry["result"])


def _safe_run_path(username: str, run_id: str) -> Optional[Path]:
    """run_id가 "run_" 접두사만 가진 안전한 값인지 확인(경로 조작(../ 등) 방지)하고,
    이 사용자의 실행이력 폴더 안에서 해당 파일을 찾음."""
    if not run_id.startswith("run_") or "/" in run_id or "\\" in run_id or ".." in run_id:
        return None
    path = _user_reports_dir(username) / f"run_{run_id}.json"
    return path if path.exists() else None


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str, request: Request) -> JSONResponse:
    """디스크에 저장된 과거 실행 리포트를 직접 조회 (대시보드의 "실행 선택" 드롭다운이 사용)."""
    username = _current_username(request)
    path = _safe_run_path(username, run_id)
    if not path:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/jira/tickets")
def jira_tickets(request: Request) -> JSONResponse:
    """이 사용자의 실행에서 생성된 Jira 티켓을 모아서 반환 (_execute_run이 jira_{run_id}.json으로 저장해둔 것)."""
    username = _current_username(request)
    tickets: List[Dict[str, Any]] = []
    for path in sorted(_user_reports_dir(username).glob("jira_run_*.json")):
        run_id = path.stem[len("jira_"):]
        try:
            for ticket in json.loads(path.read_text(encoding="utf-8")):
                tickets.append({"run_id": run_id, **ticket})
        except Exception:
            continue
    return JSONResponse(tickets)


@app.get("/api/reports/latest")
def latest_report(request: Request, format: str = "json") -> Response:
    """최신 리포트를 JSON/CSV/마크다운 중 원하는 형식으로 다운로드."""
    username = _current_username(request)
    latest_path = _user_reports_dir(username) / "latest.json"
    export_dir = _user_reports_dir(username) / "exports"
    if not latest_path.exists():
        return JSONResponse({"error": "no report available"}, status_code=404)
    if format.lower() == "csv":
        csv_path = export_dir / "run_latest.csv"
        if not csv_path.exists():
            csv_path = export_dir / "final_quality_report.csv"
        payload = csv_path.read_text(encoding="utf-8") if csv_path.exists() else ""
        return Response(payload, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=latest_report.csv"})
    if format.lower() == "md":
        md_path = export_dir / "final_quality_report.md"
        payload = md_path.read_text(encoding="utf-8") if md_path.exists() else "# Latest Report\n\n"
        return Response(payload, media_type="text/markdown", headers={"Content-Disposition": "attachment; filename=latest_report.md"})
    return Response(latest_path.read_text(encoding="utf-8"), media_type="application/json", headers={"Content-Disposition": "attachment; filename=latest_report.json"})


@app.post("/api/settings")
def save_settings(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """알림/Jira/LLM 설정을 저장 - 파일에도 쓰고, 현재 프로세스의 환경변수에도 즉시 반영.

    전역(모든 사용자 공유) 설정이고 Jira/LLM API 키 등 민감정보를 포함하므로 관리자만 변경 가능."""
    error = _require_admin(request)
    if error:
        return error
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for key, value in payload.items():
        if value:
            os.environ[str(key).upper()] = str(value)
    return JSONResponse({"saved": True, "path": str(SETTINGS_PATH)})


def _load_settings_dict() -> Dict[str, Any]:
    """저장된 설정을 조회 - 파일에 없으면 환경변수 값으로 보충.

    관리자 게이트가 필요 없는 순수 조회 함수 - HTTP 핸들러(load_settings, 아래)는 이 함수를
    호출하기 전에 _require_admin으로 막지만, VOC 분석 라우터처럼 서버 내부에서 Jira/LLM
    설정값만 필요한 곳(민감정보를 HTTP 응답으로 노출하지 않는 경우)은 이 함수를 직접 호출."""
    settings: Dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        settings.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
    env_mappings = {
        "slack_webhook_url": os.getenv("SLACK_WEBHOOK_URL"),
        "discord_webhook_url": os.getenv("DISCORD_WEBHOOK_URL"),
        "teams_webhook_url": os.getenv("TEAMS_WEBHOOK_URL"),
        "jira_base_url": os.getenv("JIRA_BASE_URL"),
        "jira_email": os.getenv("JIRA_EMAIL"),
        "jira_token": os.getenv("JIRA_TOKEN"),
        "jira_project": os.getenv("JIRA_PROJECT"),
        "openai_api_key": os.getenv("OPENAI_API_KEY"),
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY"),
        "llm_provider": os.getenv("LLM_PROVIDER"),
        "llm_key_name": os.getenv("LLM_KEY_NAME"),
        "llm_key_value": os.getenv("LLM_KEY_VALUE"),
        "llm_endpoint": os.getenv("LLM_ENDPOINT"),
        "llm_model": os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL"),
    }
    for key, value in env_mappings.items():
        if value:
            settings[key] = value
    return settings


@app.get("/api/settings")
def load_settings(request: Request) -> JSONResponse:
    """API 키/웹훅 URL 등 민감정보를 그대로 반환하므로 관리자만 조회 가능."""
    error = _require_admin(request)
    if error:
        return error
    return JSONResponse(_load_settings_dict())


@app.get("/api/runs")
def runs(request: Request) -> JSONResponse:
    """실행 이력 요약 목록 (대시보드의 통과율 추이 차트/실행 선택 드롭다운이 사용)."""
    username = _current_username(request)
    return JSONResponse(list_run_history(reports_dir=str(_user_reports_dir(username))))


# ===================== 게시판(일반/FAQ/VOC) + VOC 자동분석 =====================
# monitoring_addon과 동일하게 configure() 의존성 주입으로 붙여서 app/main.py를 더 비대하게
# 만들지 않음. 모니터링 애드온과 달리 이 기능은 선택 사항이 아니라 핵심 기능이라 try/except로
# 감싸지 않음(로딩 실패는 다른 애드온처럼 조용히 넘어가면 안 되고 서버 기동 자체가 실패해야 함).
from app.routers import board as _board_module
from app.routers import voc_analysis as _voc_analysis_module

_board_module.configure(BOARD_STORE, _current_username, _is_admin_effective)
app.include_router(_board_module.router)

_voc_analysis_module.configure(
    BOARD_STORE, _current_username, _load_settings_dict, _llm_judge_kwargs, _independent_judge_kwargs, _is_admin_effective,
    app_version_fn=lambda: {"server_started_at": SERVER_STARTED_AT, "git_sha": GIT_SHA},
)
app.include_router(_voc_analysis_module.router)


# ===================== 모니터링 애드온 (k6/SQLite 이력/Prometheus) =====================
# 여기부터는 기존 REST API를 단 하나도 수정하지 않는 순수 추가 블록입니다. 라우터 로딩이
# 실패해도(패키지 손상, DB 오류 등) 예외를 여기서 잡아 기존 서버 기동 자체는 항상 성공합니다.
if MONITORING_ADDON_ENABLED:
    try:
        from app.routers import monitoring_addon as _monitoring_addon_module

        _monitoring_addon_module.configure(METRICS, HealthChecker(), MONITORING_ADDON_DB)
        app.include_router(_monitoring_addon_module.router)
        if PROMETHEUS_ADDON_ENABLED:
            app.include_router(_monitoring_addon_module.metrics_router)

        @app.get("/monitoring-addon", response_class=HTMLResponse)
        def monitoring_addon_page() -> HTMLResponse:
            """모니터링 애드온 전용 페이지 - 기존 대시보드(index.html)와는 완전히 별도인 신규 페이지."""
            html_path = Path(__file__).with_name("templates").joinpath("monitoring_addon.html")
            html = html_path.read_text(encoding="utf-8")
            html = html.replace("__GRAFANA_LINK_ENABLED__", "true" if GRAFANA_LINK_ENABLED else "false")
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
    except Exception as e:
        print(f"[monitoring-addon] router load skipped: {e}")
