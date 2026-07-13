import json
from pathlib import Path

from qa_agent.excel_io import load_dataset
from qa_agent.jira_notifier import JiraNotifier
from qa_agent.slack_notifier import SlackNotifier


def test_excel_loader_handles_json_dataset(tmp_path):
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps([{'id': 'TC-200', 'category': 'COM', 'question': 'Q', 'golden_answer': 'A'}]), encoding='utf-8')
    cases = load_dataset(path)
    assert cases[0].id == 'TC-200'


def test_jira_notifier_handles_disabled_config():
    notifier = JiraNotifier({'enabled': False})
    assert notifier.notify({'cases': []}) == []


def test_slack_notifier_handles_missing_webhook():
    notifier = SlackNotifier(None)
    assert notifier.notify({'run_id': 'x'}).get('status') == 'skipped'
