"""VOC 자동분석 REST API - 게시판 VOC 글 + (선택)Jira 백로그 + (선택)엑셀 업로드를 모아
LLM 분석을 동기 실행하고 결과를 전역 저장(reports/voc_analysis/)함.

app/main.py와의 결합은 monitoring_addon.py/board.py와 동일하게 `configure()` 의존성
주입 방식을 씀(순환 임포트 회피).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response

from qa_agent.board import BoardStore
from qa_agent.excel_io import (
    build_voc_import_template_workbook,
    build_voc_json_template,
    load_voc_excel,
    load_voc_json,
)
from qa_agent.jira_client import fetch_backlog_issues
from qa_agent.llm_client import OpenAIJudgeClient
from qa_agent.voc_analysis import MAX_ITEMS_FOR_PROMPT, run_voc_analysis_with_judge

router = APIRouter(prefix="/api/voc-analysis")
logger = logging.getLogger(__name__)

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


@router.post("/run")
def run_analysis(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """게시판 VOC 글 + 선택적 Jira/엑셀 소스를 모아 LLM 분석을 동기 실행.

    use_board 기본값은 True(아무것도 지정하지 않으면 게시판 VOC를 씀 - 기존 동작과 동일한
    하위호환 기본값). Jira/엑셀을 추가로 켤 때는 게시판 VOC를 포함할지 여부를 명시적으로
    선택할 수 있음(use_board=false로 게시판을 빼고 외부 소스만 분석하는 것도 가능)."""
    board_posts: List[Dict[str, Any]] = []
    if payload.get("use_board", True):
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
        except Exception:
            logger.exception("VOC Jira retrieval failed")
            return JSONResponse({"error": "Jira 조회에 실패했습니다. 연결 설정과 권한을 확인하세요."}, status_code=502)

    excel_rows: List[Dict[str, Any]] = []
    if payload.get("use_excel") and not payload.get("excel_path"):
        return JSONResponse({"error": "엑셀 사용을 선택했지만 업로드된 파일이 없습니다"}, status_code=400)
    if payload.get("use_excel") and payload.get("excel_path"):
        resolved = _resolve_upload_path(payload["excel_path"], _username(request))
        if not resolved:
            return JSONResponse({"error": "invalid excel_path"}, status_code=400)
        loader = load_voc_json if resolved.suffix.lower() == ".json" else load_voc_excel
        try:
            excel_rows = loader(resolved)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            logger.exception("VOC external file loading failed")
            return JSONResponse({"error": "업로드 데이터를 불러오지 못했습니다. 파일을 다시 업로드하세요."}, status_code=400)

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
    if raw_item_limit is not None:
        try:
            if isinstance(raw_item_limit, bool):
                raise ValueError
            parsed_item_limit = int(raw_item_limit)
        except (TypeError, ValueError):
            return JSONResponse({"error": "item_limit은 숫자여야 합니다"}, status_code=400)
        if parsed_item_limit < 1:
            return JSONResponse({"error": "item_limit은 1 이상이어야 합니다"}, status_code=400)
        item_limit = min(parsed_item_limit, MAX_ITEMS_FOR_PROMPT)

    try:
        result = run_voc_analysis_with_judge(
            generation_client, judge_client, board_posts, jira_issues, excel_rows,
            focus_instruction=focus_instruction, item_limit=item_limit, cross_model=cross_model,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("VOC analysis generation failed")
        return JSONResponse({"error": "VOC 분석 처리에 실패했습니다. LLM 설정과 서버 로그를 확인하세요."}, status_code=502)

    analysis_dir = _user_analysis_dir(_username(request))
    analysis_dir.mkdir(parents=True, exist_ok=True)
    # 초 단위 타임스탬프만 쓰면 같은 초에 완료된 두 요청의 결과 파일이 서로 덮어써서 감사
    # 기록이 유실될 수 있었음(실제로 지적된 결함) - 마이크로초 + uuid 접미사로 사실상
    # 충돌 불가능하게 하면서도, 파일명 앞부분은 여전히 타임스탬프라 정렬/식별이 쉬움
    analysis_id = f"voc_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
    record = {
        "id": analysis_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": _username(request),
        "app_version": _state["app_version"](),
        "params": {
            "use_board": bool(payload.get("use_board", True)),
            "use_jira": bool(payload.get("use_jira")),
            "jira_jql": payload.get("jira_jql"),
            "use_excel": bool(payload.get("use_excel")),
            "excel_path": payload.get("excel_path"),
            "focus_instruction": focus_instruction or None,
            "item_limit": item_limit,
        },
        "result": result,
    }
    _write_analysis_record_atomically(analysis_id, record, analysis_dir)
    return JSONResponse(record)


def _write_analysis_record_atomically(analysis_id: str, record: Dict[str, Any], analysis_dir: Optional[Path] = None) -> None:
    """같은 디렉터리에 임시 파일로 먼저 쓴 뒤 os.replace()로 원자적 교체 - 쓰는 도중에
    프로세스가 죽거나 같은 파일을 읽는 다른 요청이 있어도 반쯤 쓰인 JSON을 보는 일이 없음."""
    target_dir = analysis_dir or VOC_ANALYSIS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / f"{analysis_id}.json"
    tmp_path = target_dir / f".{analysis_id}.json.tmp"
    tmp_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, final_path)


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


def _scan_voc_history() -> Dict[str, Any]:
    """reports/voc_analysis/(shared + 사용자별) 아래 모든 결과 파일을 스캔해 실제 judge
    판정/quality_gate 분포를 집계 - 특정 3건이 아니라 지금까지의 전체 실행 이력 기준."""
    search_roots = [VOC_ANALYSIS_DIR] + sorted((VOC_ANALYSIS_DIR / "users").glob("*")) if (VOC_ANALYSIS_DIR / "users").exists() else [VOC_ANALYSIS_DIR]
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


@router.get("/history")
def analysis_history(request: Request) -> JSONResponse:
    analysis_dir = _user_analysis_dir(_username(request))
    if not analysis_dir.exists():
        return JSONResponse([])
    records = []
    for path in sorted(analysis_dir.glob("voc_*.json"), reverse=True):
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
    return JSONResponse(records)


def _safe_analysis_path(analysis_id: str, username: str = "shared") -> Optional[Path]:
    if not analysis_id.startswith("voc_") or "/" in analysis_id or "\\" in analysis_id or ".." in analysis_id:
        return None
    return _user_analysis_dir(username) / f"{analysis_id}.json"


@router.delete("/{analysis_id}")
def delete_analysis(analysis_id: str, request: Request) -> JSONResponse:
    """VOC 분석 이력 삭제 - 게시글 삭제와 동일하게 관리자만 가능(이력 관리 기능)."""
    if not _state["is_admin"](request):
        return JSONResponse({"error": "forbidden (admin only)"}, status_code=403)
    path = _safe_analysis_path(analysis_id, _username(request))
    if not path or not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    path.unlink()
    return JSONResponse({"deleted": True})


@router.get("/{analysis_id}")
def analysis_detail(analysis_id: str, request: Request) -> JSONResponse:
    if "/" in analysis_id or "\\" in analysis_id or ".." in analysis_id:
        return JSONResponse({"error": "invalid id"}, status_code=400)
    path = _user_analysis_dir(_username(request)) / f"{analysis_id}.json"
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
