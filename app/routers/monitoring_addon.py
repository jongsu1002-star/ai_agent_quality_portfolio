"""모니터링 애드온 REST API - 기존 app/main.py의 어떤 엔드포인트도 대체하지 않습니다.

`app/main.py`가 순환 임포트 없이 기존 `METRICS`/`HealthChecker`/DB 인스턴스를 이 모듈에
주입할 수 있도록 `configure()`를 통한 의존성 주입 방식을 씁니다(이 모듈이 `app.main`을
직접 import하지 않음 - app.main이 이 모듈을 import하는 단방향 관계만 존재).

모든 핸들러는 예외를 여기서 잡아 `{"error": ...}` + 500으로 변환합니다 - DB 오류 등 애드온
내부 문제가 FastAPI의 기본 에러 처리 경로를 통해 다른 요청에 영향을 주지 않도록 하기 위함이며,
동시에 사용자에게도 이해하기 쉬운 형태로 실패 원인을 보여주기 위함입니다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from monitoring_addon.config import K6_HISTORY_ENABLED, MONITORING_ADDON_DB_ENABLED
from monitoring_addon.k6_result_importer import import_latest_k6_result
from monitoring_addon.k6_result_reader import K6ResultInvalid, K6ResultNotFound, read_k6_result
from monitoring_addon.k6_runner import (
    K6_RUN_MANAGER,
    validate_duration,
    validate_path,
    validate_target_url,
    validate_utterance,
    validate_vus,
)
from monitoring_addon.prometheus_exporter import render_prometheus_text
from monitoring_addon.schemas import shape_k6_run, shape_k6_run_list

router = APIRouter(prefix="/api/monitoring-addon")
metrics_router = APIRouter()  # /metrics-addon은 /api 하위가 아닌 별도 최상위 경로라 라우터를 분리

_state: Dict[str, Any] = {"db": None, "metrics": None, "health_checker": None}


def configure(metrics, health_checker, db) -> None:
    """app/main.py가 자신이 이미 갖고 있는 METRICS/HealthChecker/DB 인스턴스를 주입."""
    _state["metrics"] = metrics
    _state["health_checker"] = health_checker
    _state["db"] = db


@router.get("/k6/latest")
def k6_latest() -> JSONResponse:
    """최신 k6 실행 결과. DB가 비어있으면 reports/k6/latest.json에서 읽어 import까지 시도."""
    try:
        db = _state["db"]
        if not MONITORING_ADDON_DB_ENABLED or db is None:
            # DB 없이 파일만 직접 읽어 반환 (K6_HISTORY_ENABLED=false와 무관하게 latest는 항상 조회 가능)
            run = read_k6_result(Path("reports/k6/latest.json"))
            return JSONResponse(run)

        # DB가 비어있을 때뿐 아니라 매번 시도 - reports/k6/latest.json이 더 최신 run_id로
        # 갱신돼 있으면(예: 방금 새 k6 실행이 끝난 경우) 그것도 import해서 반영해야 함.
        # import_latest_k6_result는 같은 run_id면 그냥 스킵하므로 매번 불러도 비용이 작음.
        imported = import_latest_k6_result(db)
        if imported.get("status") == "invalid_json" and db.get_latest_k6_run() is None:
            return JSONResponse(imported)

        latest = db.get_latest_k6_run()
        if latest is None:
            return JSONResponse({"status": "no_data"})
        return JSONResponse(shape_k6_run(latest))
    except K6ResultNotFound:
        return JSONResponse({"status": "no_data"})
    except K6ResultInvalid as exc:
        return JSONResponse({"status": "invalid_json", "error": str(exc)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/k6/runs")
def k6_runs(page: int = 1, page_size: int = 10, result: Optional[str] = None) -> JSONResponse:
    """k6 실행 이력 목록 (최신순 페이지네이션, result=Pass/Fail로 필터 가능)."""
    try:
        db = _state["db"]
        if not K6_HISTORY_ENABLED or not MONITORING_ADDON_DB_ENABLED or db is None:
            return JSONResponse({"status": "history_disabled", "items": [], "total": 0})
        items, total = db.list_k6_runs(page=page, page_size=page_size, result_filter=result)
        return JSONResponse({"items": shape_k6_run_list(items), "page": page, "page_size": page_size, "total": total})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/k6/runs/{run_id}")
def k6_run_detail(run_id: str) -> JSONResponse:
    """특정 k6 실행 상세 (threshold 상세 포함)."""
    try:
        db = _state["db"]
        if not K6_HISTORY_ENABLED or not MONITORING_ADDON_DB_ENABLED or db is None:
            return JSONResponse({"status": "history_disabled"}, status_code=404)
        row = db.get_k6_run(run_id)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(shape_k6_run(row))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/k6/import")
def k6_import() -> JSONResponse:
    """reports/k6/latest.json을 수동으로 DB에 import (같은 run_id면 중복 저장하지 않음)."""
    try:
        db = _state["db"]
        if not MONITORING_ADDON_DB_ENABLED or db is None:
            return JSONResponse({"status": "db_disabled"})
        return JSONResponse(import_latest_k6_result(db))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/history/summary")
def history_summary(limit: int = 60) -> JSONResponse:
    """기존 MetricsCollector 요약값을 1분 주기로 읽기 전용 저장해둔 장기 스냅샷 조회."""
    try:
        db = _state["db"]
        if not MONITORING_ADDON_DB_ENABLED or db is None:
            return JSONResponse({"status": "db_disabled", "items": []})
        return JSONResponse({"items": db.recent_snapshots(limit=limit)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/k6/trigger")
def k6_trigger(payload: Dict[str, Any]) -> JSONResponse:
    """웹에서 k6 성능테스트를 직접 실행 - 대상은 localhost/사내망 IP로만 제한, 동시 실행 불가."""
    try:
        target_url = str(payload.get("target_url", "")).strip()
        vus = int(payload.get("vus", 10) or 10)
        duration = str(payload.get("duration", "30s")).strip()
        path = str(payload.get("path") or "/").strip()
        utterance = str(payload.get("utterance") or "").strip()
        request_field = str(payload.get("request_field") or "message").strip()

        for error in (
            validate_target_url(target_url),
            validate_vus(vus),
            validate_duration(duration),
            validate_path(path),
            validate_utterance(utterance),
        ):
            if error:
                return JSONResponse({"error": error}, status_code=400)

        error = K6_RUN_MANAGER.start(target_url, vus, duration, path, utterance, request_field)
        if error:
            return JSONResponse({"error": error}, status_code=409)
        return JSONResponse({"status": "started"})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/k6/trigger/status")
def k6_trigger_status() -> JSONResponse:
    """진행 중인(또는 방금 끝난) 웹 트리거 k6 실행의 상태 폴링."""
    try:
        return JSONResponse(K6_RUN_MANAGER.get_status())
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@metrics_router.get("/metrics-addon")
def metrics_addon() -> PlainTextResponse:
    """Prometheus가 스크레이프할 텍스트 노출 포맷. 기존 /api/monitoring/summary는 건드리지 않음."""
    try:
        metrics = _state["metrics"]
        summary = metrics.summary() if metrics is not None else {}
        db = _state["db"]
        latest_run = db.get_latest_k6_run() if (MONITORING_ADDON_DB_ENABLED and db is not None) else None
        try:
            # VOC 지표 계산이 실패해도(예: docs/테스트_결과.md 형식 변경) 기존 서버/k6
            # 지표까지 통째로 500이 되면 안 되므로 별도로 감싼다.
            from app.routers.voc_analysis import get_quality_metrics_for_prometheus

            voc_quality = get_quality_metrics_for_prometheus()
        except Exception:
            voc_quality = None
        text = render_prometheus_text(summary, latest_run, voc_quality)
        return PlainTextResponse(text, media_type="text/plain; version=0.0.4")
    except Exception as exc:
        return PlainTextResponse(f"# error generating metrics: {exc}\n", status_code=500)
