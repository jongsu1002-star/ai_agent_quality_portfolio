"""qa_agent 전체에서 사용하는 데이터 모델(dataclass) 모음.

모든 결과 클래스는 `to_dict()`를 가지고 있고, 이 반환값이 그대로 JSON 리포트와
REST API 응답으로 나갑니다. 그래서 새 필드를 추가하면 `to_dict()`에도 꼭 반영해야
합니다 - 안 그러면 필드는 있는데 리포트/화면에는 조용히 안 보이는 실수가 생깁니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GoldenCase:
    """테스트 케이스 1건 (질문 + 기대 정답 + 부가 정보).

    데이터셋 파일(JSON/Excel)의 한 행이 이 객체 하나가 됩니다.
    """

    id: str
    category: str
    question: str
    golden_answer: str
    relevant_doc_ids: List[str] = field(default_factory=list)  # 비어있으면 검색품질(RAG) 평가는 자동으로 건너뜀
    required_keywords: List[str] = field(default_factory=list)  # dual_compare 진단용 키워드 (판정에는 관여하지 않음)
    test_type: Optional[str] = None
    existing_answer: Optional[str] = None  # dataset_only 커넥터 모드에서 재사용할 "미리 저장된 답변"
    existing_contexts: Optional[List[str]] = None
    existing_doc_ids: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoldenCase":
        """업로드된 JSON/Excel 한 행을 GoldenCase로 변환. 필수 필드가 없으면 즉시 에러."""
        missing = [k for k in ("id", "category", "question", "golden_answer") if not data.get(k)]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")
        return cls(
            id=str(data["id"]),
            category=str(data["category"]),
            question=str(data["question"]),
            golden_answer=str(data["golden_answer"]),
            relevant_doc_ids=list(data.get("relevant_doc_ids") or []),
            required_keywords=list(data.get("required_keywords") or []),
            test_type=data.get("test_type"),
            existing_answer=data.get("existing_answer"),
            existing_contexts=list(data.get("existing_contexts") or []),
            existing_doc_ids=list(data.get("existing_doc_ids") or []),
        )


@dataclass
class ChatbotResponse:
    """커넥터(Connector)가 챗봇을 호출한 뒤 돌려주는 공통 응답 형태.

    error가 채워지면(문자열이 있으면) 그 케이스의 나머지 평가는 전부 건너뛰고
    바로 실패 처리합니다 - 인프라 오류(네트워크 장애 등)를 품질 실패와 섞지 않기 위함입니다.
    """

    answer: str = ""
    contexts: List[str] = field(default_factory=list)
    doc_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "contexts": self.contexts,
            "doc_ids": self.doc_ids,
            "error": self.error,
        }


@dataclass
class RetrievalResult:
    """검색품질(RAG) 평가 결과 - Recall@K / Precision@K / MRR."""

    applicable: bool = True  # relevant_doc_ids가 없는 케이스는 False (평가 대상이 아님)
    recall_at_k: float = 0.0
    precision_at_k: float = 0.0
    mrr: float = 0.0
    passed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "applicable": self.applicable,
            "recall_at_k": self.recall_at_k,
            "precision_at_k": self.precision_at_k,
            "mrr": self.mrr,
            "passed": self.passed,
        }


@dataclass
class DualScoreSide:
    """이원 평가(Dual Evaluation)에서 룰 쪽 또는 LLM 쪽, 한쪽 판정 하나."""

    score: float = 0.0
    passed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"score": self.score, "passed": self.passed}


@dataclass
class DualEvalResult:
    """룰 기반 판정 + (선택적) LLM 판정을 합쳐 최종 판정을 내리는 결과.

    근거성/컨텍스트관련성/회귀/유해성 평가가 모두 이 형태를 공유합니다.
    llm이 None이면 LLM 판정을 아예 안 했다는 뜻(비활성/호출실패 등)이며,
    이 경우 최종 판정(final_pass)은 룰 쪽 결과만으로 결정됩니다.
    """

    rule: DualScoreSide = field(default_factory=DualScoreSide)
    llm: Optional[DualScoreSide] = None
    agreement: str = "n/a"  # "match" / "mismatch" / "n/a"(LLM 미평가)
    final_pass: bool = False
    is_regression: bool = False  # 이전 실행에서는 통과했는데 이번에 실패했으면 True
    evaluated: bool = False  # 이 기법이 실제로 실행됐는지 여부

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule.to_dict(),
            "llm": self.llm.to_dict() if self.llm else None,
            "agreement": self.agreement,
            "final_pass": self.final_pass,
            "is_regression": self.is_regression,
            "evaluated": self.evaluated,
        }


@dataclass
class LLMJudgeResult:
    """LLM-as-a-Judge 채점 결과 (정확성/관련성/유해성/일관성, 1~5점).

    toxicity는 낮을수록 안전합니다(1=안전, 5=매우 유해).
    passed가 None이면 "판정 자체를 안 함"(errored=True, 예: API 키 없음)이라는 뜻이고,
    이 상태는 파이프라인의 overall_pass 계산에서 제외됩니다(인프라 문제로 취급).
    """

    accuracy: float = 0.0
    relevance: float = 0.0
    toxicity: float = 0.0
    reason: str = ""
    consistency: float = 0.0
    passed: Optional[bool] = None
    refused: bool = False  # 모델이 채점 자체를 거부한 경우 (이건 진짜 품질 신호로 보고 실패 처리)
    errored: bool = False  # API 키 없음/네트워크 오류 등 인프라 문제로 판정을 못한 경우

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accuracy": self.accuracy,
            "relevance": self.relevance,
            "toxicity": self.toxicity,
            "reason": self.reason,
            "consistency": self.consistency,
            "passed": self.passed,
            "refused": self.refused,
            "errored": self.errored,
        }


@dataclass
class RubricResult:
    """루브릭(가중치 채점표) 평가 결과."""

    scores: Dict[str, float] = field(default_factory=dict)  # 항목별 점수 (예: {"accuracy": 4.0, "clarity": 5.0})
    weighted_total: float = 0.0
    passed: bool = False
    rationale: str = ""
    refused: bool = False
    errored: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scores": self.scores,
            "weighted_total": self.weighted_total,
            "passed": self.passed,
            "rationale": self.rationale,
            "refused": self.refused,
            "errored": self.errored,
        }


@dataclass
class CaseResult:
    """케이스 1건에 대한 전체 평가 결과 (선택된 기법들의 결과를 전부 모아둠)."""

    case_id: str
    overall_pass: bool = False
    errors: List[str] = field(default_factory=list)  # 커넥터 호출 실패 등 인프라 오류 메시지
    retrieval: Optional[RetrievalResult] = None
    groundedness: Optional[DualEvalResult] = None
    context_relevance: Optional[DualEvalResult] = None
    llm_judge: Optional[LLMJudgeResult] = None
    rubric: Optional[RubricResult] = None
    regression: Optional[DualEvalResult] = None
    toxicity: Optional[DualEvalResult] = None
    dual_compare: Optional[Dict[str, Any]] = None  # existing_answer vs 이번 실행 응답 비교 결과

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "overall_pass": self.overall_pass,
            "errors": self.errors,
            "retrieval": self.retrieval.to_dict() if self.retrieval else None,
            "groundedness": self.groundedness.to_dict() if self.groundedness else None,
            "context_relevance": self.context_relevance.to_dict() if self.context_relevance else None,
            "llm_judge": self.llm_judge.to_dict() if self.llm_judge else None,
            "rubric": self.rubric.to_dict() if self.rubric else None,
            "regression": self.regression.to_dict() if self.regression else None,
            "toxicity": self.toxicity.to_dict() if self.toxicity else None,
            "dual_compare": self.dual_compare,
        }


@dataclass
class RunReport:
    """파이프라인 실행 1회의 전체 결과 (모든 케이스 결과 + 집계 통계)."""

    run_id: str
    overall_pass_rate: float = 0.0
    category_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # 카테고리별 {total, passed}
    test_type_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # 테스트 유형(GoldenCase.test_type)별 {total, passed} - 없으면 "미분류"
    regressions_detected: int = 0  # is_regression=True인 케이스 수
    comparison_summary: Dict[str, Any] = field(default_factory=dict)  # 룰-LLM 일치/불일치 집계
    mismatch_cases: List[Dict[str, Any]] = field(default_factory=list)  # 룰-LLM 판정이 갈린 케이스 목록
    functional_test: Dict[str, Any] = field(default_factory=dict)  # 커넥터 계약 검사(run-level) 결과
    rule_api_comparison: Dict[str, Any] = field(default_factory=dict)  # dual_compare 상태별 집계
    cases: List[CaseResult] = field(default_factory=list)
    dataset_path: Optional[str] = None  # 이 실행이 테스트한 데이터셋 파일 경로 (없으면 기본 데모 케이스)
    dataset_case_count: int = 0  # 위 데이터셋에서 실제로 채점된 케이스 수 (필터링 전 원본 기준)
    testcase_path: Optional[str] = None  # 이 실행이 사용한 테스트 케이스(발화문) 파일 경로 (없으면 데이터셋 자체 question 사용)
    testcase_case_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "overall_pass_rate": self.overall_pass_rate,
            "category_stats": self.category_stats,
            "test_type_stats": self.test_type_stats,
            "regressions_detected": self.regressions_detected,
            "comparison_summary": self.comparison_summary,
            "mismatch_cases": self.mismatch_cases,
            "functional_test": self.functional_test,
            "rule_api_comparison": self.rule_api_comparison,
            "cases": [case.to_dict() for case in self.cases],
            "dataset_path": self.dataset_path,
            "dataset_case_count": self.dataset_case_count,
            "testcase_path": self.testcase_path,
            "testcase_case_count": self.testcase_case_count,
        }


@dataclass
class ValidationResult:
    """필수 키워드 포함 여부 검증 결과.

    dual_compare 딕셔너리 내부에서 진단용으로만 쓰이며, 최종 통과/실패 판정에는
    직접 관여하지 않습니다 (판정은 LLM 채점 쪽이 결정).
    """

    required_keywords: List[str] = field(default_factory=list)
    missing_keywords: List[str] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "required_keywords": self.required_keywords,
            "missing_keywords": self.missing_keywords,
            "passed": self.passed,
        }
