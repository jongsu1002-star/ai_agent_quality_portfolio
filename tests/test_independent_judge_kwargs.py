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


# ===================== provider 전환 시 모델/키 혼용 방지 (실제 운영 장애로 재현된 버그) =====================
#
# 실제로 발생했던 장애: llm_provider="openai"에 llm_model="gpt-4o-mini"가 설정된 상태에서
# 독립 Judge가 anthropic으로 전환됐는데도 llm_model이 그대로 kwargs["model"]에 들어가
# Anthropic API에 "gpt-4o-mini"라는 존재하지 않는 모델을 요청 -> 404 Not Found.


def test_primary_provider_model_does_not_leak_when_judge_switches_provider():
    settings = {"llm_provider": "openai", "llm_model": "gpt-4o-mini", "openai_api_key": "sk-openai", "anthropic_api_key": "sk-anthropic"}
    kwargs, cross_model = _independent_judge_kwargs(settings)
    assert cross_model is True
    assert kwargs["provider"] == "anthropic"
    assert "model" not in kwargs  # gpt-4o-mini가 anthropic 클라이언트로 새어 들어가면 안 됨


def test_primary_provider_llm_key_value_does_not_leak_when_judge_switches_provider():
    """llm_key_value는 "현재 선택된 provider용 override 키"인데, provider가 바뀌면 그
    override는 더 이상 유효하지 않음 - 반대 provider의 전용 키(anthropic_api_key)만 써야 함."""
    settings = {
        "llm_provider": "openai", "llm_key_value": "override-key-meant-for-openai",
        "openai_api_key": "sk-openai", "anthropic_api_key": "sk-anthropic",
    }
    kwargs, cross_model = _independent_judge_kwargs(settings)
    assert cross_model is True
    assert kwargs["provider"] == "anthropic"
    assert kwargs["api_key"] == "sk-anthropic"
    assert kwargs["api_key"] != "override-key-meant-for-openai"


def test_model_override_preserved_when_provider_does_not_actually_change():
    """폴백 경로(교차검증 불가, 같은 provider 유지)에서는 llm_model을 지울 이유가 없음 -
    같은 provider 맥락 안에서의 정상적인 모델 오버라이드이기 때문."""
    settings = {"llm_provider": "openai", "llm_model": "gpt-4o", "openai_api_key": "sk-openai"}
    kwargs, cross_model = _independent_judge_kwargs(settings)
    assert cross_model is False
    assert kwargs["provider"] == "openai"
    assert kwargs.get("model") == "gpt-4o"
