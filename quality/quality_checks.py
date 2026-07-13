"""CI/CD에서 품질 게이트로 쓸 수 있는 헬퍼 - 최신 리포트를 읽어 pass/fail을 판정."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class QualityCheckRunner:
    def __init__(self, reports_dir: str = "reports"):
        self.reports_dir = Path(reports_dir)

    def run(self) -> Dict[str, Any]:
        """최신 리포트 파일이 있는지만 간단히 확인 (상세 판정은 run_gate() 참고)."""
        latest = self.reports_dir / "latest.json"
        if latest.exists():
            return {"status": "ok", "latest_report": latest.name}
        return {"status": "pending", "latest_report": None}

    def summarize_failures(self, report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """리포트에서 실패한 케이스 목록만 뽑아냄."""
        failures = []
        for case in report.get("cases", []):
            if not case.get("overall_pass"):
                failures.append({"case_id": case.get("case_id"), "reason": "failed evaluation"})
        return failures

    def run_gate(self, pass_rate_threshold: float = 1.0) -> Dict[str, Any]:
        """CI 품질 게이트 - 최신 리포트를 읽어 빌드를 통과시킬지 판정.

        CLI/CI 단계에서 게이트 실패 시 `exit 1` 하는 용도로 쓰도록 설계했습니다.
        예외를 던지지 않고 판정 결과를 구조화된 dict로 반환하니, 실패 처리 방식은
        호출하는 쪽에서 자유롭게 결정하면 됩니다.
        """
        latest = self.reports_dir / "latest.json"
        if not latest.exists():
            return {"gate": "fail", "reason": "no report available", "pass_rate": None}

        report = json.loads(latest.read_text(encoding="utf-8"))
        pass_rate = report.get("overall_pass_rate", 0.0)
        failures = self.summarize_failures(report)
        passed_gate = pass_rate >= pass_rate_threshold
        return {
            "gate": "pass" if passed_gate else "fail",
            "run_id": report.get("run_id"),
            "pass_rate": pass_rate,
            "threshold": pass_rate_threshold,
            "failing_cases": failures,
        }
