"""/health 엔드포인트가 사용하는 헬스체크 - 리포트 디렉터리 쓰기 가능 여부 등을 확인."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass
class HealthStatus:
    service: str
    status: str
    details: Dict[str, Any]


class HealthChecker:
    def __init__(self, reports_dir: str = "reports"):
        self.reports_dir = Path(reports_dir)

    def check(self) -> HealthStatus:
        """실제로 파일을 써 봐서 리포트 디렉터리 쓰기 가능 여부 등을 확인."""
        checks = {
            "reports_dir_writable": self._reports_dir_writable(),
            "latest_report_present": (self.reports_dir / "latest.json").exists(),
            "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        }
        status = "ok" if checks["reports_dir_writable"] else "degraded"
        return HealthStatus(service="qa_agent_quality_platform", status=status, details=checks)

    def _reports_dir_writable(self) -> bool:
        """임시 파일을 하나 써봤다가 지워서 실제 쓰기 권한이 있는지 확인."""
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            probe = self.reports_dir / ".healthcheck_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False
