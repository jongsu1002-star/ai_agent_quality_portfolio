"""Jira Cloud REST API 공용 헬퍼 - 인증 헤더 생성(jira_notifier.py와 공유) + 이슈 조회(읽기).

jira_notifier.py는 지금까지 티켓 생성(쓰기)만 했고, 기존 조회(GET /rest/api/3/search)는
중복 티켓 방지용으로만 내부에서 썼음. VOC 자동분석이 "Jira 백로그"를 읽어와야 해서, 그
조회 기능을 여기로 뽑아 범용화함.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

import requests


def basic_auth_header(email: str, api_token: str) -> str:
    """Jira Cloud REST API의 Basic Auth 헤더 - base64(email:api_token) 형식."""
    credentials = f"{email}:{api_token}".encode("utf-8")
    return "Basic " + base64.b64encode(credentials).decode("ascii")


def _adf_to_text(node: Any) -> str:
    """Jira Cloud REST v3의 description은 ADF(Atlassian Document Format, 중첩 dict)라서
    그대로 쓰면 프롬프트에 JSON 구조가 그대로 섞여 들어감 - 텍스트 노드만 재귀적으로 모음."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        return " ".join(_adf_to_text(child) for child in node.get("content", []) if child)
    if isinstance(node, list):
        return " ".join(_adf_to_text(item) for item in node)
    return ""


def _normalize_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    fields = issue.get("fields") or {}
    return {
        "key": issue.get("key", ""),
        "summary": fields.get("summary") or "",
        "description": _adf_to_text(fields.get("description")).strip(),
        "status": (fields.get("status") or {}).get("name", ""),
        "updated": fields.get("updated", ""),
    }


def fetch_backlog_issues(config: Dict[str, Any], jql: Optional[str] = None, max_results: int = 50) -> List[Dict[str, Any]]:
    """Jira 이슈(백로그)를 JQL로 조회해 정규화된 목록으로 반환.

    config는 jira_notifier.JiraNotifier와 동일한 키(base_url/email/api_token/project_key)를
    기대. jql을 지정하지 않으면 project_key 기준 최신순 기본 쿼리를 사용. 설정이 비어있거나
    HTTP 요청이 실패하면 예외를 그대로 던짐 - 호출부(HTTP 라우트)가 잡아서 사용자에게
    읽기 좋은 오류로 안내해야 함.
    """
    base_url = (config.get("base_url") or "").rstrip("/")
    email = config.get("email") or ""
    api_token = config.get("api_token") or ""
    if not (base_url and email and api_token):
        raise ValueError("Jira 설정(base_url/email/api_token)이 비어 있습니다")

    if not jql:
        project_key = config.get("project_key")
        jql = f'project = "{project_key}" ORDER BY updated DESC' if project_key else "ORDER BY updated DESC"

    headers = {"Authorization": basic_auth_header(email, api_token), "Content-Type": "application/json"}
    response = requests.get(
        f"{base_url}/rest/api/3/search",
        headers=headers,
        params={"jql": jql, "maxResults": max_results},
        timeout=10,
    )
    response.raise_for_status()
    issues = response.json().get("issues", [])
    return [_normalize_issue(issue) for issue in issues]
