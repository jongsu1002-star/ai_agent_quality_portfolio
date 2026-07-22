"""VOC 자동분석 REST API - 게시판 VOC 글 + (선택)Jira 백로그 + (선택)엑셀 업로드를 모아
LLM 분석을 동기 실행하고 결과를 전역 저장(reports/voc_analysis/)함.

app/main.py와의 결합은 monitoring_addon.py/board.py와 동일하게 `configure()` 의존성
주입 방식을 씀(순환 임포트 회피).
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from qa_agent import error_log
from qa_agent.board import BoardStore
from qa_agent.excel_io import (
    build_voc_import_template_workbook,
    build_voc_json_template,
    load_voc_excel,
    load_voc_json,
)
from qa_agent.jira_client import fetch_backlog_issues
from qa_agent.llm_client import OpenAIJudgeClient
from qa_agent.voc_analysis import (
    MAX_ITEMS_FOR_PROMPT,
    VocAnalysisCanceled,
    run_cross_validation_matrix,
    run_voc_analysis_with_judge,
)

router = APIRouter(prefix="/api/voc-analysis")
logger = logging.getLogger(__name__)

# focus_instruction 길이 상한 - 프롬프트에 실어 보내는 자유 텍스트라 지나치게 길면
# 다른 필드(VOC 원문 등)를 밀어내거나 토큰 비용을 불필요하게 늘림. 2000자는 실제
# 사용 사례("~중심으로 분석해줘" 수준의 문장 몇 개)보다 넉넉한 상한.
FOCUS_INSTRUCTION_MAX_LENGTH = 2000

# 실제 OpenAI/Anthropic API 키는 대략 50~160자 수준이라, 300자면 어떤 실제 키 형식도
# 넉넉히 수용하면서 비정상적으로 큰 페이로드(악의적이든 실수든)는 요청 단계에서 거부한다.
API_KEY_OVERRIDE_MAX_LENGTH = 300


class VocRunRequest(BaseModel):
    """POST /run, /run-async 공용 요청 모델 - 기존 Dict[str, Any] 방식을 대체.

    item_limit만 예외적으로 엄격한 타입을 강제하지 않고 Any로 받는다 - 기존 정책("1
    미만/불리언은 400 거부, 150 초과는 조용히 클램프")을 그대로 유지해야 하는 하위호환
    요구사항이 있어서, 이 필드만 _prepare_voc_run 안의 기존 수동 검증 로직을 그대로 쓴다
    (Pydantic이 자동으로 422를 내려버리면 기존 400 계약이 깨짐). 나머지 필드는 Pydantic이
    표준적으로 타입/범위를 강제(위반 시 422)."""

    use_board: bool = True
    use_jira: bool = False
    jira_jql: Optional[str] = None
    jira_max_results: int = Field(default=50, ge=1, le=200)
    use_excel: bool = False
    excel_path: Optional[str] = None
    focus_instruction: Optional[str] = Field(default=None, max_length=FOCUS_INSTRUCTION_MAX_LENGTH)
    item_limit: Optional[Any] = None
    # 아래 3개는 POST /cross-validation-matrix 전용(다른 엔드포인트는 무시함) - 공용
    # 모델에 얹은 이유는 이미 매트릭스 엔드포인트가 이 모델을 그대로 쓰고 있어서, 별도
    # 모델을 새로 만드는 것보다 필드 몇 개 추가하는 쪽이 더 단순함.
    groups: Optional[List[str]] = None  # 실행할 조합(A~D) 부분집합, 미지정 시 4개 전부
    openai_api_key: Optional[str] = Field(default=None, max_length=API_KEY_OVERRIDE_MAX_LENGTH)  # 이번 실행에서만 쓸 키(비우면 설정 화면 저장값 사용)
    anthropic_api_key: Optional[str] = Field(default=None, max_length=API_KEY_OVERRIDE_MAX_LENGTH)

    @field_validator("focus_instruction", "openai_api_key", "anthropic_api_key")
    @classmethod
    def _strip_optional_text_fields(cls, value: Optional[str]) -> Optional[str]:
        # Pydantic이 str 타입 자체는 이미 강제해줌(객체/배열/불리언 등은 여기 도달하기
        # 전에 422로 거부됨) - 여기서는 앞뒤 공백만 정리하고, 공백만 있던 값은 "지정 안
        # 함"과 동일하게 취급해 이후 로직이 빈 문자열/None을 매번 따로 신경 쓰지 않게 함.
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


# 백그라운드(비동기) 실행 registry - app/main.py::RUN_REGISTRY(QA 파이프라인)와 동일한
# 패턴을 VOC 분석에도 적용. 동기 엔드포인트(POST /run)는 하위 호환을 위해 그대로 두고,
# 이건 완전히 별도의 추가 경로(POST /run-async)라 기존 동작에는 영향이 없음.
# username -> run_id -> {status, result, error, canceled, created_at, finished_at}
VOC_RUN_REGISTRY: Dict[str, Dict[str, Dict[str, Any]]] = {}
VOC_RUN_LOCK = Lock()

# 사용자별 동시 실행 상한(고정) - 한 사용자가 여러 개를 동시에 큐에 넣어 자원을 독점하는
# 것을 막음. 완료된 작업까지 포함한 "보관" 상한과 정리 주기, 그리고 전역 워커 수는
# 배포 환경에 따라 조정할 수 있게 환경변수로 노출하되 안전한 기본값을 둠.
VOC_RUN_MAX_CONCURRENT_PER_USER = 1
VOC_RUN_MAX_STORED_PER_USER = int(os.getenv("VOC_RUN_MAX_STORED_PER_USER", "20"))
VOC_RUN_FINISHED_TTL_SECONDS = int(os.getenv("VOC_RUN_FINISHED_TTL_SECONDS", str(6 * 3600)))
VOC_RUN_MAX_WORKERS = int(os.getenv("VOC_RUN_MAX_WORKERS", "4"))

# 요청마다 Thread(daemon=True)를 무제한으로 만들던 것을 고정 크기 풀로 제한 - 동시에
# 몰리는 요청이 많아도 실제로 병렬 실행되는 LLM 파이프라인은 최대 VOC_RUN_MAX_WORKERS개뿐이고,
# 나머지는 풀 내부 큐에서 "대기"하며 VOC_RUN_REGISTRY 상 상태는 그대로 queued로 남는다
# (다중 uvicorn worker 프로세스 구성에서는 이 registry/executor가 프로세스별로 따로
# 생기므로, 이 기능은 단일 worker 프로세스 배포를 전제로 함 - 설계서/운영 문서 참고).
VOC_RUN_EXECUTOR = ThreadPoolExecutor(max_workers=VOC_RUN_MAX_WORKERS, thread_name_prefix="voc-analysis-run")
atexit.register(lambda: VOC_RUN_EXECUTOR.shutdown(wait=False, cancel_futures=True))

VOC_ANALYSIS_DIR = Path("reports") / "voc_analysis"
VOC_UPLOAD_DIR = VOC_ANALYSIS_DIR / "uploads"

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB - VOC 엑셀 업로드는 몇백 행 수준이면 충분, 대용량 업로드로
                                     # 메모리를 고갈시키는 것을 막기 위한 상한
ALLOWED_UPLOAD_EXTENSIONS = {".xlsx", ".json"}  # qa_agent/excel_io.py::load_voc_excel/load_voc_json과 반드시 일치시킬 것
ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",  # 브라우저/OS에 따라 엑셀 파일에 이 MIME이 붙는 경우가 흔함
    "application/json",
    "text/json",
}

_state: Dict[str, Any] = {"store": None, "current_username": None, "load_settings": None, "llm_kwargs": None, "independent_judge_kwargs": None, "is_admin": None, "app_version": None}


def configure(store: BoardStore, current_username_fn, load_settings_fn, llm_kwargs_fn, independent_judge_kwargs_fn, is_admin_fn, app_version_fn=None) -> None:
    _state["store"] = store
    _state["current_username"] = current_username_fn
    _state["load_settings"] = load_settings_fn
    _state["llm_kwargs"] = llm_kwargs_fn
    _state["independent_judge_kwargs"] = independent_judge_kwargs_fn
    _state["is_admin"] = is_admin_fn
    _state["app_version"] = app_version_fn or (lambda: {"server_started_at": None, "git_sha": None})


def _store() -> BoardStore:
    return _state["store"]


def _username(request: Request) -> str:
    return _state["current_username"](request)


def _user_analysis_dir(username: str) -> Path:
    """인증 미사용(shared)은 기존 경로를 유지하고, 계정 사용 시 결과를 사용자별로 격리."""
    if username == "shared":
        return VOC_ANALYSIS_DIR
    return VOC_ANALYSIS_DIR / "users" / username


def _user_upload_dir(username: str) -> Path:
    if username == "shared":
        return VOC_UPLOAD_DIR
    return _user_analysis_dir(username) / "uploads"


def _resolve_upload_path(raw_path: str, username: str = "shared") -> Optional[Path]:
    """경로 조작(../ 등) 방지 - VOC_UPLOAD_DIR 안의 파일만 허용."""
    try:
        resolved = Path(raw_path).resolve()
        base = _user_upload_dir(username).resolve()
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


@router.get("/template.json")
def voc_template_json() -> Response:
    """VOC 외부 데이터(JSON) 업로드용 양식 다운로드 - 엑셀 양식과 동일한 필드."""
    return Response(
        build_voc_json_template(),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=voc_import_template.json"},
    )


def _safe_upload_filename(filename: str) -> str:
    """Path(...).name으로 경로 구분자를 제거해 베이스네임만 남김 - "../../etc/passwd" 같은
    입력도 안전한 마지막 세그먼트로 축소됨(디렉터리 탈출/임의 경로 쓰기 방지)."""
    return Path(filename or "").name or "upload.xlsx"


@router.post("/excel/upload")
async def upload_voc_excel(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    """VOC 외부 데이터 업로드(.xlsx 또는 .json) - 파싱해 미리보기까지 함께 반환.

    저장 전에 확장자/MIME/용량을 먼저 걸러내고, 파싱 실패나 유효한 행이 없는 경우 저장했던
    파일을 즉시 삭제한다(디스크에 검증 실패한 파일이 남지 않도록). 엔드포인트 경로는 하위
    호환을 위해 /excel/upload를 유지하지만 실제로는 두 형식을 모두 받는다."""
    safe_name = _safe_upload_filename(file.filename or "")
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        return JSONResponse({"error": f"지원하지 않는 파일 형식입니다({extension or '확장자 없음'}). .xlsx 또는 .json만 지원합니다."}, status_code=400)
    if file.content_type and file.content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        return JSONResponse({"error": f"지원하지 않는 파일 형식입니다(Content-Type: {file.content_type})"}, status_code=400)

    # 크기 제한보다 1바이트 더 읽어서, 정확히 상한과 같은 파일은 통과시키면서도 그보다 큰
    # 파일은 전체를 메모리에 다 올리지 않고 바로 걸러냄
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": f"파일이 너무 큽니다(최대 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)"}, status_code=413)
    if not content:
        return JSONResponse({"error": "빈 파일입니다"}, status_code=400)

    upload_dir = _user_upload_dir(_username(request))
    upload_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = upload_dir / f"{stamp}_{safe_name}"
    path.write_bytes(content)
    loader = load_voc_json if extension == ".json" else load_voc_excel
    try:
        rows = loader(path)
        if not rows:
            path.unlink(missing_ok=True)
            return JSONResponse({"error": "유효한 content가 있는 행이 없습니다"}, status_code=400)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        path.unlink(missing_ok=True)
        logger.exception("VOC file parsing failed")
        return JSONResponse({"error": "파일을 처리하지 못했습니다. 파일 형식과 내용을 확인하세요."}, status_code=400)
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
    except Exception:
        logger.exception("VOC Jira preview failed")
        return JSONResponse({"error": "Jira 조회에 실패했습니다. 연결 설정과 권한을 확인하세요."}, status_code=502)
    return JSONResponse({"issues": issues, "count": len(issues)})


def _gather_voc_sources(payload: VocRunRequest, request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    """게시판/Jira/엑셀 소스 수집 - 동기(/run)/비동기(/run-async)/교차검증 매트릭스
    (/cross-validation-matrix) 세 실행 경로가 공유하는 부분(LLM 클라이언트 구성 이전
    단계까지). 성공하면 (sources, None), 소스 조회 실패면 (None, 에러 응답)."""
    board_posts: List[Dict[str, Any]] = []
    if payload.use_board:
        board_posts = _store().list_posts("voc", limit=300, include_hidden=False)

    jira_issues: List[Dict[str, Any]] = []
    if payload.use_jira:
        settings = _state["load_settings"]()
        jira_config = {
            "base_url": settings.get("jira_base_url"),
            "email": settings.get("jira_email"),
            "api_token": settings.get("jira_token"),
            "project_key": settings.get("jira_project"),
        }
        try:
            jira_issues = fetch_backlog_issues(jira_config, jql=payload.jira_jql, max_results=payload.jira_max_results)
        except Exception:
            logger.exception("VOC Jira retrieval failed")
            return None, JSONResponse({"error": "Jira 조회에 실패했습니다. 연결 설정과 권한을 확인하세요."}, status_code=502)

    excel_rows: List[Dict[str, Any]] = []
    if payload.use_excel and not payload.excel_path:
        return None, JSONResponse({"error": "엑셀 사용을 선택했지만 업로드된 파일이 없습니다"}, status_code=400)
    if payload.use_excel and payload.excel_path:
        resolved = _resolve_upload_path(payload.excel_path, _username(request))
        if not resolved:
            return None, JSONResponse({"error": "invalid excel_path"}, status_code=400)
        loader = load_voc_json if resolved.suffix.lower() == ".json" else load_voc_excel
        try:
            excel_rows = loader(resolved)
        except ValueError as exc:
            return None, JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            logger.exception("VOC external file loading failed")
            return None, JSONResponse({"error": "업로드 데이터를 불러오지 못했습니다. 파일을 다시 업로드하세요."}, status_code=400)

    return {"board_posts": board_posts, "jira_issues": jira_issues, "excel_rows": excel_rows}, None


def _prepare_voc_run(payload: VocRunRequest, request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    """검증 + 소스 수집(게시판/Jira/엑셀) + LLM 클라이언트 구성 - 동기(/run)와 비동기
    (/run-async) 두 실행 경로가 완전히 동일한 준비 과정을 공유한다(로직 중복/드리프트
    방지). 성공하면 (prepared, None), 검증 실패나 소스 조회 실패면 (None, 에러 응답)을
    반환 - 호출부는 에러가 있으면 그대로 반환하면 된다.

    payload는 Dict[str, Any] 대신 VocRunRequest(Pydantic)로 받는다 - focus_instruction의
    타입/길이/공백은 이미 Pydantic이 강제했고(비-문자열 값은 여기 도달하기 전에 422),
    jira_max_results도 이미 1~200 범위로 강제됐다. item_limit만 기존 하위호환 정책
    (1 미만/불리언 400 거부, 150 초과 클램프)을 유지하려고 여기서 수동 검증한다."""
    sources, error = _gather_voc_sources(payload, request)
    if error is not None:
        return None, error
    board_posts = sources["board_posts"]
    jira_issues = sources["jira_issues"]
    excel_rows = sources["excel_rows"]

    settings = _state["load_settings"]()
    llm_kwargs = _state["llm_kwargs"](settings)
    generation_client = OpenAIJudgeClient(**llm_kwargs)
    if not generation_client.enabled:
        return None, JSONResponse({"error": "LLM이 설정되지 않았습니다 (설정 탭에서 LLM 연동을 먼저 구성하세요)"}, status_code=400)

    # 독립 Judge: 생성에 쓴 provider와 가능하면 다른 provider로 결과를 재검증(자기평가 편향
    # 방지). 교차검증용 키가 없으면 judge_client가 비활성 상태로 만들어지고, run_independent_judge가
    # 이를 감지해 verdict="SKIPPED"로 정직하게 표시함(호출 자체를 건너뜀 - LLM 비용 낭비 방지)
    independent_kwargs, cross_model = _state["independent_judge_kwargs"](settings)
    judge_client = OpenAIJudgeClient(**independent_kwargs)

    focus_instruction = payload.focus_instruction or ""  # Pydantic 검증기가 이미 strip/공백-only는 None으로 정리해둠
    # 사용자가 지정한 건수 제한("최근 20건만") - 1~MAX_ITEMS_FOR_PROMPT 범위로 clamp,
    # 미지정이면 기본 상한 그대로 사용. item_limit은 기존 하위호환 정책을 유지해야 해서
    # Pydantic Field 제약 대신 여기서 수동으로 검증(위 docstring 참고).
    raw_item_limit = payload.item_limit
    item_limit = MAX_ITEMS_FOR_PROMPT
    if raw_item_limit is not None:
        try:
            if isinstance(raw_item_limit, bool):
                raise ValueError
            parsed_item_limit = int(raw_item_limit)
        except (TypeError, ValueError):
            return None, JSONResponse({"error": "item_limit은 숫자여야 합니다"}, status_code=400)
        if parsed_item_limit < 1:
            return None, JSONResponse({"error": "item_limit은 1 이상이어야 합니다"}, status_code=400)
        item_limit = min(parsed_item_limit, MAX_ITEMS_FOR_PROMPT)

    prepared = {
        "board_posts": board_posts,
        "jira_issues": jira_issues,
        "excel_rows": excel_rows,
        "generation_client": generation_client,
        "judge_client": judge_client,
        "cross_model": cross_model,
        "focus_instruction": focus_instruction,
        "item_limit": item_limit,
        "username": _username(request),
        "params": {
            "use_board": payload.use_board,
            "use_jira": payload.use_jira,
            "jira_jql": payload.jira_jql,
            "use_excel": payload.use_excel,
            "excel_path": payload.excel_path,
            "focus_instruction": focus_instruction or None,
            "item_limit": item_limit,
        },
    }
    return prepared, None


def _build_and_save_analysis_record(prepared: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """분석 결과를 기록으로 감싸 원자적으로 저장 - 동기/비동기 두 경로가 공유하는
    동일한 저장 형식(analysis_id/created_at/created_by 등)."""
    username = prepared["username"]
    analysis_dir = _user_analysis_dir(username)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    # 초 단위 타임스탬프만 쓰면 같은 초에 완료된 두 요청의 결과 파일이 서로 덮어써서 감사
    # 기록이 유실될 수 있었음(실제로 지적된 결함) - 마이크로초 + uuid 접미사로 사실상
    # 충돌 불가능하게 하면서도, 파일명 앞부분은 여전히 타임스탬프라 정렬/식별이 쉬움
    analysis_id = f"voc_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
    record = {
        "id": analysis_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": username,
        "app_version": _state["app_version"](),
        "params": prepared["params"],
        "result": result,
    }
    _write_analysis_record_atomically(analysis_id, record, analysis_dir)
    return record


@router.post("/run")
def run_analysis(payload: VocRunRequest, request: Request) -> JSONResponse:
    """게시판 VOC 글 + 선택적 Jira/엑셀 소스를 모아 LLM 분석을 동기 실행(요청을 끝까지
    붙들고 있음 - 수십 초 소요될 수 있음). 응답을 기다리지 않고 폴링하려면 POST
    /run-async를 쓸 것(같은 준비 로직을 공유하는 별도 경로, 하위 호환을 위해 이 동기
    경로는 그대로 유지).

    use_board 기본값은 True(아무것도 지정하지 않으면 게시판 VOC를 씀 - 기존 동작과 동일한
    하위호환 기본값). Jira/엑셀을 추가로 켤 때는 게시판 VOC를 포함할지 여부를 명시적으로
    선택할 수 있음(use_board=false로 게시판을 빼고 외부 소스만 분석하는 것도 가능)."""
    prepared, error = _prepare_voc_run(payload, request)
    if error is not None:
        return error

    try:
        result = run_voc_analysis_with_judge(
            prepared["generation_client"], prepared["judge_client"],
            prepared["board_posts"], prepared["jira_issues"], prepared["excel_rows"],
            focus_instruction=prepared["focus_instruction"], item_limit=prepared["item_limit"],
            cross_model=prepared["cross_model"],
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("VOC analysis generation failed")
        error_log.record_error("voc_analysis", exc, username=prepared["username"])
        return JSONResponse({"error": "VOC 분석 처리에 실패했습니다. LLM 설정과 서버 로그를 확인하세요."}, status_code=502)

    try:
        record = _build_and_save_analysis_record(prepared, result)
    except Exception as exc:
        # 생성 자체는 성공했는데 저장 단계(디스크 가득 참, 권한 오류 등)에서 실패한 경우 -
        # 비동기 경로(_execute_voc_analysis_async)와 동일한 결함 클래스라 같은 방식으로
        # 방어함(내부 경로/예외 원문은 노출하지 않고 상세는 로그에만).
        logger.exception("VOC analysis result save failed")
        error_log.record_error("voc_analysis_save", exc, username=prepared["username"])
        return JSONResponse({"error": "분석 결과를 저장하지 못했습니다. 서버 로그를 확인하거나 관리자에게 문의하세요."}, status_code=502)
    return JSONResponse(record)


# ===================== 교차검증 매트릭스 (A/B/C/D) =====================
# 운영용 단일 실행 경로(POST /run, /run-async)와 별개로, 같은 VOC 데이터를 OpenAI/
# Anthropic 양쪽으로 각각 생성한 뒤 4가지 생성×평가 조합을 전부 비교하는 실험/비교
# 모드. 저장 형식이 일반 분석 결과(voc_*.json)와 다르므로(result.summary가 아니라
# result.matrix), 같은 이력/상세 화면이 오작동하지 않도록 별도 하위 폴더에 완전히
# 분리해서 저장한다(_all_analysis_roots()의 voc_*.json glob과 겹치지 않는 접두사).
VOC_XVAL_DIR = VOC_ANALYSIS_DIR / "cross_validation"


def _user_xval_dir(username: str) -> Path:
    if username == "shared":
        return VOC_XVAL_DIR
    return VOC_XVAL_DIR / "users" / username


def _all_xval_roots() -> List[Path]:
    if (VOC_XVAL_DIR / "users").exists():
        return [VOC_XVAL_DIR] + sorted((VOC_XVAL_DIR / "users").glob("*"))
    return [VOC_XVAL_DIR]


def _find_xval_file(analysis_id: str) -> Optional[Path]:
    if not analysis_id.startswith("vocxval_") or "/" in analysis_id or "\\" in analysis_id or ".." in analysis_id:
        return None
    for root in _all_xval_roots():
        candidate = root / f"{analysis_id}.json"
        if candidate.exists():
            return candidate
    return None


def _build_and_save_xval_record(username: str, params: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    xval_dir = _user_xval_dir(username)
    xval_dir.mkdir(parents=True, exist_ok=True)
    analysis_id = f"vocxval_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
    record = {
        "id": analysis_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": username,
        "app_version": _state["app_version"](),
        "params": params,
        "result": result,
    }
    _write_analysis_record_atomically(analysis_id, record, xval_dir)
    return record


def _build_matrix_clients(
    settings: Dict[str, Any],
    openai_api_key_override: Optional[str] = None,
    anthropic_api_key_override: Optional[str] = None,
) -> Tuple[OpenAIJudgeClient, OpenAIJudgeClient]:
    """설정에서 OpenAI/Anthropic 클라이언트를 각각 명시적으로 구성 - "현재 선택된
    provider" 개념과 무관하게 둘 다 만들어야 하므로 _independent_judge_kwargs(반대쪽
    하나만 고르는 함수)는 재사용하지 않는다. provider가 이미 고정돼 있어 llm_model 등
    다른 provider의 설정이 새어 들어갈 여지도 없음(1차 리뷰 결함 2번과 동일 클래스의
    오염을 애초에 피하는 설계).

    *_api_key_override: 매트릭스 화면에서 이번 실행에 한해 직접 입력한 키(선택) - 설정
    화면에 키를 저장하지 않고도, 또는 저장된 키와 다른 키로 즉석에서 비교 실행하고 싶을
    때 쓴다. 비어 있으면 기존과 동일하게 설정에 저장된 키를 그대로 쓴다. 호출부(라우트)가
    이 값을 저장 이력(params)에 절대 포함하지 않도록 책임진다 - 매트릭스 이력은 전원
    공개라, 여기 흘러들어가면 다른 사용자에게 키가 그대로 노출된다."""
    openai_client = OpenAIJudgeClient(provider="openai", api_key=openai_api_key_override or settings.get("openai_api_key"))
    anthropic_client = OpenAIJudgeClient(provider="anthropic", api_key=anthropic_api_key_override or settings.get("anthropic_api_key"))
    return openai_client, anthropic_client


_CROSS_VALIDATION_GROUP_LETTERS = ("A", "B", "C", "D")


def _prepare_xval_run(payload: VocRunRequest, request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    """검증 + 소스 수집 + 클라이언트 구성 - 동기(POST /cross-validation-matrix)와 비동기
    (POST .../run-async) 두 실행 경로가 완전히 동일한 준비 과정을 공유한다(_prepare_voc_run과
    동일한 이유: 로직 중복/드리프트 방지). 성공하면 (prepared, None), 검증 실패면
    (None, 에러 응답)을 반환."""
    sources, error = _gather_voc_sources(payload, request)
    if error is not None:
        return None, error

    groups: Optional[List[str]] = None
    if payload.groups is not None:
        normalized_groups = [str(g).strip().upper() for g in payload.groups]
        invalid = [g for g in normalized_groups if g not in _CROSS_VALIDATION_GROUP_LETTERS]
        if invalid:
            return None, JSONResponse({"error": f"groups에 알 수 없는 조합이 포함되어 있습니다: {invalid} (A/B/C/D만 가능)"}, status_code=400)
        if not normalized_groups:
            return None, JSONResponse({"error": "groups를 지정하는 경우 최소 1개 이상 선택해야 합니다"}, status_code=400)
        # 원래 표시 순서(A→D)로 정렬하고 중복은 제거
        groups = [g for g in _CROSS_VALIDATION_GROUP_LETTERS if g in normalized_groups]

    settings = _state["load_settings"]()
    openai_client, anthropic_client = _build_matrix_clients(
        settings,
        openai_api_key_override=payload.openai_api_key,
        anthropic_api_key_override=payload.anthropic_api_key,
    )

    focus_instruction = payload.focus_instruction or ""
    raw_item_limit = payload.item_limit
    item_limit = MAX_ITEMS_FOR_PROMPT
    if raw_item_limit is not None:
        try:
            if isinstance(raw_item_limit, bool):
                raise ValueError
            parsed_item_limit = int(raw_item_limit)
        except (TypeError, ValueError):
            return None, JSONResponse({"error": "item_limit은 숫자여야 합니다"}, status_code=400)
        if parsed_item_limit < 1:
            return None, JSONResponse({"error": "item_limit은 1 이상이어야 합니다"}, status_code=400)
        item_limit = min(parsed_item_limit, MAX_ITEMS_FOR_PROMPT)

    prepared = {
        "openai_client": openai_client,
        "anthropic_client": anthropic_client,
        "board_posts": sources["board_posts"],
        "jira_issues": sources["jira_issues"],
        "excel_rows": sources["excel_rows"],
        "focus_instruction": focus_instruction,
        "item_limit": item_limit,
        "groups": groups,
        "username": _username(request),
        "params": {
            "use_board": payload.use_board,
            "use_jira": payload.use_jira,
            "jira_jql": payload.jira_jql,
            "use_excel": payload.use_excel,
            "excel_path": payload.excel_path,
            "focus_instruction": focus_instruction or None,
            "item_limit": item_limit,
            "groups": groups or list(_CROSS_VALIDATION_GROUP_LETTERS),
            # openai_api_key/anthropic_api_key는 의도적으로 여기 포함하지 않음(전원 공개 이력에 새는 것 방지)
        },
    }
    return prepared, None


@router.post("/cross-validation-matrix")
def run_cross_validation(payload: VocRunRequest, request: Request) -> JSONResponse:
    """같은 VOC 데이터로 OpenAI/Anthropic을 각각 생성·평가에 돌려 A(OpenAI 생성/Anthropic
    평가)·B(Anthropic 생성/OpenAI 평가)·C(OpenAI 생성/OpenAI 평가, 대조군)·D(Anthropic
    생성/Anthropic 평가, 대조군) 4가지 조합을 비교하는 실험 모드. 운영용 POST /run과
    달리 OpenAI/Anthropic 키가 **모두** 설정되어 있어야 하고, LLM 호출이 늘어나(생성
    2회 + 평가 4회) 더 오래 걸릴 수 있다. 요청 본문 형식은 POST /run과 동일(item_limit
    정책도 동일하게 수동 검증). 응답을 기다리지 않고 폴링하려면 POST .../run-async를
    쓸 것(같은 준비 로직을 공유하는 별도 경로 - VOC 분석의 /run vs /run-async와 동일한
    관계, 하위 호환을 위해 이 동기 경로는 그대로 유지).

    payload.groups: A~D 중 이번 실행에 포함할 조합(선택, 미지정 시 4개 전부). 일부만
    고르면 실제로 필요한 provider만 생성 호출을 수행해 비용/시간을 아낀다(예: A만 고르면
    OpenAI 생성 1회 + Anthropic 평가 1회로 끝남).
    payload.openai_api_key / anthropic_api_key: 이번 실행에서만 쓸 키(선택, 비우면
    설정 화면에 저장된 키 사용) - 저장 이력(params)에는 절대 포함하지 않는다."""
    prepared, error = _prepare_xval_run(payload, request)
    if error is not None:
        return error

    try:
        result = run_cross_validation_matrix(
            prepared["openai_client"], prepared["anthropic_client"],
            prepared["board_posts"], prepared["jira_issues"], prepared["excel_rows"],
            focus_instruction=prepared["focus_instruction"], item_limit=prepared["item_limit"], groups=prepared["groups"],
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("VOC cross-validation matrix failed")
        error_log.record_error("voc_cross_validation_matrix", exc, username=prepared["username"])
        return JSONResponse({"error": "교차검증 매트릭스 처리에 실패했습니다. LLM 설정과 서버 로그를 확인하세요."}, status_code=502)

    try:
        record = _build_and_save_xval_record(prepared["username"], prepared["params"], result)
    except Exception as exc:
        logger.exception("VOC cross-validation matrix result save failed")
        error_log.record_error("voc_cross_validation_matrix_save", exc, username=prepared["username"])
        return JSONResponse({"error": "매트릭스 결과를 저장하지 못했습니다. 서버 로그를 확인하거나 관리자에게 문의하세요."}, status_code=502)
    return JSONResponse(record)


def _execute_xval_async(username: str, run_id: str, prepared: Dict[str, Any]) -> None:
    """백그라운드 스레드 풀(VOC_RUN_EXECUTOR)에서 실제 교차검증 매트릭스를 실행(POST
    .../run-async가 이 함수를 제출) - _execute_voc_analysis_async와 완전히 동일한 패턴
    (생성/저장 각 단계의 예외를 전부 잡아 registry에 status="error"로 기록해 status가
    "running"에 영원히 고착되는 일이 없게 함). VOC_RUN_REGISTRY를 그대로 공유하므로
    사용자당 동시 실행 제한도 일반 VOC 분석과 함께 적용된다(동시에 둘 다 돌리지 않음 -
    둘 다 LLM 호출이 많아 자원을 아끼는 방향이 안전함)."""
    with VOC_RUN_LOCK:
        VOC_RUN_REGISTRY[username][run_id]["status"] = "running"

    def _on_stage(stage: str) -> None:
        with VOC_RUN_LOCK:
            VOC_RUN_REGISTRY[username][run_id]["stage"] = stage

    try:
        result = run_cross_validation_matrix(
            prepared["openai_client"], prepared["anthropic_client"],
            prepared["board_posts"], prepared["jira_issues"], prepared["excel_rows"],
            focus_instruction=prepared["focus_instruction"], item_limit=prepared["item_limit"],
            groups=prepared["groups"], on_stage=_on_stage,
        )
    except ValueError as exc:
        _finish_voc_run(username, run_id, "error", error=str(exc))
        return
    except Exception as exc:
        logger.exception("VOC cross-validation matrix failed (async run_id=%s)", run_id)
        error_log.record_error("voc_cross_validation_matrix", exc, username=username, run_id=run_id)
        _finish_voc_run(username, run_id, "error", error="교차검증 매트릭스 처리에 실패했습니다. LLM 설정과 서버 로그를 확인하세요.")
        return

    _on_stage("저장 중")
    try:
        record = _build_and_save_xval_record(username, prepared["params"], result)
    except Exception as exc:
        logger.exception("VOC cross-validation matrix result save failed (async run_id=%s)", run_id)
        error_log.record_error("voc_cross_validation_matrix_save", exc, username=username, run_id=run_id)
        _finish_voc_run(username, run_id, "error", error="매트릭스 결과를 저장하지 못했습니다. 서버 로그를 확인하거나 관리자에게 문의하세요.")
        return

    _finish_voc_run(username, run_id, "done", result=record)


@router.post("/cross-validation-matrix/run-async")
def run_cross_validation_async(payload: VocRunRequest, request: Request) -> JSONResponse:
    """교차검증 매트릭스를 백그라운드로 제출하고 즉시 run_id를 반환 - POST /run-async(일반
    VOC 분석)와 완전히 동일한 패턴. 같은 VOC_RUN_REGISTRY/VOC_RUN_EXECUTOR를 공유하므로
    사용자당 동시 실행 1건 제한도 함께 적용된다(이미 VOC 분석이나 다른 매트릭스가
    queued/running이면 409 + active_run_id)."""
    prepared, error = _prepare_xval_run(payload, request)
    if error is not None:
        return error

    username = prepared["username"]
    with VOC_RUN_LOCK:
        _cleanup_voc_registry_locked(username)
        user_runs = VOC_RUN_REGISTRY.setdefault(username, {})
        active_id = next((rid for rid, entry in user_runs.items() if entry["status"] in ("queued", "running")), None)
        if active_id is not None:
            return JSONResponse(
                {"error": "이미 실행 중인 VOC 분석/교차검증 매트릭스가 있습니다. 완료된 뒤 다시 시도하세요.", "active_run_id": active_id},
                status_code=409,
            )
        run_id = f"vocxval_async_{uuid.uuid4().hex[:12]}"
        user_runs[run_id] = {
            "status": "queued", "result": None, "error": None, "canceled": False,
            "created_at": time.time(), "finished_at": None,
        }

    VOC_RUN_EXECUTOR.submit(_execute_xval_async, username, run_id, prepared)
    return JSONResponse({"run_id": run_id, "status": "queued"})


@router.get("/cross-validation-matrix/run-async/{run_id}/status")
def cross_validation_async_status(run_id: str, request: Request) -> JSONResponse:
    """실행 상태 폴링용(queued/running/done/error) - GET /run-async/{id}/status(일반 VOC
    분석)와 동일한 응답 형태."""
    username = _username(request)
    with VOC_RUN_LOCK:
        entry = VOC_RUN_REGISTRY.get(username, {}).get(run_id)
        if entry is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({
            "run_id": run_id, "status": entry["status"], "error": entry.get("error"),
            "finished_at": entry.get("finished_at"), "stage": entry.get("stage"),
        })


@router.get("/cross-validation-matrix/run-async/{run_id}/result")
def cross_validation_async_result(run_id: str, request: Request) -> JSONResponse:
    """완료된 비동기 매트릭스 실행의 전체 결과 조회(아직 안 끝났거나 없으면 404) - 동기
    POST /cross-validation-matrix와 완전히 동일한 record 형식을 반환하므로 프론트는 두
    경로를 같은 렌더 함수로 처리 가능."""
    username = _username(request)
    with VOC_RUN_LOCK:
        entry = VOC_RUN_REGISTRY.get(username, {}).get(run_id)
        if not entry or entry.get("result") is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(entry["result"])


@router.get("/cross-validation-matrix/history")
def cross_validation_history(request: Request) -> JSONResponse:
    """모든 실행자의 교차검증 매트릭스 이력 - 일반 VOC 분석 이력과 동일하게 전원 공개.

    반드시 GET /cross-validation-matrix/{analysis_id}(아래)보다 먼저 등록돼야 함 -
    FastAPI는 등록 순서대로 매칭을 시도하므로, 뒤에 있으면 "history"라는 문자열이
    {analysis_id} 경로변수로 잘못 잡아먹힘(아래 상세 라우트와 동일한 이유로 이 라우트도
    반드시 DELETE/GET용 진짜 catch-all(`/{analysis_id}`, 파일 뒷부분)보다 앞서 있어야 함)."""
    records = []
    for root in _all_xval_roots():
        if not root.exists():
            continue
        for path in root.glob("vocxval_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                matrix = data.get("result", {}).get("matrix", [])
                records.append({
                    "id": data["id"],
                    "created_at": data["created_at"],
                    "created_by": data.get("created_by"),
                    "gate_summary": {entry["group"]: entry.get("quality_gate", {}).get("status") for entry in matrix},
                })
            except Exception:
                continue
    records.sort(key=lambda r: r["created_at"], reverse=True)
    return JSONResponse(records)


@router.get("/cross-validation-matrix/{analysis_id}")
def cross_validation_detail(analysis_id: str, request: Request) -> JSONResponse:
    if "/" in analysis_id or "\\" in analysis_id or ".." in analysis_id:
        return JSONResponse({"error": "invalid id"}, status_code=400)
    path = _find_xval_file(analysis_id)
    if not path:
        return JSONResponse({"error": "not found"}, status_code=404)
    record = json.loads(path.read_text(encoding="utf-8"))
    # P1-2와 동일한 정책: excel_path는 전원 제외, jira_jql/focus_instruction은 실행자/관리자만
    return JSONResponse(_sanitize_analysis_detail_for_viewer(record, request))


@router.delete("/cross-validation-matrix/{analysis_id}")
def delete_cross_validation(analysis_id: str, request: Request) -> JSONResponse:
    """관리자 또는 실행한 본인만 삭제 가능 - 일반 VOC 분석 이력 삭제 정책과 동일."""
    path = _find_xval_file(analysis_id)
    if not path:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return JSONResponse({"error": "not found"}, status_code=404)
    is_owner = record.get("created_by") == _username(request)
    if not (is_owner or _state["is_admin"](request)):
        return JSONResponse({"error": "forbidden (admin or the executor who ran it only)"}, status_code=403)
    path.unlink()
    return JSONResponse({"deleted": True})


def _finish_voc_run(username: str, run_id: str, status: str, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
    """비동기 실행의 종료 상태(done/error/canceled) 기록을 한 곳으로 모음 - 세 가지
    종료 경로(취소/생성 실패/저장 실패/성공) 모두 반드시 이 함수를 거치게 해서
    finished_at 기록 누락이나 status="running" 고착을 구조적으로 방지한다."""
    with VOC_RUN_LOCK:
        entry = VOC_RUN_REGISTRY.get(username, {}).get(run_id)
        if entry is None:
            return  # cleanup 등으로 이미 registry에서 지워진 뒤(정상적인 레이스, 무시)
        entry["status"] = status
        entry["result"] = result
        entry["error"] = error
        entry["finished_at"] = time.time()


def _execute_voc_analysis_async(username: str, run_id: str, prepared: Dict[str, Any]) -> None:
    """백그라운드 스레드 풀(VOC_RUN_EXECUTOR)에서 실제 VOC 분석을 실행(POST /run-async가
    이 함수를 제출) - app/main.py::_execute_run(QA 파이프라인)과 동일한 패턴. 생성/저장
    각 단계의 예외를 전부 잡아 registry에 status="error"로 기록하므로, 어느 단계에서
    실패하든 클라이언트에게는 정상적으로 실패 상태가 보이고 status가 "running"에
    영원히 고착되는 일이 없다(과거 결함: 저장 단계 예외가 이 함수 밖으로 새 나가
    스레드만 조용히 죽고 registry는 running에 멈춰 있었음)."""
    with VOC_RUN_LOCK:
        VOC_RUN_REGISTRY[username][run_id]["status"] = "running"

    def _should_cancel() -> bool:
        with VOC_RUN_LOCK:
            return VOC_RUN_REGISTRY[username][run_id].get("canceled", False)

    def _on_stage(stage: str) -> None:
        with VOC_RUN_LOCK:
            VOC_RUN_REGISTRY[username][run_id]["stage"] = stage

    try:
        result = run_voc_analysis_with_judge(
            prepared["generation_client"], prepared["judge_client"],
            prepared["board_posts"], prepared["jira_issues"], prepared["excel_rows"],
            focus_instruction=prepared["focus_instruction"], item_limit=prepared["item_limit"],
            cross_model=prepared["cross_model"], should_cancel=_should_cancel, on_stage=_on_stage,
        )
    except VocAnalysisCanceled:
        _finish_voc_run(username, run_id, "canceled")
        return
    except ValueError as exc:
        _finish_voc_run(username, run_id, "error", error=str(exc))
        return
    except Exception as exc:
        logger.exception("VOC analysis generation failed (async run_id=%s)", run_id)
        error_log.record_error("voc_analysis", exc, username=username, run_id=run_id)
        _finish_voc_run(username, run_id, "error", error="VOC 분석 처리에 실패했습니다. LLM 설정과 서버 로그를 확인하세요.")
        return

    # 저장 단계도 반드시 여기서 잡아야 함 - 생성은 성공했는데 디스크 가득 참/권한 오류 등으로
    # 저장이 실패하면, 이 try/except 없이는 예외가 그대로 밖으로 새 나가 status가
    # "running"에 영원히 고착됨(폴링하는 클라이언트가 영원히 끝나기를 기다리게 됨).
    _on_stage("저장 중")
    try:
        record = _build_and_save_analysis_record(prepared, result)
    except Exception as exc:
        logger.exception("VOC analysis result save failed (async run_id=%s)", run_id)
        error_log.record_error("voc_analysis_save", exc, username=username, run_id=run_id)
        _finish_voc_run(username, run_id, "error", error="분석 결과를 저장하지 못했습니다. 서버 로그를 확인하거나 관리자에게 문의하세요.")
        return

    _finish_voc_run(username, run_id, "done", result=record)


def _cleanup_voc_registry_locked(username: str) -> None:
    """VOC_RUN_LOCK을 이미 보유한 상태에서만 호출 - 실행 중(queued/running) 작업은
    절대 건드리지 않고, 종료된(done/error/canceled) 작업만 두 기준으로 정리한다:
    ① finished_at이 TTL(VOC_RUN_FINISHED_TTL_SECONDS)을 넘긴 것 ② 그러고도 사용자당
    보관 개수가 VOC_RUN_MAX_STORED_PER_USER를 넘으면 오래된 것부터 제거. registry가
    무한정 커지며 메모리를 갉아먹는 것을 막는 용도(완료돼도 아무도 안 지우면 프로세스가
    떠 있는 한 계속 쌓이는 구조였음)."""
    user_runs = VOC_RUN_REGISTRY.get(username)
    if not user_runs:
        return
    now = time.time()
    finished_statuses = ("done", "error", "canceled")

    for rid in [rid for rid, entry in user_runs.items() if entry["status"] in finished_statuses]:
        finished_at = user_runs[rid].get("finished_at")
        if finished_at is not None and (now - finished_at) > VOC_RUN_FINISHED_TTL_SECONDS:
            del user_runs[rid]

    finished_ids = sorted(
        (rid for rid, entry in user_runs.items() if entry["status"] in finished_statuses),
        key=lambda rid: user_runs[rid].get("finished_at") or 0,
    )
    excess = len(finished_ids) - VOC_RUN_MAX_STORED_PER_USER
    for rid in finished_ids[:max(excess, 0)]:
        del user_runs[rid]


def _cleanup_voc_registry(username: str) -> None:
    """_cleanup_voc_registry_locked의 락 획득 버전 - 테스트 등 VOC_RUN_LOCK을 아직
    보유하지 않은 외부 호출부용(락을 이미 쥔 상태에서 이 함수를 부르면 데드락이므로,
    run_analysis_async 내부에서는 반드시 _locked 버전을 직접 호출할 것)."""
    with VOC_RUN_LOCK:
        _cleanup_voc_registry_locked(username)


@router.post("/run-async")
def run_analysis_async(payload: VocRunRequest, request: Request) -> JSONResponse:
    """VOC 분석을 백그라운드 스레드 풀(VOC_RUN_EXECUTOR)에 제출하고 즉시 run_id를
    반환(QA 파이프라인의 POST /api/run과 동일한 패턴). 진행 상태는 GET
    /run-async/{run_id}/status로, 완료된 결과는 GET /run-async/{run_id}/result로
    폴링해서 조회. 요청 검증(LLM 미설정, 엑셀 경로 오류 등)은 스레드를 띄우기 전에
    동기적으로 먼저 수행 - 어차피 실패할 요청을 위해 스레드를 만들 필요가 없고,
    사용자도 400/502를 폴링 없이 즉시 받는다.

    사용자당 동시 실행은 VOC_RUN_MAX_CONCURRENT_PER_USER(1)로 제한 - 이미 queued나
    running인 작업이 있으면 409를 반환하고 새 작업은 만들지 않는다(무제한 스레드 생성/
    메모리 증가 방지의 핵심 방어선). 등록 자체는 VOC_RUN_LOCK 아래 원자적으로 처리해
    동시에 들어온 두 요청이 동시성 검사를 모두 통과해버리는 경쟁 조건을 막는다."""
    prepared, error = _prepare_voc_run(payload, request)
    if error is not None:
        return error

    username = prepared["username"]
    with VOC_RUN_LOCK:
        _cleanup_voc_registry_locked(username)
        user_runs = VOC_RUN_REGISTRY.setdefault(username, {})
        active_id = next((rid for rid, entry in user_runs.items() if entry["status"] in ("queued", "running")), None)
        if active_id is not None:
            return JSONResponse(
                {"error": "이미 실행 중인 VOC 분석이 있습니다. 완료된 뒤 다시 시도하세요.", "active_run_id": active_id},
                status_code=409,
            )
        run_id = f"voc_async_{uuid.uuid4().hex[:12]}"
        user_runs[run_id] = {
            "status": "queued", "result": None, "error": None, "canceled": False,
            "created_at": time.time(), "finished_at": None,
        }

    VOC_RUN_EXECUTOR.submit(_execute_voc_analysis_async, username, run_id, prepared)
    return JSONResponse({"run_id": run_id, "status": "queued"})


@router.get("/run-async/{run_id}/status")
def voc_run_async_status(run_id: str, request: Request) -> JSONResponse:
    """실행 상태 폴링용(queued/running/done/error/canceled). 결과 본문은 담지 않아 응답을 가볍게 유지."""
    username = _username(request)
    with VOC_RUN_LOCK:
        entry = VOC_RUN_REGISTRY.get(username, {}).get(run_id)
        if entry is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({
            "run_id": run_id, "status": entry["status"], "error": entry.get("error"),
            "finished_at": entry.get("finished_at"), "stage": entry.get("stage"),
        })


@router.get("/run-async/{run_id}/result")
def voc_run_async_result(run_id: str, request: Request) -> JSONResponse:
    """완료된 비동기 실행의 전체 결과 조회(아직 안 끝났거나 없으면 404) - 동기 /run과
    완전히 동일한 record 형식을 반환하므로 프론트는 두 경로를 같은 렌더 함수로 처리 가능."""
    username = _username(request)
    with VOC_RUN_LOCK:
        entry = VOC_RUN_REGISTRY.get(username, {}).get(run_id)
        if not entry or entry.get("result") is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(entry["result"])


@router.post("/run-async/{run_id}/cancel")
def voc_run_async_cancel(run_id: str, request: Request) -> JSONResponse:
    """실행 중인 백그라운드 분석에 취소를 요청 - 이미 시작된 개별 LLM 호출 자체를 끊지는
    못하지만(현재 스택으로는 불가능), 아직 시작하지 않은 다음 단계(생성 -> 내부재점검 ->
    독립Judge)로 넘어가는 것은 막아서 클라이언트가 취소를 누르면 실질적으로 남은 LLM
    호출·대기 시간이 최대 1단계분으로 줄어든다."""
    username = _username(request)
    with VOC_RUN_LOCK:
        entry = VOC_RUN_REGISTRY.get(username, {}).get(run_id)
        if entry is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if entry["status"] in ("done", "error", "canceled"):
            return JSONResponse({"canceled": False, "reason": f"already {entry['status']}"})
        entry["canceled"] = True
    return JSONResponse({"canceled": True})


def _write_analysis_record_atomically(analysis_id: str, record: Dict[str, Any], analysis_dir: Optional[Path] = None) -> None:
    """같은 디렉터리에 임시 파일로 먼저 쓴 뒤 os.replace()로 원자적 교체 - 쓰는 도중에
    프로세스가 죽거나 같은 파일을 읽는 다른 요청이 있어도 반쯤 쓰인 JSON을 보는 일이 없음.

    쓰기나 교체 도중 실패하면(디스크 가득 참, 권한 오류 등) 임시 파일을 정리한 뒤 예외를
    다시 던진다 - 호출부(동기 /run, 비동기 백그라운드 실행 모두)가 이 예외를 잡아 사용자
    에게는 일반화된 오류만 보여주고 상세는 로그에 남기지만, 그 전에 `.{id}.json.tmp`
    잔여 파일이 디스크에 계속 남는 것은 여기서 막는다."""
    target_dir = analysis_dir or VOC_ANALYSIS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / f"{analysis_id}.json"
    tmp_path = target_dir / f".{analysis_id}.json.tmp"
    try:
        tmp_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, final_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


DOCS_DIR = Path("docs")
EXPORTS_DIR = Path("reports") / "exports"

# VOC 분석 탭 하단 "점검 범위" 차트가 보여주는 파일 -> 표시 라벨 매핑(순서 유지).
# docs/테스트_결과.md의 "파일별 결과" 표에서 이 파일들의 건수만 합산해서 쓴다.
_LAYER_FILE_LABELS = [
    ("tests/test_voc_analysis.py", "함수 단위(voc_analysis)"),
    ("tests/test_voc_analysis_api.py", "HTTP API(voc_analysis)"),
    ("tests/test_board_api.py", "HTTP API(board)"),
    ("tests/test_isolation_regression.py", "테스트 격리 회귀"),
    ("tests/test_independent_judge_kwargs.py", "Provider 분기"),
    ("tests/test_jira_client.py", "Jira 클라이언트"),
]

_TEST_SUMMARY_RE = re.compile(
    r"최종 실행 시각: (?P<ts>\S+).*?총 테스트 수: (?P<total>\d+).*?통과: (?P<passed>\d+)",
    re.DOTALL,
)
_TABLE_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|[^|]*\|\s*(\d+)\s*\|")


def _read_test_summary() -> Dict[str, Any]:
    """docs/테스트_결과.md(pytest 실행마다 자동 갱신)에서 최신 총계/레이어별 건수를 실시간으로 읽음.

    하드코딩된 차트 수치가 재배포 전까지 갱신되지 않던 문제(2차 코드 리뷰 P1-1)의 대응 -
    매 요청마다 이 문서를 다시 읽으므로 pytest를 새로 돌리면 다음 새로고침에 바로 반영됨."""
    path = DOCS_DIR / "테스트_결과.md"
    if not path.exists():
        return {"available": False, "reason": f"{path} 파일이 없습니다"}
    text = path.read_text(encoding="utf-8")
    match = _TEST_SUMMARY_RE.search(text)
    if not match:
        return {"available": False, "reason": "문서 형식을 인식하지 못했습니다"}
    per_file_counts: Counter[str] = Counter()
    for line in text.splitlines():
        row = _TABLE_ROW_RE.match(line)
        if row:
            per_file_counts[row.group(1)] += int(row.group(2))
    layer_breakdown = [
        {"label": label, "count": per_file_counts.get(file_name, 0)}
        for file_name, label in _LAYER_FILE_LABELS
        if per_file_counts.get(file_name, 0) > 0
    ]
    return {
        "available": True,
        "generated_at": match.group("ts"),
        "total": int(match.group("total")),
        "passed": int(match.group("passed")),
        "layer_breakdown": layer_breakdown,
        "source": str(path),
    }


def _read_latest_audit_manifest() -> Optional[Dict[str, Any]]:
    manifests = sorted(EXPORTS_DIR.glob("audit_manifest_*.json"), reverse=True)
    if not manifests:
        return None
    try:
        return json.loads(manifests[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _all_analysis_roots() -> List[Path]:
    """reports/voc_analysis/(shared 최상위 + 사용자별 하위 폴더) 전체 검색 대상 - 품질
    대시보드 집계뿐 아니라 이력 조회/상세/삭제에서도 "모든 실행자"를 찾을 때 공유해서 쓴다."""
    if (VOC_ANALYSIS_DIR / "users").exists():
        return [VOC_ANALYSIS_DIR] + sorted((VOC_ANALYSIS_DIR / "users").glob("*"))
    return [VOC_ANALYSIS_DIR]


def _find_analysis_file(analysis_id: str) -> Optional[Path]:
    """analysis_id 하나로 모든 사용자 폴더를 뒤져 실제 저장된 파일을 찾는다(조회/삭제 공용).

    결과는 실행자별 폴더에 나뉘어 저장되지만(_user_analysis_dir), 이력 조회·상세·삭제는
    "누가 실행했든" 찾을 수 있어야 하므로 저장 위치를 미리 알 필요 없이 전체를 훑는다."""
    if not analysis_id.startswith("voc_") or "/" in analysis_id or "\\" in analysis_id or ".." in analysis_id:
        return None
    for root in _all_analysis_roots():
        candidate = root / f"{analysis_id}.json"
        if candidate.exists():
            return candidate
    return None


def _scan_voc_history() -> Dict[str, Any]:
    """reports/voc_analysis/(shared + 사용자별) 아래 모든 결과 파일을 스캔해 실제 judge
    판정/quality_gate 분포를 집계 - 특정 3건이 아니라 지금까지의 전체 실행 이력 기준."""
    search_roots = _all_analysis_roots()
    verdicts: Counter[str] = Counter()
    gates: Counter[str] = Counter()
    total = 0
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.glob("voc_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            result = data.get("result", {})
            judge = result.get("judge") or {}
            gate = result.get("quality_gate") or {}
            verdicts[judge.get("verdict", "UNKNOWN")] += 1
            gates[gate.get("status", "UNKNOWN")] += 1
            total += 1
    return {"total_runs": total, "judge_verdict": dict(verdicts), "quality_gate": dict(gates)}


@router.get("/quality-dashboard")
def quality_dashboard() -> JSONResponse:
    """VOC 분석 탭 하단 품질 차트가 쓰는 실시간 집계 API.

    2차 코드 리뷰(P1-1)에서 지적된 "차트가 HTML에 고정값으로 박혀 있어 재배포 전까지
    최신 상태를 반영하지 못한다"는 문제의 정식 대응 - 테스트/실행 결과 집계는 이 API가
    매 요청마다 실제 파일을 다시 읽어 계산하고, 프론트는 더 이상 숫자를 하드코딩하지 않는다.
    결함 수정 현황(defect_status)만은 예외 - 코드 리뷰에서 사람이 검토·확정하는 값이라
    자동 계산 대상이 아니며, 결함보고서 최신 개정을 따라 이 한 곳에만 유지한다."""
    test_summary = _read_test_summary()
    manifest = _read_latest_audit_manifest()
    history = _scan_voc_history()
    return JSONResponse({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "test_summary": test_summary,
        "latest_audit_manifest": manifest,
        "voc_history": history,
        "defect_status": {
            "p0_resolved": 11, "p0_total": 11,
            "p1_resolved": 8, "p1_total": 8,
            "source": "VOC_분석_파이프라인_결함보고서.md(3차 개정) - 사람이 검토·확정한 값, 자동 계산 아님",
        },
    })


_REPORT_VERSION_DOC_KEYS = {"voc_quality_report", "voc_defect_report"}


def _load_report_versions() -> Dict[str, List[Dict[str, Any]]]:
    """scripts/snapshot_report_versions.py가 호스트에서 git 이력으로부터 미리 추출해둔
    스냅샷을 읽음 - 런타임 컨테이너에는 .git이 없으므로(.dockerignore) git을 직접
    조회하지 않고, 커밋된 정적 JSON 파일만 읽는다.

    DOCS_DIR을 매 호출마다 다시 읽어야 한다(모듈 로드 시점에 경로를 미리 계산해두면
    conftest.py의 테스트 격리 monkeypatch가 이 함수에는 반영되지 않아, 테스트가 실제
    저장소의 docs/report_versions.json을 그대로 읽어버리는 문제가 있었음)."""
    path = DOCS_DIR / "report_versions.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@router.get("/report-versions/{doc_key}")
def report_versions(doc_key: str) -> JSONResponse:
    """해당 문서(품질평가보고서/결함보고서)의 실제 git 개정 이력 목록 - 본문 내용은 빼고
    커밋/일시/커밋 메시지만 반환(목록 자체를 가볍게 유지, 본문은 선택 시 별도 조회)."""
    if doc_key not in _REPORT_VERSION_DOC_KEYS:
        return JSONResponse({"error": "unknown document"}, status_code=404)
    versions = _load_report_versions().get(doc_key, [])
    return JSONResponse([
        {"commit": v["commit"], "date": v["date"], "message": v["message"]}
        for v in versions
    ])


@router.get("/report-versions/{doc_key}/{commit}")
def report_version_content(doc_key: str, commit: str) -> JSONResponse:
    if doc_key not in _REPORT_VERSION_DOC_KEYS:
        return JSONResponse({"error": "unknown document"}, status_code=404)
    versions = _load_report_versions().get(doc_key, [])
    for v in versions:
        if v["commit"] == commit:
            return JSONResponse(v)
    return JSONResponse({"error": "unknown version"}, status_code=404)


@router.get("/history")
def analysis_history(request: Request) -> JSONResponse:
    """모든 실행자의 VOC 분석 이력을 함께 보여준다 - 게시판/Jira 티켓과 마찬가지로 팀
    공용 산출물이라 조회는 전원 공개, 삭제만 관리자/작성자 본인으로 좁게 제한한다
    (delete_analysis 참고)."""
    records = []
    for root in _all_analysis_roots():
        if not root.exists():
            continue
        for path in root.glob("voc_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                result = data.get("result", {})
                records.append({
                    "id": data["id"],
                    "created_at": data["created_at"],
                    "created_by": data.get("created_by"),
                    "summary": result.get("summary", ""),
                    "judge_verdict": result.get("judge", {}).get("verdict"),
                    "quality_gate_status": result.get("quality_gate", {}).get("status"),
                })
            except Exception:
                continue
    records.sort(key=lambda r: r["created_at"], reverse=True)
    return JSONResponse(records)


@router.delete("/{analysis_id}")
def delete_analysis(analysis_id: str, request: Request) -> JSONResponse:
    """VOC 분석 이력 삭제 - 관리자 또는 그 분석을 실행한 본인만 가능. 조회는 전원 공개로
    풀되(analysis_history), 삭제는 "관리자만"에서 "관리자 또는 실행자 본인"으로 좁혀서
    허용 - 본인이 실행한 결과는 본인 판단으로 정리할 수 있어야 하지만, 남의 실행 결과를
    임의로 지우는 것은 여전히 막는다."""
    path = _find_analysis_file(analysis_id)
    if not path:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return JSONResponse({"error": "not found"}, status_code=404)
    is_owner = record.get("created_by") == _username(request)
    if not (is_owner or _state["is_admin"](request)):
        return JSONResponse({"error": "forbidden (admin or the executor who ran it only)"}, status_code=403)
    path.unlink()
    return JSONResponse({"deleted": True})


# P1-2: 상세 조회(GET /{analysis_id})는 이력 목록과 마찬가지로 전원 공개다(팀 공용
# 산출물이므로). 하지만 저장 파일(record)에는 실행 당시 params가 그대로 들어있어
# excel_path(파일시스템 경로), jira_jql/focus_instruction(실행자가 입력한 조회/지시
# 내용)까지 전원에게 그대로 노출되는 문제가 있었다. 내부 저장 모델과 API로 내보내는
# DTO를 분리해 이 필드들을 좁힌다 - 저장 파일 자체(diskformat)는 감사 목적상 그대로
# 두고, 여기서 응답을 만들 때만 걸러낸다.
def _sanitize_analysis_detail_for_viewer(record: Dict[str, Any], request: Request) -> Dict[str, Any]:
    sanitized = dict(record)
    params = dict(record.get("params") or {})
    # excel_path는 서버 파일시스템 경로라 그 값을 아는 것 자체가 정보 노출임 - 실행자/
    # 관리자를 포함해 그 누구에게도 상세 응답으로는 절대 돌려주지 않는다(업로드 당시
    # 응답에서만 잠깐 쓰이고 그걸로 끝).
    params.pop("excel_path", None)

    is_owner = record.get("created_by") == _username(request)
    is_admin = bool(_state["is_admin"](request))
    if not (is_owner or is_admin):
        # jira_jql/focus_instruction은 실행자가 직접 입력한 조회 조건/지시문이라 다른
        # 팀원에게는 실행자/관리자 전용 정보로 취급해 숨긴다(값 노출이 아니라 키 자체를
        # 뺀다 - null도 "실제로 비어있었다"는 정보가 될 수 있어 아예 생략).
        params.pop("jira_jql", None)
        params.pop("focus_instruction", None)
    sanitized["params"] = params
    return sanitized


@router.get("/{analysis_id}")
def analysis_detail(analysis_id: str, request: Request) -> JSONResponse:
    if "/" in analysis_id or "\\" in analysis_id or ".." in analysis_id:
        return JSONResponse({"error": "invalid id"}, status_code=400)
    path = _find_analysis_file(analysis_id)
    if not path:
        return JSONResponse({"error": "not found"}, status_code=404)
    record = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(_sanitize_analysis_detail_for_viewer(record, request))
