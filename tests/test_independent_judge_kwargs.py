"""app.main._independent_judge_kwargs 단위 테스트 - VOC 분석의 독립 LLM Judge가 생성
provider와 실제로 다른 provider를 고르는지(자기평가 편향 방지의 핵심 전제) 검증.

정종수_AI_개발자_운영_매뉴얼 기반 VOC 분석 품질평가 보고서의 test_llm_judge_configuration/
test_openai_provider_branch/test_anthropic_provider_branch에 대응.
"""

from app.main import _independent_judge_kwargs


def test_llm_judge_configuration_returns_constructor_kwargs():
    settings = {"llm_provider": "openai", "openai_api_key": "sk-a", "anthropic_api_key": "sk-b"}
    kwargs, cross_model = _independent_judge_kwargs(settings)
    assert "provider" in kwargs
    assert cross_model is True


def test_openai_provider_branch_when_primary_is_anthropic():
    """생성이 anthropic이면, openai 키가 있을 때 독립 Judge는 openai로 교차 검증."""
    settings = {"llm_provider": "anthropic", "openai_api_key": "sk-openai", "anthropic_api_key": "sk-anthropic"}
    kwargs, cross_model = _independent_judge_kwargs(settings)
    assert kwargs["provider"] == "openai"
    assert kwargs["api_key"] == "sk-openai"
    assert cross_model is True


def test_anthropic_provider_branch_when_primary_is_openai():
    """생성이 openai면, anthropic 키가 있을 때 독립 Judge는 anthropic으로 교차 검증."""
    settings = {"llm_provider": "openai", "openai_api_key": "sk-openai", "anthropic_api_key": "sk-anthropic"}
    kwargs, cross_model = _independent_judge_kwargs(settings)
    assert kwargs["provider"] == "anthropic"
    assert kwargs["api_key"] == "sk-anthropic"
    assert cross_model is True


def test_falls_back_to_same_provider_when_only_one_key_configured():
    """openai 키만 있으면 진짜 교차검증이 불가능 - 같은 provider로 폴백하고 cross_model=False로 정직하게 표시."""
    settings = {"llm_provider": "openai", "openai_api_key": "sk-openai"}
    kwargs, cross_model = _independent_judge_kwargs(settings)
    assert kwargs["provider"] == "openai"
    assert cross_model is False


def test_falls_back_when_neither_key_configured():
    settings = {"llm_provider": "openai"}
    kwargs, cross_model = _independent_judge_kwargs(settings)
    assert kwargs["provider"] == "openai"
    assert cross_model is False


def test_custom_provider_prefers_anthropic_for_cross_validation():
    settings = {"llm_provider": "custom", "llm_endpoint": "https://x", "llm_key_value": "k", "anthropic_api_key": "sk-anthropic"}
    kwargs, cross_model = _independent_judge_kwargs(settings)
    assert kwargs["provider"] == "anthropic"
    assert cross_model is True
