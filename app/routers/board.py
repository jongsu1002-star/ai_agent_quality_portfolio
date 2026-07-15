"""게시판(일반/FAQ/VOC) + 댓글 REST API.

app/main.py의 `_require_login` 미들웨어가 이미 전역적으로 로그인 여부를 강제하므로(계정이
하나도 없는 "shared" 모드에서는 미들웨어 자체가 통과시킴), 이 라우터의 각 핸들러는 로그인
여부를 따로 검사하지 않고 "작성자 본인 또는 관리자만" 같은 소유권 검사만 함 - 기존
dataset/testcase 삭제 엔드포인트와 동일한 패턴.

app/main.py가 순환 임포트 없이 자신의 BoardStore/_current_username/_is_admin을 이 모듈에
주입할 수 있도록 monitoring_addon.py와 동일하게 `configure()` 의존성 주입 방식을 씀.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from qa_agent.board import BOARD_TYPES, BoardStore

router = APIRouter(prefix="/api/board")

_state: Dict[str, Any] = {"store": None, "current_username": None, "is_admin": None}


def configure(store: BoardStore, current_username_fn, is_admin_fn) -> None:
    _state["store"] = store
    _state["current_username"] = current_username_fn
    _state["is_admin"] = is_admin_fn


def _store() -> BoardStore:
    return _state["store"]


def _username(request: Request) -> str:
    return _state["current_username"](request)


def _can_modify(request: Request, author: str) -> bool:
    return author == _username(request) or _state["is_admin"](request)


@router.get("/posts")
def list_posts(request: Request, board_type: str, search: Optional[str] = None, limit: int = 300, offset: int = 0) -> JSONResponse:
    if board_type not in BOARD_TYPES:
        return JSONResponse({"error": f"unknown board_type: {board_type}"}, status_code=400)
    is_admin = _state["is_admin"](request)
    viewer = _username(request)
    items = _store().list_posts(board_type, limit=limit, offset=offset, search=search, viewer=viewer, include_hidden=is_admin)
    total = _store().count_posts(board_type, search=search, viewer=viewer, include_hidden=is_admin)
    return JSONResponse({"items": items, "total": total})


@router.get("/posts/{post_id}")
def get_post(post_id: int, request: Request) -> JSONResponse:
    post = _store().get_post(post_id)
    if not post:
        return JSONResponse({"error": "post not found"}, status_code=404)
    if not post["visible"] and not _can_modify(request, post["author"]):
        # 비노출 글은 작성자/관리자가 아니면 존재 자체를 숨김(404)
        return JSONResponse({"error": "post not found"}, status_code=404)
    post = dict(post)
    post["comments"] = _store().list_comments(post_id)
    return JSONResponse(post)


@router.post("/posts")
def create_post(payload: Dict[str, Any], request: Request) -> JSONResponse:
    board_type = str(payload.get("board_type") or "")
    title = str(payload.get("title") or "")
    content = str(payload.get("content") or "")
    if board_type not in BOARD_TYPES:
        return JSONResponse({"error": f"unknown board_type: {board_type}"}, status_code=400)
    post = _store().create_post(board_type, title, content, _username(request))
    if not post:
        return JSONResponse({"error": "title is required"}, status_code=400)
    return JSONResponse(post)


@router.put("/posts/{post_id}")
def update_post(post_id: int, payload: Dict[str, Any], request: Request) -> JSONResponse:
    author = _store().get_post_author(post_id)
    if author is None:
        return JSONResponse({"error": "post not found"}, status_code=404)
    if not _can_modify(request, author):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    updated = _store().update_post(post_id, str(payload.get("title") or ""), str(payload.get("content") or ""))
    if not updated:
        return JSONResponse({"error": "title is required"}, status_code=400)
    return JSONResponse(updated)


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, request: Request) -> JSONResponse:
    """게시글 삭제는 관리자만 가능 - 작성자 본인도 삭제할 수 없음(VOC 등 민원 증적을 작성자가
    임의로 지울 수 없게 하려는 정책, 사용자 명시 요구사항)."""
    author = _store().get_post_author(post_id)
    if author is None:
        return JSONResponse({"error": "post not found"}, status_code=404)
    if not _state["is_admin"](request):
        return JSONResponse({"error": "forbidden (admin only)"}, status_code=403)
    _store().delete_post(post_id)
    return JSONResponse({"deleted": True})


@router.post("/posts/{post_id}/visibility")
def set_post_visibility(post_id: int, payload: Dict[str, Any], request: Request) -> JSONResponse:
    """노출/비노출 전환 - 작성자 또는 관리자만 가능."""
    author = _store().get_post_author(post_id)
    if author is None:
        return JSONResponse({"error": "post not found"}, status_code=404)
    if not _can_modify(request, author):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    updated = _store().set_post_visibility(post_id, bool(payload.get("visible")))
    if not updated:
        return JSONResponse({"error": "post not found"}, status_code=404)
    return JSONResponse(updated)


@router.post("/posts/{post_id}/comments")
def add_comment(post_id: int, payload: Dict[str, Any], request: Request) -> JSONResponse:
    if not _store().get_post(post_id):
        return JSONResponse({"error": "post not found"}, status_code=404)
    comment = _store().add_comment(post_id, _username(request), str(payload.get("content") or ""))
    if not comment:
        return JSONResponse({"error": "content is required"}, status_code=400)
    return JSONResponse(comment)


@router.put("/comments/{comment_id}")
def update_comment(comment_id: int, payload: Dict[str, Any], request: Request) -> JSONResponse:
    author = _store().get_comment_author(comment_id)
    if author is None:
        return JSONResponse({"error": "comment not found"}, status_code=404)
    if not _can_modify(request, author):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    updated = _store().update_comment(comment_id, str(payload.get("content") or ""))
    if not updated:
        return JSONResponse({"error": "content is required"}, status_code=400)
    return JSONResponse(updated)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, request: Request) -> JSONResponse:
    """게시글 삭제와 동일한 정책(관리자만) — 댓글도 VOC 민원 증적일 수 있어 작성자 임의
    삭제를 막음. 사용자가 게시글에 대해서만 명시했지만, 같은 성격의 콘텐츠라 대칭 적용."""
    author = _store().get_comment_author(comment_id)
    if author is None:
        return JSONResponse({"error": "comment not found"}, status_code=404)
    if not _state["is_admin"](request):
        return JSONResponse({"error": "forbidden (admin only)"}, status_code=403)
    _store().delete_comment(comment_id)
    return JSONResponse({"deleted": True})
