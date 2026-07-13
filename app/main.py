"""FastAPI 웹앱 진입점 - qa_agent 코어 엔진을 REST API + 대시보드로 노출.

여기서는 새 채점 로직을 만들지 않습니다 - 전부 qa_agent.pipeline.PipelineOrchestrator에
위임하고, 이 파일은 HTTP 요청/응답 변환과 실행 상태 관리(RUN_REGISTRY)만 담당합니다.
"""

import dataclasses
import json
import os
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

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

from qa_agent.config_loader import Config
from qa_agent.excel_io import build_template_workbook, build_testcase_template_workbook, load_dataset, load_testcase
from qa_agent.jira_notifier import JiraNotifier
from qa_agent.models import GoldenCase
from qa_agent.pipeline import ALL_TECHNIQUES, PipelineOrchestrator
from qa_agent.reporter import list_run_history, write_defect_report_doc, write_reports
from qa_agent.slack_notifier import DiscordNotifier, SlackNotifier, TeamsNotifier


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


app = FastAPI(title="AI Agent Quality Platform", lifespan=_lifespan)
METRICS = MetricsCollector()  # 모니터링 탭이 조회하는 서버 운영 지표(요청수/응답시간/에러율) 싱글턴
EXTERNAL_MONITOR = ExternalMonitorRegistry(path=str(Path("reports") / "monitoring_targets.json"))  # 외부 URL 합성 모니터링 대상 저장소

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


RUN_REGISTRY: Dict[str, Dict[str, Any]] = {}  # run_id -> {status, progress, result, error, jira_tickets}
RUN_LOCK = Lock()  # 백그라운드 스레드에서 RUN_REGISTRY를 건드릴 때 쓰는 락
ACTIVE_DATASET: Optional[str] = None  # 현재 선택된 데이터셋 파일 경로 (없으면 기본 데모 케이스 사용)
ACTIVE_DATASET_CASE_COUNT: int = 0
EXPORT_DIR = Path("reports") / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = Path("reports") / "settings.json"
SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
ACTIVE_DATASET_POINTER = Path("reports") / ".active_dataset.json"  # ACTIVE_DATASET을 서버 재시작 후에도 복원하기 위한 포인터 파일
DATASET_DIR = Path("reports") / "datasets"  # 업로드된 데이터셋 파일들이 실제로 저장되는 위치
DATASET_DIR.mkdir(parents=True, exist_ok=True)
DATASET_HISTORY_PATH = DATASET_DIR / ".history.json"  # 업로드 이력 (설정 탭의 "업로드 이력" 목록에 사용)
DATASET_HISTORY_LIMIT = 30  # 이력이 무한정 커지지 않도록 최근 N건만 유지

# 테스트 케이스(발화문) - 데이터셋(정답)과 완전히 별도로 관리되는 두 번째 파일 세트.
# 데이터셋 = category/golden_answer 등 "기준값", 테스트 케이스 = id/question만 있는 "질문".
# 둘 다 안 올리면 기존처럼 데이터셋 파일 자체의 question이 그대로 쓰임(하위호환) - 테스트
# 케이스를 올리면 id가 일치하는 데이터셋 케이스의 question만 그 발화문으로 덮어써서 실행됨.
ACTIVE_TESTCASE: Optional[str] = None
ACTIVE_TESTCASE_CASE_COUNT: int = 0
TESTCASE_ACTIVE_POINTER = Path("reports") / ".active_testcase.json"
TESTCASE_DIR = Path("reports") / "testcases"
TESTCASE_DIR.mkdir(parents=True, exist_ok=True)
TESTCASE_HISTORY_PATH = TESTCASE_DIR / ".history.json"
TESTCASE_HISTORY_LIMIT = 30


def _normalize_stored_path(raw: str) -> str:
    """저장된 경로 문자열을 항상 '/' 구분자로 정규화.

    이 값들은 pointer/history 파일(JSON)에 그대로 저장되는데, Windows에서 네이티브로 돌릴 때
    쓰인 값(예: "reports\\datasets\\x.json")을 나중에 Docker(Linux) 컨테이너에서 그대로 읽으면
    백슬래시가 경로 구분자로 해석되지 않아 파일을 못 찾는다(반대 방향도 마찬가지). '/'는 Windows
    Python에서도 정상적으로 경로 구분자로 인식되므로, 어느 쪽에서 쓰였든 항상 '/'로 통일해두면
    호스트 OS와 무관하게 안전하게 읽고 쓸 수 있다.
    """
    return raw.replace("\\", "/")


def _restore_active_dataset() -> None:
    """서버가 (재)시작될 때 이전에 선택돼 있던 데이터셋을 포인터 파일에서 복원."""
    global ACTIVE_DATASET, ACTIVE_DATASET_CASE_COUNT
    if not ACTIVE_DATASET_POINTER.exists():
        return
    try:
        pointer = json.loads(ACTIVE_DATASET_POINTER.read_text(encoding="utf-8"))
        path = pointer.get("path")
        if path:
            path = _normalize_stored_path(path)
        if path and Path(path).exists():
            ACTIVE_DATASET = path
            ACTIVE_DATASET_CASE_COUNT = len(load_dataset(path))
    except Exception:
        pass


def _restore_active_testcase() -> None:
    """서버가 (재)시작될 때 이전에 선택돼 있던 테스트 케이스를 포인터 파일에서 복원."""
    global ACTIVE_TESTCASE, ACTIVE_TESTCASE_CASE_COUNT
    if not TESTCASE_ACTIVE_POINTER.exists():
        return
    try:
        pointer = json.loads(TESTCASE_ACTIVE_POINTER.read_text(encoding="utf-8"))
        path = pointer.get("path")
        if path:
            path = _normalize_stored_path(path)
        if path and Path(path).exists():
            ACTIVE_TESTCASE = path
            ACTIVE_TESTCASE_CASE_COUNT = len(load_testcase(path))
    except Exception:
        pass


_restore_active_dataset()
_restore_active_testcase()


def _load_dataset_history() -> List[Dict[str, Any]]:
    """지금까지 업로드된 데이터셋 이력을 읽어옴 (없으면 빈 리스트)."""
    if not DATASET_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(DATASET_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_dataset_history(entry: Dict[str, Any]) -> None:
    """업로드 이력에 항목 1건을 추가하고 최근 DATASET_HISTORY_LIMIT건만 남김."""
    history = _load_dataset_history()
    history.append(entry)
    DATASET_HISTORY_PATH.write_text(json.dumps(history[-DATASET_HISTORY_LIMIT:], indent=2, ensure_ascii=False), encoding="utf-8")


def _set_active_dataset(path: Path, case_count: int) -> None:
    """현재 활성 데이터셋을 바꾸고, 서버 재시작에도 유지되도록 포인터 파일에 기록."""
    global ACTIVE_DATASET, ACTIVE_DATASET_CASE_COUNT
    ACTIVE_DATASET = _normalize_stored_path(str(path))
    ACTIVE_DATASET_CASE_COUNT = case_count
    ACTIVE_DATASET_POINTER.write_text(json.dumps({"path": ACTIVE_DATASET}), encoding="utf-8")


def _load_testcase_history() -> List[Dict[str, Any]]:
    if not TESTCASE_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(TESTCASE_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_testcase_history(entry: Dict[str, Any]) -> None:
    history = _load_testcase_history()
    history.append(entry)
    TESTCASE_HISTORY_PATH.write_text(json.dumps(history[-TESTCASE_HISTORY_LIMIT:], indent=2, ensure_ascii=False), encoding="utf-8")


def _set_active_testcase(path: Path, case_count: int) -> None:
    global ACTIVE_TESTCASE, ACTIVE_TESTCASE_CASE_COUNT
    ACTIVE_TESTCASE = _normalize_stored_path(str(path))
    ACTIVE_TESTCASE_CASE_COUNT = case_count
    TESTCASE_ACTIVE_POINTER.write_text(json.dumps({"path": ACTIVE_TESTCASE}), encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """대시보드 HTML을 그대로 반환 - 매 요청마다 파일을 새로 읽으므로 재시작 없이 수정사항이 반영됨."""
    html_path = Path(__file__).with_name("templates").joinpath("index.html")
    return html_path.read_text(encoding="utf-8")


@app.get("/health")
def healthcheck() -> JSONResponse:
    """헬스체크 - monitoring.HealthChecker에 위임."""
    status = HealthChecker().check()
    return JSONResponse({"service": status.service, "status": status.status, "details": status.details})


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
}


@app.get("/api/docs/{doc_key}")
def get_doc(doc_key: str) -> JSONResponse:
    """문서 하나를 매 요청마다 새로 읽어서 반환 - 항상 최신 내용을 보장."""
    filename = DOC_FILES.get(doc_key)
    if not filename:
        return JSONResponse({"error": "unknown document"}, status_code=404)
    path = Path("docs") / filename
    if not path.exists():
        return JSONResponse({"error": "document not found"}, status_code=404)
    return JSONResponse({"name": filename, "content": path.read_text(encoding="utf-8")})


@app.post("/api/dataset/upload")
async def upload_dataset(file: UploadFile = File(...)) -> JSONResponse:
    """데이터셋 파일(JSON/Excel)을 업로드하고 즉시 활성 데이터셋으로 지정."""
    content = await file.read()
    # 파일명 앞에 타임스탬프를 붙여서, 같은 이름으로 재업로드해도 이전 업로드를 덮어쓰지 않음
    # - 그래야 각 업로드가 /api/dataset/history에서 계속 선택 가능하게 남음
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = DATASET_DIR / f"{stamp}_{file.filename}"
    path.write_bytes(content)

    try:
        cases = load_dataset(path)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    _set_active_dataset(path, len(cases))
    _append_dataset_history({
        "path": _normalize_stored_path(str(path)),
        "filename": file.filename,
        "case_count": len(cases),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    })
    return JSONResponse({
        "dataset_path": _normalize_stored_path(str(path)),
        "case_count": len(cases),
        "missing_existing_answer_count": sum(1 for case in cases if not case.existing_answer),
    })


@app.get("/api/dataset/current")
def current_dataset() -> JSONResponse:
    """현재 활성 데이터셋 정보를 반환 (대시보드 상단 지표에 표시)."""
    return JSONResponse({
        "path": ACTIVE_DATASET,
        "filename": Path(ACTIVE_DATASET).name if ACTIVE_DATASET else None,
        "is_default": not ACTIVE_DATASET,
        "case_count": ACTIVE_DATASET_CASE_COUNT,
        "error": None,
    })


@app.post("/api/dataset/reset")
def reset_dataset() -> JSONResponse:
    """Clears the active dataset selection (back to the built-in demo case).

    Deliberately does not touch upload history or the files on disk -- a user
    can always pick a previous upload again via /api/dataset/select afterward.
    """
    global ACTIVE_DATASET, ACTIVE_DATASET_CASE_COUNT
    ACTIVE_DATASET = None
    ACTIVE_DATASET_CASE_COUNT = 0
    if ACTIVE_DATASET_POINTER.exists():
        ACTIVE_DATASET_POINTER.unlink()
    return JSONResponse({"reset": True})


@app.get("/api/dataset/history")
def dataset_history() -> JSONResponse:
    """업로드 이력을 최신순으로 반환, 각 항목에 파일 존재 여부/현재 활성 여부를 함께 표시."""
    history = _load_dataset_history()
    enriched = []
    for entry in reversed(history):
        norm_path = _normalize_stored_path(entry["path"])
        enriched.append({**entry, "path": norm_path, "exists": Path(norm_path).exists(), "active": norm_path == ACTIVE_DATASET})
    return JSONResponse(enriched)


@app.post("/api/dataset/select")
def select_dataset(payload: Dict[str, Any]) -> JSONResponse:
    """Re-activates a previously uploaded dataset from history without re-uploading it."""
    path = Path(_normalize_stored_path(str(payload.get("path", ""))))
    try:
        resolved = path.resolve()
        dataset_dir_resolved = DATASET_DIR.resolve()
    except OSError:
        return JSONResponse({"error": "invalid dataset path"}, status_code=400)
    if resolved != dataset_dir_resolved and dataset_dir_resolved not in resolved.parents:
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
    _set_active_dataset(path, len(cases))
    return JSONResponse({"dataset_path": _normalize_stored_path(str(path)), "case_count": len(cases)})


@app.post("/api/dataset/delete")
def delete_dataset(payload: Dict[str, Any]) -> JSONResponse:
    """업로드 이력에서 데이터셋 파일 1건 또는 여러 건을 완전히 삭제(디스크 파일 + 이력 항목).

    payload에 "path"(단일) 또는 "paths"(배열, 체크박스 다중 삭제용) 중 하나를 받음. 이력 목록은
    한 번만 읽고 한 번만 다시 씀 - 여러 건을 개별 요청으로 나눠 보내면 각 요청이 서로의 변경을
    덮어쓰는 경합이 생길 수 있어(모두 같은 이력 파일을 읽고 쓰기 때문), 반드시 이렇게 한 번에 처리.

    지금 활성 데이터셋이 삭제 대상에 포함되면 활성 선택은 자동으로 해제됨(기본 데모 케이스로
    되돌아감) - 존재하지 않는 파일을 계속 활성 데이터셋으로 가리키고 있으면 다음 실행이 실패하기 때문.
    """
    global ACTIVE_DATASET, ACTIVE_DATASET_CASE_COUNT
    raw_paths = payload.get("paths")
    if raw_paths is None:
        raw_paths = [payload.get("path", "")]
    targets = {_normalize_stored_path(str(p)) for p in raw_paths if p}
    if not targets:
        return JSONResponse({"error": "삭제할 경로가 없습니다"}, status_code=400)

    dataset_dir_resolved = DATASET_DIR.resolve()
    deleted, invalid = [], []
    for target in targets:
        try:
            resolved = Path(target).resolve()
        except OSError:
            invalid.append(target)
            continue
        if resolved != dataset_dir_resolved and dataset_dir_resolved not in resolved.parents:
            invalid.append(target)
            continue
        if resolved.exists():
            resolved.unlink()
        deleted.append(target)

    history = _load_dataset_history()
    history = [entry for entry in history if _normalize_stored_path(entry["path"]) not in deleted]
    DATASET_HISTORY_PATH.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    was_active = ACTIVE_DATASET in deleted
    if was_active:
        ACTIVE_DATASET = None
        ACTIVE_DATASET_CASE_COUNT = 0
        if ACTIVE_DATASET_POINTER.exists():
            ACTIVE_DATASET_POINTER.unlink()

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
def dataset_preview(path: Optional[str] = None) -> JSONResponse:
    """데이터셋 내용을 표로 미리보기 (앞 200행까지). path 지정 시 이력의 그 파일, 없으면 활성 데이터셋."""
    target = _resolve_within(path, DATASET_DIR) if path else (Path(ACTIVE_DATASET) if ACTIVE_DATASET else None)
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
def dataset_download(path: Optional[str] = None) -> Response:
    """데이터셋 원본 파일을 그대로 다운로드. path 지정 시 이력의 그 파일, 없으면 활성 데이터셋."""
    target = _resolve_within(path, DATASET_DIR) if path else (Path(ACTIVE_DATASET) if ACTIVE_DATASET else None)
    if not target or not target.exists():
        return JSONResponse({"error": "잘못된 경로이거나 파일을 찾을 수 없습니다"}, status_code=404)
    return _download_response(target)


# ==================== 테스트 케이스(발화문) - 데이터셋과 별도 관리 ====================

@app.post("/api/testcase/upload")
async def upload_testcase(file: UploadFile = File(...)) -> JSONResponse:
    """테스트 케이스 파일(id+question만, 정답 없음)을 업로드하고 즉시 활성화."""
    content = await file.read()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = TESTCASE_DIR / f"{stamp}_{file.filename}"
    path.write_bytes(content)

    try:
        testcase_map = load_testcase(path)
    except (ValueError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not testcase_map:
        return JSONResponse({"error": "유효한 id/question 행이 없습니다"}, status_code=400)

    _set_active_testcase(path, len(testcase_map))
    _append_testcase_history({
        "path": _normalize_stored_path(str(path)),
        "filename": file.filename,
        "case_count": len(testcase_map),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    })
    return JSONResponse({"testcase_path": _normalize_stored_path(str(path)), "case_count": len(testcase_map)})


@app.get("/api/testcase/current")
def current_testcase() -> JSONResponse:
    """현재 활성 테스트 케이스 정보를 반환."""
    return JSONResponse({
        "path": ACTIVE_TESTCASE,
        "filename": Path(ACTIVE_TESTCASE).name if ACTIVE_TESTCASE else None,
        "is_default": not ACTIVE_TESTCASE,
        "case_count": ACTIVE_TESTCASE_CASE_COUNT,
        "error": None,
    })


@app.post("/api/testcase/reset")
def reset_testcase() -> JSONResponse:
    """활성 테스트 케이스를 해제 - 이후 실행은 데이터셋 자체의 question을 그대로 씀(하위호환)."""
    global ACTIVE_TESTCASE, ACTIVE_TESTCASE_CASE_COUNT
    ACTIVE_TESTCASE = None
    ACTIVE_TESTCASE_CASE_COUNT = 0
    if TESTCASE_ACTIVE_POINTER.exists():
        TESTCASE_ACTIVE_POINTER.unlink()
    return JSONResponse({"reset": True})


@app.get("/api/testcase/history")
def testcase_history() -> JSONResponse:
    history = _load_testcase_history()
    enriched = []
    for entry in reversed(history):
        norm_path = _normalize_stored_path(entry["path"])
        enriched.append({**entry, "path": norm_path, "exists": Path(norm_path).exists(), "active": norm_path == ACTIVE_TESTCASE})
    return JSONResponse(enriched)


@app.post("/api/testcase/select")
def select_testcase(payload: Dict[str, Any]) -> JSONResponse:
    """이력에서 이전 테스트 케이스 파일을 다시 활성화."""
    path = Path(_normalize_stored_path(str(payload.get("path", ""))))
    try:
        resolved = path.resolve()
        testcase_dir_resolved = TESTCASE_DIR.resolve()
    except OSError:
        return JSONResponse({"error": "invalid testcase path"}, status_code=400)
    if resolved != testcase_dir_resolved and testcase_dir_resolved not in resolved.parents:
        return JSONResponse({"error": "invalid testcase path"}, status_code=400)
    if not resolved.exists():
        return JSONResponse({"error": "testcase file not found"}, status_code=404)

    try:
        testcase_map = load_testcase(resolved)
    except (ValueError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    _set_active_testcase(path, len(testcase_map))
    return JSONResponse({"testcase_path": _normalize_stored_path(str(path)), "case_count": len(testcase_map)})


@app.post("/api/testcase/delete")
def delete_testcase(payload: Dict[str, Any]) -> JSONResponse:
    """업로드 이력에서 테스트 케이스 파일 1건 또는 여러 건을 완전히 삭제(디스크 파일 + 이력 항목).

    payload에 "path"(단일) 또는 "paths"(배열, 체크박스 다중 삭제용) 중 하나를 받음 - dataset/delete와
    동일한 이유로 이력 파일은 한 번만 읽고 한 번만 다시 씀."""
    global ACTIVE_TESTCASE, ACTIVE_TESTCASE_CASE_COUNT
    raw_paths = payload.get("paths")
    if raw_paths is None:
        raw_paths = [payload.get("path", "")]
    targets = {_normalize_stored_path(str(p)) for p in raw_paths if p}
    if not targets:
        return JSONResponse({"error": "삭제할 경로가 없습니다"}, status_code=400)

    testcase_dir_resolved = TESTCASE_DIR.resolve()
    deleted, invalid = [], []
    for target in targets:
        try:
            resolved = Path(target).resolve()
        except OSError:
            invalid.append(target)
            continue
        if resolved != testcase_dir_resolved and testcase_dir_resolved not in resolved.parents:
            invalid.append(target)
            continue
        if resolved.exists():
            resolved.unlink()
        deleted.append(target)

    history = _load_testcase_history()
    history = [entry for entry in history if _normalize_stored_path(entry["path"]) not in deleted]
    TESTCASE_HISTORY_PATH.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    was_active = ACTIVE_TESTCASE in deleted
    if was_active:
        ACTIVE_TESTCASE = None
        ACTIVE_TESTCASE_CASE_COUNT = 0
        if TESTCASE_ACTIVE_POINTER.exists():
            TESTCASE_ACTIVE_POINTER.unlink()

    return JSONResponse({"deleted": deleted, "invalid": invalid, "was_active": was_active})


@app.get("/api/testcase/template")
def testcase_template() -> Response:
    """빈 테스트 케이스 양식(id/question 2열)을 다운로드."""
    workbook = build_testcase_template_workbook()
    return StreamingResponse(iter([workbook.getvalue()]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=testcase_template.xlsx"})


@app.get("/api/testcase/preview")
def testcase_preview(path: Optional[str] = None) -> JSONResponse:
    """테스트 케이스 내용을 표로 미리보기 (앞 200행까지). path 지정 시 이력의 그 파일, 없으면 활성 테스트 케이스."""
    target = _resolve_within(path, TESTCASE_DIR) if path else (Path(ACTIVE_TESTCASE) if ACTIVE_TESTCASE else None)
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
def testcase_download(path: Optional[str] = None) -> Response:
    """테스트 케이스 원본 파일을 그대로 다운로드. path 지정 시 이력의 그 파일, 없으면 활성 테스트 케이스."""
    target = _resolve_within(path, TESTCASE_DIR) if path else (Path(ACTIVE_TESTCASE) if ACTIVE_TESTCASE else None)
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


def _execute_run(run_id: str, techniques: List[str], category_filter: Optional[str], payload: Dict[str, Any]) -> None:
    """백그라운드 스레드에서 실제 파이프라인을 실행 (POST /api/run이 이 함수를 스레드로 띄움).

    예외가 나도 여기서 잡아 RUN_REGISTRY에 status="error"로 기록합니다 - 스레드가 죽어도
    폴링하는 클라이언트에게는 정상적으로 실패 상태가 보여야 하기 때문입니다.
    """
    with RUN_LOCK:
        RUN_REGISTRY[run_id]["status"] = "running"

    try:
        # 클라이언트가 dataset_path/testcase_path 키를 명시적으로 보냈다면(화면에 보이는 활성
        # 값을 그대로 실어 보낸 것) 그 값을 그대로 신뢰 - 서버의 ACTIVE_* 전역값은 다른 탭/사용자가
        # 그 사이에 바꿨을 수 있어서, 화면에 보이던 것과 실제로 채점된 값이 어긋날 수 있음.
        # 키 자체가 없는 예전 클라이언트/외부 API 호출은 기존처럼 서버의 현재 활성 값을 씀.
        no_dataset = bool(payload.get("no_dataset"))
        testcase_path = payload["testcase_path"] if "testcase_path" in payload else ACTIVE_TESTCASE
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
            dataset_path = payload["dataset_path"] if "dataset_path" in payload else ACTIVE_DATASET
            if dataset_path:
                dataset_path = _normalize_stored_path(dataset_path)
            cases = load_dataset(dataset_path) if dataset_path else []
            if not cases:
                cases = _default_cases()
            if testcase_map:
                # 테스트 케이스(발화문)가 활성화돼 있으면 id가 일치하는 데이터셋 케이스의 question만
                # 그 발화문으로 덮어씀 - id가 테스트 케이스에 없는 데이터셋 케이스는 이번 실행에서 제외(스킵)
                cases = [dataclasses.replace(c, question=testcase_map[c.id]) for c in cases if c.id in testcase_map]

        config = Config(reports_dir="reports")
        connector_payload = payload.get("connector") or {}
        if payload.get("mode"):
            connector_payload.setdefault("mode", payload["mode"])
        if payload.get("api_endpoint"):
            connector_payload.setdefault("api_endpoint", payload["api_endpoint"])
        for key, value in connector_payload.items():
            if hasattr(config.connector, key) and value not in (None, ""):
                setattr(config.connector, key, value)
        # LLM 연동 provider별로 설정 필드가 달라서(custom=key_name/base_url 직접 지정,
        # anthropic/openai=키만 지정) 분기 처리. "none"이면 아무 키도 안 넣어 완전히 비활성화됨
        provider = (payload.get("llm_provider") or "openai").lower()
        config.llm_judge["provider"] = provider
        if provider == "custom":
            config.llm_judge["api_key"] = payload.get("llm_key_value") or ""
            config.llm_judge["key_name"] = payload.get("llm_key_name") or "Authorization"
            config.llm_judge["base_url"] = payload.get("llm_endpoint") or ""
        elif provider == "anthropic":
            api_key = payload.get("llm_key_value") or payload.get("anthropic_api_key")
            if api_key:
                config.llm_judge["api_key"] = api_key
        elif provider != "none":
            api_key = payload.get("llm_key_value") or payload.get("openai_api_key")
            if api_key:
                config.llm_judge["api_key"] = api_key
        if payload.get("llm_model"):
            config.llm_judge["model"] = payload["llm_model"]

        def on_progress(done: int, total: int) -> None:
            with RUN_LOCK:
                RUN_REGISTRY[run_id]["progress"] = {"completed": done, "total": total}

        report = PipelineOrchestrator(config).run(cases, category_filter=category_filter, techniques=techniques, run_id=run_id, on_progress=on_progress, dataset_path=dataset_path, testcase_path=testcase_path)
        write_reports(report, reports_dir="reports")
        write_defect_report_doc(report.to_dict(), docs_dir="docs")

        jira_tickets: List[Dict[str, Any]] = []
        if not payload.get("no_jira"):
            jira_config = payload.get("jira_config") or {}
            jira_tickets = JiraNotifier({"enabled": bool(jira_config.get("base_url") or jira_config.get("email") or jira_config.get("api_token")), **jira_config}).notify(report.to_dict())
            if jira_tickets:
                (Path("reports") / f"jira_{run_id}.json").write_text(json.dumps(jira_tickets, indent=2), encoding="utf-8")

        if payload.get("slack_webhook_url"):
            SlackNotifier(payload["slack_webhook_url"]).notify(report.to_dict())
        if payload.get("discord_webhook_url"):
            DiscordNotifier(payload["discord_webhook_url"]).notify(report.to_dict())
        if payload.get("teams_webhook_url"):
            TeamsNotifier(payload["teams_webhook_url"]).notify(report.to_dict())

        with RUN_LOCK:
            RUN_REGISTRY[run_id]["status"] = "done"
            RUN_REGISTRY[run_id]["result"] = report.to_dict()
            RUN_REGISTRY[run_id]["jira_tickets"] = jira_tickets
    except Exception as exc:
        with RUN_LOCK:
            RUN_REGISTRY[run_id]["status"] = "error"
            RUN_REGISTRY[run_id]["error"] = str(exc)


@app.post("/api/run")
def run_pipeline(payload: Optional[Dict[str, Any]] = None) -> JSONResponse:
    """파이프라인 실행을 시작 - 백그라운드 스레드로 실행하고 즉시 run_id를 반환.

    진행 상황/결과는 GET /api/run/{run_id}/status, /result로 폴링해서 조회.
    """
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

    run_id = f"run_{len(RUN_REGISTRY) + 1}"
    with RUN_LOCK:
        RUN_REGISTRY[run_id] = {"status": "queued", "progress": {"completed": 0, "total": 1}, "result": None, "error": None, "jira_tickets": []}

    thread = Thread(target=_execute_run, args=(run_id, techniques, payload.get("category"), payload), daemon=True)
    thread.start()

    return JSONResponse({"run_id": run_id, "status": RUN_REGISTRY[run_id]["status"]})


@app.get("/api/run/{run_id}/status")
def run_status(run_id: str) -> JSONResponse:
    """실행 상태/진행률 폴링용 (queued/running/done/error). 결과 본문은 안 담아 응답을 가볍게 유지."""
    entry = RUN_REGISTRY.get(run_id, {"status": "error", "progress": {"completed": 0, "total": 1}, "error": "not found"})
    entry = dict(entry)
    entry.setdefault("run_id", run_id)
    entry.pop("result", None)
    return JSONResponse(entry)


@app.get("/api/run/{run_id}/result")
def run_result(run_id: str) -> JSONResponse:
    """완료된 실행의 전체 리포트를 조회 (아직 안 끝났거나 없으면 404)."""
    entry = RUN_REGISTRY.get(run_id)
    if not entry or entry.get("result") is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(entry["result"])


def _safe_run_path(run_id: str) -> Optional[Path]:
    """run_id가 "run_" 접두사만 가진 안전한 값인지 확인 - 경로 조작(../ 등) 방지."""
    if not run_id.startswith("run_") or "/" in run_id or "\\" in run_id or ".." in run_id:
        return None
    path = Path("reports") / f"run_{run_id}.json"
    return path if path.exists() else None


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> JSONResponse:
    """디스크에 저장된 과거 실행 리포트를 직접 조회 (대시보드의 "실행 선택" 드롭다운이 사용)."""
    path = _safe_run_path(run_id)
    if not path:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/jira/tickets")
def jira_tickets() -> JSONResponse:
    """모든 실행에서 생성된 Jira 티켓을 모아서 반환 (_execute_run이 jira_{run_id}.json으로 저장해둔 것)."""
    tickets: List[Dict[str, Any]] = []
    for path in sorted(Path("reports").glob("jira_run_*.json")):
        run_id = path.stem[len("jira_"):]
        try:
            for ticket in json.loads(path.read_text(encoding="utf-8")):
                tickets.append({"run_id": run_id, **ticket})
        except Exception:
            continue
    return JSONResponse(tickets)


@app.get("/api/reports/latest")
def latest_report(format: str = "json") -> Response:
    """최신 리포트를 JSON/CSV/마크다운 중 원하는 형식으로 다운로드."""
    latest_path = Path("reports") / "latest.json"
    export_dir = Path("reports") / "exports"
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
def save_settings(payload: Dict[str, Any]) -> JSONResponse:
    """알림/Jira/LLM 설정을 저장 - 파일에도 쓰고, 현재 프로세스의 환경변수에도 즉시 반영."""
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for key, value in payload.items():
        if value:
            os.environ[str(key).upper()] = str(value)
    return JSONResponse({"saved": True, "path": str(SETTINGS_PATH)})


@app.get("/api/settings")
def load_settings() -> JSONResponse:
    """저장된 설정을 조회 - 파일에 없으면 환경변수 값으로 보충."""
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
    return JSONResponse(settings)


@app.get("/api/runs")
def runs() -> JSONResponse:
    """실행 이력 요약 목록 (대시보드의 통과율 추이 차트/실행 선택 드롭다운이 사용)."""
    return JSONResponse(list_run_history(reports_dir="reports"))


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
        def monitoring_addon_page() -> str:
            """모니터링 애드온 전용 페이지 - 기존 대시보드(index.html)와는 완전히 별도인 신규 페이지."""
            html_path = Path(__file__).with_name("templates").joinpath("monitoring_addon.html")
            html = html_path.read_text(encoding="utf-8")
            return html.replace("__GRAFANA_LINK_ENABLED__", "true" if GRAFANA_LINK_ENABLED else "false")
    except Exception as e:
        print(f"[monitoring-addon] router load skipped: {e}")
