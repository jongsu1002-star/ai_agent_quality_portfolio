"""VOC 자동분석 REST API - 게시판 VOC 글 + (선택)Jira 백로그 + (선택)엑셀 업로드를 모아
LLM 분석을 동기 실행하고 결과를 전역 저장(reports/voc_analysis/)함.

app/main.py와의 결합은 monitoring_addon.py/board.py와 동일하게 `configure()` 의존성
주입 방식을 씀(순환 임포트 회피).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response

from qa_agent.board import BoardStore
from qa_agent.excel_io import build_voc_import_template_workbook, load_voc_excel
from qa_agent.jira_client import fetch_backlog_issues
from qa_agent.llm_client import OpenAIJudgeClient
from qa_agent.voc_analysis import MAX_ITEMS_FOR_PROMPT, run_voc_analysis_with_judge

router = APIRouter(prefix="/api/voc-analysis")

VOC_ANALYSIS_DIR = Path("reports") / "voc_analysis"
VOC_UPLOAD_DIR = VOC_ANALYSIS_DIR / "uploads"

_state: Dict[str, Any] = {"store": None, "current_username": None, "load_settings": None, "llm_kwargs": None, "independent_judge_kwargs": None}


def configure(store: BoardStore, current_username_fn, load_settings_fn, llm_kwargs_fn, independent_judge_kwargs_fn) -> None:
    _state["store"] = store
    _state["current_username"] = current_username_fn
    _state["load_settings"] = load_settings_fn
    _state["llm_kwargs"] = llm_kwargs_fn
    _state["independent_judge_kwargs"] = independent_judge_kwargs_fn


def _store() -> BoardStore:
    return _state["store"]


def _username(request: Request) -> str:
    return _state["current_username"](request)


def _resolve_upload_path(raw_path: str) -> Optional[Path]:
    """경로 조작(../ 등) 방지 - VOC_UPLOAD_DIR 안의 파일만 허용."""
    try:
        resolved = Path(raw_path).resolve()
        base = VOC_UPLOAD_DIR.resolve()
        resolved.relative_to(base)
    except (ValueError, OSError):
        return None
    return resolved if resolved.exists() else None


@router.get("/template")
def voc_template() -> Response:
    """VOC 외부 데이터(엑셀) 업로드용 양식 다운로드."""
    workbook = build_voc_import_template_workbook()
    return Response(
        workbook.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=voc_import_template.xlsx"},
    )


@router.post("/excel/upload")
async def upload_voc_excel(file: UploadFile = File(...)) -> JSONResponse:
    """VOC 외부 데이터 엑셀 업로드 - 파싱해 미리보기까지 함께 반환."""
    VOC_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = VOC_UPLOAD_DIR / f"{stamp}_{file.filename}"
    path.write_bytes(content)
    try:
        rows = load_voc_excel(path)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not rows:
        return JSONResponse({"error": "유효한 content가 있는 행이 없습니다"}, status_code=400)
    return JSONResponse({"excel_path": str(path), "row_count": len(rows), "preview": rows[:5]})


@router.get("/jira-preview")
def jira_preview(jql: Optional[str] = None, max_results: int = 50) -> JSONResponse:
    """분석 실행 전, Jira에서 실제로 어떤 이슈가 조회되는지 미리 확인."""
    settings = _state["load_settings"]()
    jira_config = {
        "base_url": settings.get("jira_base_url"),
        "email": settings.get("jira_email"),
        "api_token": settings.get("jira_token"),
        "project_key": settings.get("jira_project"),
    }
    try:
        issues = fetch_backlog_issues(jira_config, jql=jql, max_results=max_results)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"Jira 조회 실패: {exc}"}, status_code=502)
    return JSONResponse({"issues": issues, "count": len(issues)})


@router.post("/run")
def run_analysis(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """게시판 VOC 글(항상 포함) + 선택적 Jira/엑셀 소스를 모아 LLM 분석을 동기 실행."""
    board_posts = _store().list_posts("voc", limit=300, include_hidden=False)

    jira_issues: List[Dict[str, Any]] = []
    if payload.get("use_jira"):
        settings = _state["load_settings"]()
        jira_config = {
            "base_url": settings.get("jira_base_url"),
            "email": settings.get("jira_email"),
            "api_token": settings.get("jira_token"),
            "project_key": settings.get("jira_project"),
        }
        try:
            jira_issues = fetch_backlog_issues(jira_config, jql=payload.get("jira_jql"), max_results=payload.get("jira_max_results", 50))
        except Exception as exc:
            return JSONResponse({"error": f"Jira 조회 실패: {exc}"}, status_code=502)

    excel_rows: List[Dict[str, Any]] = []
    if payload.get("use_excel") and payload.get("excel_path"):
        resolved = _resolve_upload_path(payload["excel_path"])
        if not resolved:
            return JSONResponse({"error": "invalid excel_path"}, status_code=400)
        try:
            excel_rows = load_voc_excel(resolved)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    settings = _state["load_settings"]()
    llm_kwargs = _state["llm_kwargs"](settings)
    generation_client = OpenAIJudgeClient(**llm_kwargs)
    if not generation_client.enabled:
        return JSONResponse({"error": "LLM이 설정되지 않았습니다 (설정 탭에서 LLM 연동을 먼저 구성하세요)"}, status_code=400)

    # 독립 Judge: 생성에 쓴 provider와 가능하면 다른 provider로 결과를 재검증(자기평가 편향
    # 방지). 교차검증용 키가 없으면 judge_client가 비활성 상태로 만들어지고, run_independent_judge가
    # 이를 감지해 verdict="SKIPPED"로 정직하게 표시함(호출 자체를 건너뜀 - LLM 비용 낭비 방지)
    independent_kwargs, cross_model = _state["independent_judge_kwargs"](settings)
    judge_client = OpenAIJudgeClient(**independent_kwargs)

    focus_instruction = str(payload.get("focus_instruction") or "")
    # 사용자가 지정한 건수 제한("최근 20건만") - 1~MAX_ITEMS_FOR_PROMPT 범위로 clamp,
    # 미지정이면 기본 상한 그대로 사용
    raw_item_limit = payload.get("item_limit")
    item_limit = MAX_ITEMS_FOR_PROMPT
    if raw_item_limit:
        try:
            item_limit = max(1, min(int(raw_item_limit), MAX_ITEMS_FOR_PROMPT))
        except (TypeError, ValueError):
            return JSONResponse({"error": "item_limit은 숫자여야 합니다"}, status_code=400)

    try:
        result = run_voc_analysis_with_judge(
            generation_client, judge_client, board_posts, jira_issues, excel_rows,
            focus_instruction=focus_instruction, item_limit=item_limit, cross_model=cross_model,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"VOC 분석 실패: {exc}"}, status_code=502)

    VOC_ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    analysis_id = f"voc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    record = {
        "id": analysis_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": _username(request),
        "params": {
            "use_jira": bool(payload.get("use_jira")),
            "jira_jql": payload.get("jira_jql"),
            "use_excel": bool(payload.get("use_excel")),
            "excel_path": payload.get("excel_path"),
            "focus_instruction": focus_instruction or None,
            "item_limit": item_limit,
        },
        "result": result,
    }
    (VOC_ANALYSIS_DIR / f"{analysis_id}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return JSONResponse(record)


@router.get("/history")
def analysis_history() -> JSONResponse:
    if not VOC_ANALYSIS_DIR.exists():
        return JSONResponse([])
    records = []
    for path in sorted(VOC_ANALYSIS_DIR.glob("voc_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records.append({"id": data["id"], "created_at": data["created_at"], "created_by": data.get("created_by"), "summary": data.get("result", {}).get("summary", "")})
        except Exception:
            continue
    return JSONResponse(records)


@router.get("/{analysis_id}")
def analysis_detail(analysis_id: str) -> JSONResponse:
    if "/" in analysis_id or "\\" in analysis_id or ".." in analysis_id:
        return JSONResponse({"error": "invalid id"}, status_code=400)
    path = VOC_ANALYSIS_DIR / f"{analysis_id}.json"
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
