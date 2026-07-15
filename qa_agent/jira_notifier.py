"""카테고리별 실패율이 높으면 Jira에 자동으로 티켓을 만들어주는 알림 모듈."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import requests

from .jira_client import basic_auth_header


class JiraNotifier:
    """카테고리별 실패율이 임계값을 넘으면 카테고리당 1건씩 Jira 티켓을 생성.

    중복 티켓 방지를 위해 실행 ID + 카테고리 라벨로 기존 이슈를 먼저 검색하고,
    그 검색 자체가 실패하면(네트워크 오류 등) 안전하게 생성을 건너뜁니다(중복 생성보다
    누락이 낫다는 판단).
    """

    def __init__(self, config: Dict[str, Any] | None = None):
        self.config = config or {}

    @property
    def _ready(self) -> bool:
        """enabled 플래그와 필수 설정(base_url/email/api_token)이 모두 있어야 True."""
        if not self.config.get("enabled"):
            return False
        return all(self.config.get(key) for key in ("base_url", "email", "api_token"))

    def _auth_header(self) -> str:
        """Jira Cloud REST API의 Basic Auth 헤더 - base64(email:api_token) 형식."""
        return basic_auth_header(self.config["email"], self.config["api_token"])

    def _ticket_exists(self, base_url: str, headers: Dict[str, str], run_id: str, category: str) -> bool:
        """같은 실행/카테고리로 이미 만들어진 티켓이 있는지 JQL로 검색."""
        jql = f'labels = "run:{run_id}" AND labels = "category:{category}"'
        try:
            response = requests.get(f"{base_url}/rest/api/3/search", headers=headers, params={"jql": jql}, timeout=10)
            response.raise_for_status()
            return bool(response.json().get("issues"))
        except Exception:
            return True  # 검색 실패 시: 중복일 수 있다고 보수적으로 판단하고 생성을 건너뜀

    def notify(self, report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """리포트를 보고 실패율 높은 카테고리마다 티켓을 생성. 비활성/설정 미비면 그냥 빈 리스트 반환."""
        if not self._ready:
            return []

        base_url = self.config["base_url"].rstrip("/")
        headers = {"Authorization": self._auth_header(), "Content-Type": "application/json"}
        threshold = self.config.get("category_fail_rate_threshold", 0.2)
        run_id = report.get("run_id", "unknown")

        results: List[Dict[str, Any]] = []
        for category, stats in (report.get("category_stats") or {}).items():
            total = stats.get("total", 0)
            passed = stats.get("passed", 0)
            if not total:
                continue
            fail_rate = 1 - (passed / total)
            if fail_rate < threshold:
                continue
            if self._ticket_exists(base_url, headers, run_id, category):
                continue

            # Jira Cloud REST v3은 description을 ADF(Atlassian Document Format)로 요구함
            payload = {
                "fields": {
                    "project": {"key": self.config.get("project_key", "QA")},
                    "summary": f"QA run {run_id}: {category} failure rate {fail_rate:.0%}",
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": f"Category {category} failed {total - passed}/{total} cases (fail rate {fail_rate:.0%}) in run {run_id}."}],
                        }],
                    },
                    "issuetype": {"name": "Task"},
                    "labels": [f"run:{run_id}", f"category:{category}"],
                }
            }
            try:
                response = requests.post(f"{base_url}/rest/api/3/issue", headers=headers, data=json.dumps(payload), timeout=10)
                response.raise_for_status()
                results.append({"status": "created", "key": response.json().get("key"), "category": category})
            except Exception as exc:
                results.append({"status": "error", "category": category, "reason": str(exc)})

        return results
