import json

from qa_agent.llm_client import OpenAIJudgeClient


def test_openai_provider_is_default_and_reads_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = OpenAIJudgeClient()
    assert client.provider == "openai"
    assert client.enabled is True
    assert client.key_name == "Authorization"


def test_none_provider_is_always_disabled_even_with_a_key():
    client = OpenAIJudgeClient(api_key="sk-real-key", provider="none")
    assert client.enabled is False


def test_custom_provider_requires_both_key_and_endpoint():
    assert OpenAIJudgeClient(provider="custom", api_key="secret").enabled is False  # no base_url
    assert OpenAIJudgeClient(provider="custom", base_url="https://llm.example.com/v1").enabled is False  # no key
    client = OpenAIJudgeClient(provider="custom", api_key="secret", base_url="https://llm.example.com/v1", key_name="X-API-Key")
    assert client.enabled is True
    assert client.key_name == "X-API-Key"


def test_custom_provider_sends_configured_header_name_not_authorization(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"score": 5})}}]}

    def fake_post(url, headers=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("qa_agent.llm_client.requests.post", fake_post)

    client = OpenAIJudgeClient(provider="custom", api_key="secret-value", base_url="https://llm.example.com/v1", key_name="X-API-Key")
    result = client.judge("system", "user")

    assert result == {"score": 5}
    assert captured["url"] == "https://llm.example.com/v1/chat/completions"
    assert captured["headers"]["X-API-Key"] == "secret-value"
    assert "Authorization" not in captured["headers"]


def test_anthropic_provider_reads_env_and_defaults(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = OpenAIJudgeClient(provider="anthropic")
    assert client.enabled is True
    assert client.key_name == "x-api-key"
    assert client.base_url == "https://api.anthropic.com/v1"
    assert client.model == "claude-sonnet-5"


def test_anthropic_provider_calls_messages_api_and_parses_text_block(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": json.dumps({"score": 4})}]}

    def fake_post(url, headers=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(data)
        return FakeResponse()

    monkeypatch.setattr("qa_agent.llm_client.requests.post", fake_post)

    client = OpenAIJudgeClient(provider="anthropic", api_key="sk-ant-secret")
    result = client.judge("system prompt", "user prompt")

    assert result == {"score": 4}
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-secret"
    assert captured["headers"]["anthropic-version"]
    assert "Authorization" not in captured["headers"]
    assert captured["body"]["messages"] == [{"role": "user", "content": "user prompt"}]
    assert "system prompt" in captured["body"]["system"]


def test_anthropic_provider_unwraps_markdown_json_fences(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": '```json\n{"score": 3}\n```'}]}

    monkeypatch.setattr("qa_agent.llm_client.requests.post", lambda *a, **k: FakeResponse())

    client = OpenAIJudgeClient(provider="anthropic", api_key="sk-ant-secret")
    assert client.judge("system", "user") == {"score": 3}
