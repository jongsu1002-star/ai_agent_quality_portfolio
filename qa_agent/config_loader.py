"""파이프라인 전체에 주입되는 설정(Config) 객체와 로더.

Config 하나가 커넥터/평가자/리포터 등 거의 모든 컴포넌트 생성자에 전달되는
횡단 관심사(cross-cutting concern)입니다. `load_config()`로 JSON 파일 + 실행 시
오버라이드를 합쳐서 만들 수도 있고, 웹앱처럼 `Config()`를 기본값으로 바로 써도 됩니다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ConnectorConfig:
    """챗봇(테스트 대상)을 어떻게 호출할지 정의."""

    mode: str = "dataset_only"  # "dataset_only"(저장된 답변 재사용) / "mock"(키워드 규칙) / "api"(실제 호출)
    api_endpoint: str = ""  # mode="api"일 때 호출할 엔드포인트
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    request_field: str = "question"  # 요청 JSON에서 질문을 담을 필드명 - 대상 챗봇마다 다를 수 있음
    # (예: 어떤 챗봇은 "question" 대신 "message"를 기대함). 응답 쪽은 이미 answer/reply 둘 다 읽음.


@dataclass
class Thresholds:
    """기법별 pass/fail 임계값. 값을 바꾸면 코드 수정 없이 판정 기준이 바뀝니다."""

    retrieval: Dict[str, float] = field(default_factory=lambda: {"recall_at_k": 0.7, "precision_at_k": 0.5, "mrr": 0.5})
    groundedness: Dict[str, float] = field(default_factory=lambda: {"score": 0.6})
    context_relevance: Dict[str, float] = field(default_factory=lambda: {"score": 0.05})  # 낮게 잡은 안전판
    llm_judge: Dict[str, float] = field(default_factory=lambda: {"accuracy": 3.5, "relevance": 3.5, "consistency": 3.5, "toxicity": 1.5})
    regression: Dict[str, float] = field(default_factory=lambda: {"similarity": 0.75})


@dataclass
class PipelineConfig:
    """실행 성능/동작 관련 설정."""

    parallel_workers: int = 4  # ThreadPoolExecutor 동시 실행 케이스 수
    top_k: int = 5  # 검색품질 평가 시 상위 몇 건까지 볼지
    reports_dir: str = "reports"
    fail_on_regression: bool = False


@dataclass
class Config:
    """모든 설정을 한데 묶은 최상위 객체. 대부분의 컴포넌트 생성자가 이걸 받습니다."""

    reports_dir: str = "reports"
    connector: ConnectorConfig = field(default_factory=ConnectorConfig)
    thresholds: Thresholds = field(default_factory=Thresholds)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    llm_judge: Dict[str, Any] = field(default_factory=dict)  # provider/api_key/model/base_url/key_name
    jira: Dict[str, Any] = field(default_factory=dict)
    comparison_mode: Dict[str, Any] = field(default_factory=lambda: {"pass_policy": "either_pass"})
    rubric: Dict[str, Any] = field(default_factory=dict)  # criteria(가중치)/pass_threshold
    dual_compare: Dict[str, Any] = field(default_factory=lambda: {"judge_score_min": 4.0})

    def __post_init__(self):
        # reports_dir는 최상위와 pipeline 양쪽에 있는데, 둘이 따로 놀지 않도록 항상 동기화
        self.pipeline.reports_dir = self.reports_dir


def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """dict를 재귀적으로 합침 - overrides 쪽 값이 우선하되, 둘 다 dict인 키는 계속 파고들어 병합."""
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> Config:
    """JSON 설정 파일과 실행 시 오버라이드를 합쳐 Config를 만듦.

    overrides는 파일을 고치지 않고 "이번 실행 1회에만" 적용하고 싶을 때 씁니다
    (예: 웹 요청의 mode/api_endpoint 오버라이드).
    """
    data: Dict[str, Any] = {}
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    if overrides:
        data = _deep_merge(data, overrides)

    reports_dir = data.get("reports_dir") or (str(Path(path).parent / "reports") if path else "reports")
    config = Config(reports_dir=reports_dir)

    if "connector" in data:
        for key, value in data["connector"].items():
            if hasattr(config.connector, key):
                setattr(config.connector, key, value)
    if "thresholds" in data:
        for key, value in data["thresholds"].items():
            if hasattr(config.thresholds, key) and isinstance(value, dict):
                getattr(config.thresholds, key).update(value)
    if "pipeline" in data:
        for key, value in data["pipeline"].items():
            if hasattr(config.pipeline, key):
                setattr(config.pipeline, key, value)
    for key in ("llm_judge", "jira", "comparison_mode", "rubric", "dual_compare"):
        if key in data and isinstance(data[key], dict):
            getattr(config, key).update(data[key])

    return config
