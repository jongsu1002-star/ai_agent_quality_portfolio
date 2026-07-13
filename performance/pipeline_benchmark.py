"""qa_agent 파이프라인 실행 시간을 실제로 측정하는 벤치마크."""

from __future__ import annotations

from typing import Any, Dict, List

from qa_agent.config_loader import Config
from qa_agent.models import GoldenCase
from qa_agent.pipeline import PipelineOrchestrator

from .benchmark import BenchmarkRunner


def benchmark_pipeline(cases: List[GoldenCase], techniques: List[str], reports_dir: str = "reports") -> Dict[str, Any]:
    """PipelineOrchestrator.run()을 실제로 한 번 돌려 소요 시간을 측정.

    부하/동시접속(TPS) 테스트가 아닙니다(설계서에서 의도적으로 범위 외로 뒀고, 그런
    용도는 k6/locust를 씀) - 그냥 "N건 채점하는 데 얼마나 걸리나"만 답해줍니다.
    """
    orchestrator = PipelineOrchestrator(Config(reports_dir=reports_dir))
    result = BenchmarkRunner().run(lambda: orchestrator.run(cases, techniques=techniques, run_id="benchmark"))
    report = result["result"]
    return {
        "elapsed_seconds": result["elapsed_seconds"],
        "case_count": len(cases),
        "seconds_per_case": result["elapsed_seconds"] / len(cases) if cases else 0.0,
        "overall_pass_rate": report.overall_pass_rate,
    }
