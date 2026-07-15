from unittest.mock import MagicMock, patch

import pytest

from qa_agent.jira_client import _adf_to_text, basic_auth_header, fetch_backlog_issues


def test_basic_auth_header_is_base64_of_email_colon_token():
    header = basic_auth_header("a@b.com", "tok123")
    assert header.startswith("Basic ")


def test_adf_to_text_plain_string():
    assert _adf_to_text("plain text") == "plain text"


def test_adf_to_text_none():
    assert _adf_to_text(None) == ""


def test_adf_to_text_nested_document():
    adf = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]},
        ],
    }
    assert _adf_to_text(adf) == "hello  world"


def test_fetch_backlog_issues_raises_on_missing_config():
    with pytest.raises(ValueError):
        fetch_backlog_issues({})


@patch("qa_agent.jira_client.requests.get")
def test_fetch_backlog_issues_normalizes_response(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "issues": [
            {
                "key": "QA-1",
                "fields": {
                    "summary": "로그인 실패",
                    "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "재현됨"}]}]},
                    "status": {"name": "Open"},
                    "updated": "2026-01-01T00:00:00Z",
                },
            }
        ]
    }
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    config = {"base_url": "https://x.atlassian.net", "email": "a@b.com", "api_token": "tok", "project_key": "QA"}
    issues = fetch_backlog_issues(config)

    assert len(issues) == 1
    assert issues[0]["key"] == "QA-1"
    assert issues[0]["summary"] == "로그인 실패"
    assert "재현됨" in issues[0]["description"]
    mock_get.assert_called_once()


@patch("qa_agent.jira_client.requests.get")
def test_fetch_backlog_issues_propagates_http_errors(mock_get):
    mock_get.side_effect = ConnectionError("network down")
    config = {"base_url": "https://x.atlassian.net", "email": "a@b.com", "api_token": "tok"}
    with pytest.raises(ConnectionError):
        fetch_backlog_issues(config)
