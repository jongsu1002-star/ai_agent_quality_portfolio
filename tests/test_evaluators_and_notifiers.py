import base64
import json

from qa_agent.config_loader import load_config
from qa_agent.evaluators import RetrievalEvaluator, apply_pass_policy, compute_agreement
from qa_agent.jira_notifier import JiraNotifier
from qa_agent.models import ChatbotResponse, GoldenCase
from qa_agent.slack_notifier import DiscordNotifier


def test_retrieval_evaluator_computes_real_recall_precision_mrr():
    case = GoldenCase(id="TC-1", category="COM", question="q", golden_answer="a", relevant_doc_ids=["DOC-1", "DOC-2"])
    response = ChatbotResponse(answer="a", doc_ids=["DOC-9", "DOC-1"])

    result = RetrievalEvaluator().evaluate(case, response)

    assert result.applicable is True
    assert result.recall_at_k == 0.5
    assert result.mrr == 0.5  # DOC-1 found at rank 2
    assert result.passed is False  # below default recall threshold of 0.7


def test_apply_pass_policy_falls_back_to_rule_when_llm_unavailable():
    assert apply_pass_policy(rule_pass=True, llm_pass=None, policy="both_must_pass") is True
    assert apply_pass_policy(rule_pass=False, llm_pass=None, policy="either_pass") is False


def test_compute_agreement():
    assert compute_agreement(True, True) == "match"
    assert compute_agreement(True, False) == "mismatch"
    assert compute_agreement(True, None) == "n/a"


def test_load_config_applies_overrides():
    config = load_config(overrides={"connector": {"mode": "api", "api_endpoint": "https://example.com/chat"}, "thresholds": {"regression": {"similarity": 0.9}}})

    assert config.connector.mode == "api"
    assert config.connector.api_endpoint == "https://example.com/chat"
    assert config.thresholds.regression["similarity"] == 0.9


def test_jira_notifier_uses_base64_basic_auth(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"issues": []}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("qa_agent.jira_notifier.requests.get", fake_get)
    monkeypatch.setattr("qa_agent.jira_notifier.requests.post", lambda *a, **k: FakeResponse())

    notifier = JiraNotifier({"enabled": True, "base_url": "https://example.atlassian.net", "email": "a@b.com", "api_token": "secret"})
    notifier._ticket_exists("https://example.atlassian.net", {"Authorization": notifier._auth_header()}, "run_1", "COM")

    expected = "Basic " + base64.b64encode(b"a@b.com:secret").decode("ascii")
    assert captured["headers"]["Authorization"] == expected


def test_jira_notifier_creates_ticket_for_high_failure_category(monkeypatch):
    created = {}

    class SearchResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"issues": []}

    class CreateResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"key": "QA-1"}

    monkeypatch.setattr("qa_agent.jira_notifier.requests.get", lambda *a, **k: SearchResponse())

    def fake_post(url, headers=None, data=None, timeout=None):
        created["payload"] = json.loads(data)
        return CreateResponse()

    monkeypatch.setattr("qa_agent.jira_notifier.requests.post", fake_post)

    notifier = JiraNotifier({"enabled": True, "base_url": "https://example.atlassian.net", "email": "a@b.com", "api_token": "secret"})
    results = notifier.notify({"run_id": "run_1", "category_stats": {"COM": {"total": 10, "passed": 5}}})

    assert results == [{"status": "created", "key": "QA-1", "category": "COM"}]
    assert "run:run_1" in created["payload"]["fields"]["labels"]


def test_discord_notifier_uses_discord_schema(monkeypatch):
    captured = {}

    class FakeResponse:
        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("qa_agent.slack_notifier.urllib.request.urlopen", fake_urlopen)

    result = DiscordNotifier("https://discord.com/api/webhooks/x").notify({"run_id": "run_1", "overall_pass_rate": 1.0})

    assert result["status"] == "sent"
    assert "content" in captured["body"]
    assert "embeds" in captured["body"]
    assert "attachments" not in captured["body"]
