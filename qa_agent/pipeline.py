"""QA 파이프라인의 코어 오케스트레이터.

CLI든 웹앱이든, 신규 진입점을 추가하더라도 실제 채점 로직은 전부 여기
`PipelineOrchestrator`를 통해서만 실행되어야 합니다 (엔진 이중 구현 금지 원칙).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from .config_loader import Config
from .evaluators import (
    ContextRelevanceEvaluator,
    GroundednessEvaluator,
    LLMJudgeEvaluator,
    RegressionEvaluator,
    RetrievalEvaluator,
    RubricEvaluator,
    ToxicityEvaluator,
    validate_required_keywords,
)
from .llm_client import OpenAIJudgeClient
from .models import CaseResult, ChatbotResponse, GoldenCase, RunReport, ValidationResult

ALL_TECHNIQUES = ["rag", "llm_quality", "rubric", "regression", "functional", "dual_compare"]

# functional 기법에서 커넥터에 실제로 던져보는 4가지 "일부러 이상한" 질문
_FUNCTIONAL_PROBES = [
    {"name": "empty_question", "question": ""},
    {"name": "whitespace_question", "question": "   "},
    {"name": "oversized_question", "question": "a" * 3001},
    {"name": "special_characters", "question": "<script>alert(1)</script> ' OR 1=1 --"},
]


class PipelineOrchestrator:
    """케이스 로딩 → 커넥터 호출 → 평가 → 리포트 집계까지 한 번의 실행을 책임지는 클래스."""

    def __init__(self, config: Config):
        self.config = config
        self.reports_dir = Path(config.reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        pass_policy = config.comparison_mode.get("pass_policy", "either_pass")
        # LLM 판정 클라이언트는 여기서 딱 1개만 만들어서 모든 평가자가 공유
        self.judge_client = OpenAIJudgeClient(
            api_key=config.llm_judge.get("api_key"),
            model=config.llm_judge.get("model"),
            base_url=config.llm_judge.get("base_url"),
            provider=config.llm_judge.get("provider"),
            key_name=config.llm_judge.get("key_name"),
        )

        self.retrieval_evaluator = RetrievalEvaluator(thresholds=config.thresholds.retrieval, top_k=config.pipeline.top_k)
        self.groundedness_evaluator = GroundednessEvaluator(
            threshold=config.thresholds.groundedness.get("score", 0.6), judge_client=self.judge_client, pass_policy=pass_policy
        )
        self.context_relevance_evaluator = ContextRelevanceEvaluator(
            threshold=config.thresholds.context_relevance.get("score", 0.05), judge_client=self.judge_client, pass_policy=pass_policy
        )
        self.llm_judge_evaluator = LLMJudgeEvaluator(thresholds=config.thresholds.llm_judge, judge_client=self.judge_client)
        rubric_spec = config.rubric or {}
        self.rubric_evaluator = RubricEvaluator(
            criteria=rubric_spec.get("criteria") or {"accuracy": 0.5, "clarity": 0.5},
            pass_threshold=rubric_spec.get("pass_threshold", 3.5),
            judge_client=self.judge_client,
        )
        self.regression_evaluator = RegressionEvaluator(
            similarity_threshold=config.thresholds.regression.get("similarity", 0.75), judge_client=self.judge_client, pass_policy=pass_policy
        )
        self.toxicity_evaluator = ToxicityEvaluator(threshold=config.thresholds.llm_judge.get("toxicity", 1.5), pass_policy=pass_policy)

    def run(
        self,
        cases: List[GoldenCase],
        category_filter: Optional[List[str]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        run_id: Optional[str] = None,
        techniques: Optional[List[str]] = None,
        dataset_path: Optional[str] = None,
        testcase_path: Optional[str] = None,
    ) -> RunReport:
        """표준 파이프라인 실행. 케이스들을 병렬로 채점하고 최종 RunReport를 만들어 저장.

        dataset_path/testcase_path는 실행 결과에 그대로 기록되어(RunReport.dataset_path 등)
        대시보드가 "이 결과가 어느 데이터셋·테스트 케이스 기준인지"를 보여줄 수 있게 함 - 안 그러면
        나중에 활성 값이 바뀐 뒤 과거 실행 결과를 볼 때 지금 화면에 보이는 값과 착각하기 쉬움.

        category_filter는 카테고리 값 목록(정확 일치, OR 조건)입니다. None/빈 리스트면 전체 실행.
        호출부가 실수로 문자열 하나만 넘겨도(예전 방식 category_filter="COM") 안전하게 단일 원소
        리스트로 취급합니다 - 문자열을 그대로 두면 `in` 검사가 부분 문자열 포함 검사로 새서
        (예: "ACC" in "COM") 의도치 않은 케이스가 걸릴 수 있기 때문입니다.
        """
        techniques = techniques or ["rag", "llm_quality"]
        run_id = run_id or "run"
        if isinstance(category_filter, str):
            category_filter = [category_filter]
        category_set = set(category_filter) if category_filter else None
        filtered_cases = [case for case in cases if category_set is None or case.category in category_set]

        previous_pass_by_id = self._load_previous_pass_map(run_id) if "regression" in techniques else {}

        results: List[CaseResult] = []
        with ThreadPoolExecutor(max_workers=max(1, self.config.pipeline.parallel_workers)) as executor:
            future_to_case = {executor.submit(self._evaluate_case, case, techniques, previous_pass_by_id): case for case in filtered_cases}
            for idx, future in enumerate(future_to_case, start=1):
                results.append(future.result())
                if on_progress:
                    on_progress(idx, len(filtered_cases))

        # 데이터셋 순서를 유지 - ThreadPoolExecutor는 완료되는 순서가 뒤죽박죽이라 그대로 두면 뒤섞임
        order = {case.id: i for i, case in enumerate(filtered_cases)}
        results.sort(key=lambda r: order.get(r.case_id, 0))

        functional_test = self._run_functional_test() if "functional" in techniques else {}

        report = self._build_report(filtered_cases, results, run_id, functional_test)
        report.dataset_path = dataset_path
        report.dataset_case_count = len(cases)
        report.testcase_path = testcase_path
        report.testcase_case_count = len(filtered_cases) if testcase_path else 0
        self._write_reports(report)
        return report

    def _get_response(self, case: GoldenCase) -> ChatbotResponse:
        """커넥터 모드에 따라 케이스의 답변을 얻어옴 (실제 호출 / 목업 / 저장된 값 재사용)."""
        connector = self.config.connector
        if connector.mode == "api" and connector.api_endpoint:
            try:
                resp = requests.request(
                    connector.method or "POST",
                    connector.api_endpoint,
                    json={connector.request_field or "question": case.question, "case_id": case.id},
                    headers=connector.headers or {},
                    timeout=connector.timeout,
                )
                resp.raise_for_status()
            except requests.exceptions.RequestException as exc:
                return ChatbotResponse(error=f"api_call_failed: {exc}")
            try:
                data = resp.json()
            except ValueError as exc:
                return ChatbotResponse(error=f"invalid_json_response: {exc}")
            try:
                return ChatbotResponse(
                    answer=str(data.get("answer", data.get("reply", ""))),
                    contexts=list(data.get("contexts") or []),
                    doc_ids=list(data.get("doc_ids") or []),
                )
            except (TypeError, AttributeError) as exc:
                return ChatbotResponse(error=f"response_mapping_mismatch: {exc}")

        if connector.mode == "mock":
            return ChatbotResponse(answer=f"[mock] {case.golden_answer}", contexts=list(case.existing_contexts or []), doc_ids=list(case.existing_doc_ids or []))

        # dataset_only (기본값): 이 케이스에 이미 저장되어 있는 답변을 그대로 재사용
        if not case.existing_answer:
            return ChatbotResponse(error="dataset_only: case has no existing_answer")
        return ChatbotResponse(answer=case.existing_answer, contexts=list(case.existing_contexts or []), doc_ids=list(case.existing_doc_ids or []))

    def _evaluate_case(self, case: GoldenCase, techniques: List[str], previous_pass_by_id: Dict[str, bool]) -> CaseResult:
        """케이스 1건을 채점하는 심장부 - techniques에 따라 어떤 평가자를 돌릴지 결정."""
        result = CaseResult(case_id=case.id)
        response = self._get_response(case)

        if response.error:
            # 커넥터 호출 자체가 실패한 경우는 인프라 오류이므로 나머지 평가는 전부 건너뛰고 즉시 실패 처리
            result.errors.append(response.error)
            result.overall_pass = False
            return result

        applicable_checks: List[bool] = []

        if "rag" in techniques:
            if case.relevant_doc_ids:
                result.retrieval = self.retrieval_evaluator.evaluate(case, response)
                applicable_checks.append(result.retrieval.passed)
            result.groundedness = self.groundedness_evaluator.evaluate(case, response)
            applicable_checks.append(result.groundedness.final_pass)
            result.context_relevance = self.context_relevance_evaluator.evaluate(case, response)
            applicable_checks.append(result.context_relevance.final_pass)

        if "llm_quality" in techniques:
            result.llm_judge = self.llm_judge_evaluator.evaluate(case, response)
            if not result.llm_judge.errored:
                # LLM 호출이 아예 안 됐으면(errored) 이 케이스의 overall_pass 판정에서 제외
                applicable_checks.append(bool(result.llm_judge.passed))

        if "rubric" in techniques:
            result.rubric = self.rubric_evaluator.evaluate(case, response)
            if not result.rubric.errored:
                applicable_checks.append(result.rubric.passed)

        if "regression" in techniques:
            result.regression = self.regression_evaluator.evaluate(case, response)
            # 이전 실행에서는 통과였는데 이번엔 실패면 회귀로 표시
            result.regression.is_regression = previous_pass_by_id.get(case.id) is True and not result.regression.final_pass
            applicable_checks.append(result.regression.final_pass)

        # 유해성 검사는 techniques 선택과 무관하게 항상 실행되는 안전 가드레일
        result.toxicity = self.toxicity_evaluator.evaluate(response, result.llm_judge)
        applicable_checks.append(result.toxicity.final_pass)

        if "dual_compare" in techniques and case.existing_answer:
            result.dual_compare = self._evaluate_dual_compare(case, response)

        result.overall_pass = all(applicable_checks) if applicable_checks else True
        return result

    def _evaluate_dual_compare(self, case: GoldenCase, response: ChatbotResponse) -> Dict[str, Any]:
        """저장된(룰) 답변과 이번 실행의 실시간 응답을 비교.

        이 케이스에서 이미 받아온 response를 그대로 재사용합니다 - 커넥터를
        두 번 호출하는 별도의 rule-vs-api 동시비교 모드(이 플랫한 구조에서는
        구현하지 않음)와는 의도적으로 다른 경로입니다.
        """
        rule_answer = case.existing_answer or ""
        api_answer = response.answer or ""

        rule_missing = validate_required_keywords(rule_answer, case.required_keywords)
        api_missing = validate_required_keywords(api_answer, case.required_keywords)
        rule_validation = ValidationResult(required_keywords=list(case.required_keywords or []), missing_keywords=rule_missing, passed=not rule_missing)
        api_validation = ValidationResult(required_keywords=list(case.required_keywords or []), missing_keywords=api_missing, passed=not api_missing)

        judge_min = self.config.dual_compare.get("judge_score_min", 4.0)
        rule_judge = self._judge_dual_side(case, rule_answer, judge_min)
        api_judge = self._judge_dual_side(case, api_answer, judge_min)

        if rule_judge["passed"] and api_judge["passed"]:
            comparison_status = "BOTH_PASS"
        elif not rule_judge["passed"] and not api_judge["passed"]:
            comparison_status = "BOTH_FAIL"
        elif api_judge["passed"]:
            comparison_status = "API_ONLY_PASS"  # 참조 답변(existing_answer)이 노후화됐을 가능성
        else:
            comparison_status = "RULE_ONLY_PASS"  # 실시간 챗봇이 회귀했을 가능성 (우선 조사 대상)

        return {
            "rule_answer": rule_answer,
            "api_answer": api_answer,
            "rule_validation": rule_validation.to_dict(),
            "api_validation": api_validation.to_dict(),
            "rule_judge": rule_judge,
            "api_judge": api_judge,
            "comparison_status": comparison_status,
        }

    def _judge_dual_side(self, case: GoldenCase, answer: str, judge_min: float) -> Dict[str, Any]:
        """dual_compare에서 룰 쪽/API 쪽 답변 하나를 LLM에게 1~5점으로 채점받음."""
        if not self.judge_client.enabled:
            return {"score": 0.0, "passed": False, "errored": True, "reason": "OPENAI_API_KEY not configured"}
        try:
            verdict = self.judge_client.judge(
                system_prompt='Score how well the candidate answer addresses the question compared to the golden answer, 1-5. Respond ONLY as JSON: {"score": n, "reason": "..."}.',
                user_prompt=f"Question: {case.question}\nGolden answer: {case.golden_answer}\nCandidate answer: {answer}",
            )
            score = float(verdict.get("score", 0.0))
            return {"score": score, "passed": score >= judge_min, "errored": False, "reason": verdict.get("reason", "")}
        except Exception as exc:
            return {"score": 0.0, "passed": False, "errored": True, "reason": str(exc)}

    def _run_functional_probe(self, probe: Dict[str, str]) -> Dict[str, Any]:
        """합성 질문 하나를 커넥터에 던져서 예외 없이 잘 처리하는지 확인."""
        probe_case = GoldenCase(id=f"FUNC-{probe['name']}", category="NFR", question=probe["question"], golden_answer="")
        try:
            response = self._get_response(probe_case)
        except Exception as exc:
            return {"probe": probe["name"], "passed": False, "detail": f"connector raised an exception instead of returning error: {exc}"}
        if response.error:
            # 크래시 없이 error로 깔끔하게 처리된 것 = 오히려 정상 (역발상 검증)
            return {"probe": probe["name"], "passed": True, "detail": f"handled gracefully: {response.error}"}
        # 여기 도달했다면 _get_response()가 이미 answer/contexts/doc_ids를 str/list로 변환해
        # ChatbotResponse를 만든 뒤이므로(모든 커넥터 모드 공통), 타입은 항상 보장되어 있음 -
        # 별도의 isinstance 재검증은 불필요(항상 True)해서 제거함
        return {"probe": probe["name"], "passed": True, "detail": "ok"}

    def _run_functional_test(self) -> Dict[str, Any]:
        """functional 기법 - 케이스와 무관하게 실행 1회당 딱 한 번, 커넥터 계약을 검사."""
        probes = [self._run_functional_probe(probe) for probe in _FUNCTIONAL_PROBES]
        passed = sum(1 for probe in probes if probe["passed"])
        return {"total": len(probes), "passed": passed, "failed": len(probes) - passed, "probes": probes}

    def _load_previous_pass_map(self, current_run_id: str) -> Dict[str, bool]:
        """가장 최근 run_*.json(이번 실행 제외)에서 케이스별 통과 여부를 읽어옴 - 회귀 판정용."""
        candidates = [p for p in self.reports_dir.glob("run_*.json") if p.stem != f"run_{current_run_id}"]
        if not candidates:
            return {}
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {case.get("case_id"): bool(case.get("overall_pass")) for case in data.get("cases", [])}

    def _build_report(self, cases: List[GoldenCase], results: List[CaseResult], run_id: str, functional_test: Dict[str, Any]) -> RunReport:
        """케이스별 결과를 모아 카테고리/테스트유형 통계·회귀 건수·불일치 목록 등을 집계."""
        category_by_id = {case.id: case.category for case in cases}
        # test_type은 선택 필드라(functional/regression 등) 비어있는 케이스가 섞일 수 있음 -
        # 그런 케이스도 통계에서 누락되지 않도록 "미분류"로 묶음
        test_type_by_id = {case.id: (case.test_type or "미분류") for case in cases}

        category_stats: Dict[str, Dict[str, Any]] = {}
        test_type_stats: Dict[str, Dict[str, Any]] = {}
        for result in results:
            category = category_by_id.get(result.case_id, "UNKNOWN")
            stats = category_stats.setdefault(category, {"total": 0, "passed": 0})
            stats["total"] += 1
            stats["passed"] += 1 if result.overall_pass else 0

            test_type = test_type_by_id.get(result.case_id, "미분류")
            type_stats = test_type_stats.setdefault(test_type, {"total": 0, "passed": 0})
            type_stats["total"] += 1
            type_stats["passed"] += 1 if result.overall_pass else 0

        pass_rate = sum(1 for r in results if r.overall_pass) / len(results) if results else 1.0

        regressions_detected = sum(1 for r in results if r.regression and r.regression.is_regression)

        rule_llm_agree = 0
        rule_llm_disagree = 0
        mismatch_cases: List[Dict[str, Any]] = []
        for r in results:
            for dual in (r.groundedness, r.context_relevance, r.regression, r.toxicity):
                if dual and dual.agreement == "match":
                    rule_llm_agree += 1
                elif dual and dual.agreement == "mismatch":
                    rule_llm_disagree += 1
                    mismatch_cases.append({"case_id": r.case_id, "rule_passed": dual.rule.passed, "llm_passed": dual.llm.passed if dual.llm else None})

        comparison_summary = {
            "dual_evaluation_agree": rule_llm_agree,
            "dual_evaluation_mismatch": rule_llm_disagree,
        }

        dual_compare_statuses = [r.dual_compare["comparison_status"] for r in results if r.dual_compare]
        rule_api_comparison = {status: dual_compare_statuses.count(status) for status in set(dual_compare_statuses)} if dual_compare_statuses else {}

        return RunReport(
            run_id=run_id,
            overall_pass_rate=pass_rate,
            category_stats=category_stats,
            test_type_stats=test_type_stats,
            regressions_detected=regressions_detected,
            comparison_summary=comparison_summary,
            mismatch_cases=mismatch_cases,
            functional_test=functional_test,
            rule_api_comparison=rule_api_comparison,
            cases=results,
        )

    def _write_reports(self, report: RunReport) -> None:
        """run_{id}.json과 latest.json으로 저장 (CSV/MD/결함보고서는 reporter.py가 따로 처리)."""
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        with (self.reports_dir / f"run_{report.run_id}.json").open("w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=2)
        with (self.reports_dir / "latest.json").open("w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=2)
