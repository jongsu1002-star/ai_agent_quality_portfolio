"""Slack/Discord/Teams 웹훅으로 실행 완료를 알려주는 알림 모듈들.

세 알림 채널이 서로 다른 웹훅 페이로드 형식을 요구하기 때문에, 클래스마다
notify()를 각자의 형식에 맞게 따로 구현했습니다 (같은 형식을 그대로 재사용하면
일부 채널에서 정상적으로 표시되지 않음).
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict


class SlackNotifier:
    """Slack 인커밍 웹훅 알림. webhook_url이 없으면 조용히 건너뜀."""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url

    def notify(self, report: Dict[str, Any]) -> Dict[str, Any]:
        if not self.webhook_url:
            return {"status": "skipped", "reason": "missing webhook"}
        payload = json.dumps({
            "text": f"QA run {report.get('run_id')} completed with pass rate {report.get('overall_pass_rate')}",
            "attachments": [{"color": "#36a64f", "title": "QA Run Summary", "text": json.dumps(report, ensure_ascii=False)[:2000]}],
        }).encode("utf-8")
        req = urllib.request.Request(self.webhook_url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return {"status": "sent", "code": response.getcode()}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}


class DiscordNotifier(SlackNotifier):
    """Discord 웹훅 알림 - Slack과는 페이로드 스키마가 달라서(content/embeds) 별도 구현."""

    def notify(self, report: Dict[str, Any]) -> Dict[str, Any]:
        if not self.webhook_url:
            return {"status": "skipped", "reason": "missing webhook"}
        payload = json.dumps({
            "content": f"QA run {report.get('run_id')} completed with pass rate {report.get('overall_pass_rate')}",
            "embeds": [{"title": "QA Run Summary", "description": json.dumps(report, ensure_ascii=False)[:2000], "color": 0x36A64F}],
        }).encode("utf-8")
        req = urllib.request.Request(self.webhook_url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return {"status": "sent", "code": response.getcode()}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}


class TeamsNotifier(SlackNotifier):
    """Microsoft Teams 웹훅 알림 - Adaptive Card 형식으로 전송."""

    def notify(self, report: Dict[str, Any]) -> Dict[str, Any]:
        if not self.webhook_url:
            return {"status": "skipped", "reason": "missing webhook"}
        payload = {
            "type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.5",
                "body": [{"type": "TextBlock", "text": f"QA run {report.get('run_id')} completed", "weight": "Bolder"}, {"type": "TextBlock", "text": f"Pass rate: {report.get('overall_pass_rate')}"}]
            }}]
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.webhook_url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return {"status": "sent", "code": response.getcode()}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}
