"""`/metrics-addon`이 반환하는 Prometheus 텍스트 노출 포맷을 손으로 생성.

`prometheus_client` 같은 새 의존성을 추가하지 않고, `monitoring/metrics_collector.py`처럼
표준 라이브러리만으로 텍스트를 조립합니다. 기존 `MetricsCollector.summary()`와 k6 최신 실행
결과를 **읽기 전용**으로만 사용하며, 이 모듈이 무엇을 하든 기존 `/api/monitoring/summary`나
기존 미들웨어에는 아무 영향을 주지 않습니다.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _metric_lines(name: str, help_text: str, metric_type: str, value: Any) -> str:
    return f"# HELP {name} {help_text}\n# TYPE {name} {metric_type}\n{name} {value}\n"


def render_prometheus_text(summary: Dict[str, Any], latest_k6_run: Optional[Dict[str, Any]], voc_quality: Optional[Dict[str, Any]] = None) -> str:
    """기존 서버 자체 지표 + (있다면) 최신 k6 실행 지표 + (있다면) VOC 품질 지표를 하나의
    텍스트로 렌더링."""
    parts = [
        _metric_lines("qa_platform_total_requests", "Total HTTP requests processed since process start.", "counter", summary.get("total_requests", 0)),
        _metric_lines("qa_platform_error_rate", "5xx error rate over total requests.", "gauge", summary.get("error_rate", 0.0)),
        _metric_lines("qa_platform_avg_response_ms", "Average response time (ms) over the last hour.", "gauge", summary.get("avg_response_ms", 0.0)),
        _metric_lines("qa_platform_p95_response_ms", "p95 response time (ms) over the last hour.", "gauge", summary.get("p95_response_ms", 0.0)),
        _metric_lines("qa_platform_uptime_seconds", "Seconds since this process started.", "counter", summary.get("uptime_seconds", 0.0)),
    ]

    if latest_k6_run:
        dur = latest_k6_run.get("http_req_duration") or {
            "p95_ms": latest_k6_run.get("p95_ms"),
        }
        parts.append(_metric_lines("qa_platform_k6_total_requests", "Total requests from the latest k6 load test run.", "gauge", latest_k6_run.get("total_requests", 0)))
        parts.append(_metric_lines("qa_platform_k6_failed_rate", "Failed request rate from the latest k6 load test run.", "gauge", latest_k6_run.get("failed_rate", 0.0)))
        parts.append(_metric_lines("qa_platform_k6_p95_ms", "p95 response time (ms) from the latest k6 load test run.", "gauge", dur.get("p95_ms", 0.0)))
        thresholds_passed = latest_k6_run.get("thresholds_passed")
        parts.append(_metric_lines("qa_platform_k6_thresholds_passed", "Whether the latest k6 run passed all thresholds (1) or not (0).", "gauge", 1 if thresholds_passed else 0))

    if voc_quality:
        parts.append(_metric_lines("qa_platform_voc_test_total", "Total test count from the latest pytest run (docs/테스트_결과.md).", "gauge", voc_quality.get("test_total", 0)))
        parts.append(_metric_lines("qa_platform_voc_test_passed", "Passed test count from the latest pytest run (docs/테스트_결과.md).", "gauge", voc_quality.get("test_passed", 0)))
        parts.append(_metric_lines("qa_platform_voc_total_runs", "Total VOC analysis runs recorded so far.", "gauge", voc_quality.get("voc_total_runs", 0)))
        judge_verdict = voc_quality.get("judge_verdict", {})
        for verdict in ("PASS", "FAIL", "SKIPPED", "ERROR", "UNKNOWN"):
            parts.append(_metric_lines(f"qa_platform_voc_judge_{verdict.lower()}", f"VOC independent judge verdict count: {verdict}.", "gauge", judge_verdict.get(verdict, 0)))
        quality_gate = voc_quality.get("quality_gate", {})
        for status in ("APPROVED", "REJECTED", "REVIEW_REQUIRED", "UNVERIFIED", "UNKNOWN"):
            parts.append(_metric_lines(f"qa_platform_voc_gate_{status.lower()}", f"VOC quality gate status count: {status}.", "gauge", quality_gate.get(status, 0)))

    return "".join(parts)
