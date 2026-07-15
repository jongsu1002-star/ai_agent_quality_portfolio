import pytest

from qa_agent.voc_analysis import (
    MAX_ITEMS_FOR_PROMPT,
    build_judge_prompts,
    build_prompts,
    build_voc_items,
    normalize_board_post,
    normalize_excel_row,
    normalize_jira_issue,
    run_independent_judge,
    run_voc_analysis,
    run_voc_analysis_with_judge,
)


def test_normalize_board_post():
    item = normalize_board_post({"id": 1, "title": "느려요", "content": "응답이 느립니다", "created_at": "2026-01-01"})
    assert item["source"] == "board"
    assert item["id"] == "post-1"
    assert "느려요" in item["content"]


def test_normalize_jira_issue():
    item = normalize_jira_issue({"key": "QA-1", "summary": "버그", "description": "설명", "updated": "2026-01-02"})
    assert item["source"] == "jira"
    assert item["id"] == "QA-1"


def test_normalize_excel_row():
    item = normalize_excel_row({"source": "설문", "date": "2026-01-03", "content": "느려요"})
    assert item["source"] == "excel"
    assert item["date"] == "2026-01-03"


def test_normalize_truncates_long_content():
    long_content = "x" * 1000
    item = normalize_excel_row({"content": long_content})
    assert len(item["content"]) <= 503  # 500 + "..."


def test_build_voc_items_sorts_by_recency_and_caps_total():
    board_posts = [{"id": i, "title": "t", "content": "c", "created_at": f"2026-01-{i:02d}"} for i in range(1, 5)]
    items, counts = build_voc_items(board_posts, [], [])
    assert counts["board"] == 4
    assert counts["total_available"] == 4
    assert counts["total_considered"] == 4
    assert items[0]["date"] == "2026-01-04"  # most recent first


def test_build_voc_items_reports_pretruncation_counts():
    board_posts = [{"id": i, "title": "t", "content": "c", "created_at": f"2026-01-{i:02d}"} for i in range(1, 10)]
    items, counts = build_voc_items(board_posts[:5], board_posts[5:8], board_posts[8:])
    assert counts["board"] == 5
    assert counts["jira"] == 3
    assert counts["excel"] == 1
    assert counts["total_available"] == 9


def test_build_voc_items_truncates_to_max_items():
    many = [{"id": i, "title": "t", "content": "c", "created_at": f"2026-{(i % 12) + 1:02d}-01"} for i in range(MAX_ITEMS_FOR_PROMPT + 20)]
    items, counts = build_voc_items(many, [], [])
    assert len(items) == MAX_ITEMS_FOR_PROMPT
    assert counts["total_available"] == MAX_ITEMS_FOR_PROMPT + 20
    assert counts["total_considered"] == MAX_ITEMS_FOR_PROMPT


class _FakeJudgeClient:
    enabled = True

    def __init__(self, response):
        self.response = response
        self.calls = []

    def judge(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return dict(self.response)


def test_run_voc_analysis_raises_on_empty_input():
    client = _FakeJudgeClient({"summary": "x", "top_issues": []})
    with pytest.raises(ValueError):
        run_voc_analysis(client, [], [], [])


def test_run_voc_analysis_overwrites_source_counts_with_real_values():
    client = _FakeJudgeClient({"summary": "요약", "top_issues": [], "raw_source_counts": {"board": 999}})
    board_posts = [{"id": 1, "title": "t", "content": "c", "created_at": "2026-01-01"}]
    result = run_voc_analysis(client, board_posts, [], [])
    assert result["raw_source_counts"]["board"] == 1  # LLM이 준 999가 아니라 실제 집계값
    assert result["summary"] == "요약"
    assert len(client.calls) == 1


def test_build_prompts_includes_focus_instruction_when_given():
    items, counts = build_voc_items([{"id": 1, "title": "t", "content": "c", "created_at": "2026-01-01"}], [], [])
    system_prompt, _ = build_prompts(items, counts, focus_instruction="상담 대기시간 중심으로 분석해줘")
    assert "상담 대기시간 중심으로 분석해줘" in system_prompt


def test_build_prompts_omits_focus_section_when_blank():
    items, counts = build_voc_items([{"id": 1, "title": "t", "content": "c", "created_at": "2026-01-01"}], [], [])
    system_prompt, _ = build_prompts(items, counts, focus_instruction="")
    assert "사용자 지시사항" not in system_prompt


def test_build_voc_items_respects_custom_item_limit():
    board_posts = [{"id": i, "title": "t", "content": "c", "created_at": f"2026-01-{i:02d}"} for i in range(1, 31)]
    items, counts = build_voc_items(board_posts, [], [], item_limit=20)
    assert len(items) == 20
    assert counts["total_considered"] == 20
    assert counts["total_available"] == 30
    assert items[0]["date"] == "2026-01-30"  # 여전히 최신순


def test_run_voc_analysis_passes_focus_and_limit_through():
    client = _FakeJudgeClient({"summary": "요약", "top_issues": []})
    board_posts = [{"id": i, "title": "t", "content": "c", "created_at": f"2026-01-{i:02d}"} for i in range(1, 31)]
    result = run_voc_analysis(client, board_posts, [], [], focus_instruction="불친절 관련만", item_limit=5)
    assert result["raw_source_counts"]["total_considered"] == 5
    assert "불친절 관련만" in client.calls[0][0]  # system_prompt


# ===================== 독립 LLM Judge (자기평가 편향 방지) =====================

_VALID_ANALYSIS_RESULT = {
    "summary": "고객들은 대기시간과 불친절에 불만을 제기하고 있습니다.",
    "top_issues": [{"theme": "대기시간", "frequency": 3, "severity": "high", "suggestion": "인력 충원", "example_ids": ["post-1"]}],
}


def test_build_judge_prompts_includes_four_criteria():
    system_prompt, user_prompt = build_judge_prompts(_VALID_ANALYSIS_RESULT)
    for criterion in ("relevance", "root_cause_addressing", "feasibility", "measurability"):
        assert criterion in system_prompt
    assert "대기시간" in user_prompt  # 원본 분석 결과가 그대로 심사 대상으로 들어감


def test_build_judge_prompts_includes_focus_instruction():
    system_prompt, _ = build_judge_prompts(_VALID_ANALYSIS_RESULT, focus_instruction="대기시간 중심")
    assert "대기시간 중심" in system_prompt


def test_missing_summary_fails_without_calling_llm():
    """summary 누락 시 LLM 호출 없이 즉시 FAIL - 품질 미달 산출물에 LLM 비용을 쓰지 않음."""
    client = _FakeJudgeClient({"verdict": "PASS"})
    result = run_independent_judge(client, {"summary": "", "top_issues": [{"theme": "x"}]})
    assert result["verdict"] == "FAIL"
    assert client.calls == []


def test_missing_policy_fails_without_calling_llm():
    """top_issues(개선안/policy)가 비어있으면 즉시 FAIL - LLM 호출 없음."""
    client = _FakeJudgeClient({"verdict": "PASS"})
    result = run_independent_judge(client, {"summary": "요약 있음", "top_issues": []})
    assert result["verdict"] == "FAIL"
    assert client.calls == []


def test_independent_judge_calls_llm_for_valid_result():
    client = _FakeJudgeClient({"verdict": "PASS", "criteria": {"relevance": True, "root_cause_addressing": True, "feasibility": True, "measurability": True}, "reasoning": "타당함"})
    result = run_independent_judge(client, _VALID_ANALYSIS_RESULT, cross_model=True)
    assert result["verdict"] == "PASS"
    assert result["cross_model"] is True
    assert len(client.calls) == 1


def test_independent_judge_skipped_when_client_disabled():
    class _DisabledClient:
        enabled = False

        def judge(self, *a, **k):
            raise AssertionError("호출되면 안 됨")

    result = run_independent_judge(_DisabledClient(), _VALID_ANALYSIS_RESULT)
    assert result["verdict"] == "SKIPPED"
    assert result["cross_model"] is False


def test_independent_judge_skipped_when_client_none():
    result = run_independent_judge(None, _VALID_ANALYSIS_RESULT)
    assert result["verdict"] == "SKIPPED"


def test_independent_judge_degrades_gracefully_when_judge_call_fails():
    """독립 검증 호출 자체가 실패해도(네트워크 오류 등) 예외를 전파하지 않고 verdict=ERROR로
    감싸야 함 - 2차 검증 실패가 이미 성공한 1차 분석 결과를 무너뜨리면 안 됨."""
    class _BrokenJudgeClient:
        enabled = True

        def judge(self, *a, **k):
            raise RuntimeError("404 Not Found")

    result = run_independent_judge(_BrokenJudgeClient(), _VALID_ANALYSIS_RESULT)
    assert result["verdict"] == "ERROR"
    assert "404" in result["reasoning"]


def test_run_voc_analysis_with_judge_survives_judge_failure():
    """전체 파이프라인 레벨: 독립 Judge가 죽어도 생성 결과(summary/top_issues)는 그대로 반환."""
    class _BrokenJudgeClient:
        enabled = True

        def judge(self, *a, **k):
            raise RuntimeError("anthropic api error")

    generation_client = _FakeJudgeClient(_VALID_ANALYSIS_RESULT)
    board_posts = [{"id": 1, "title": "t", "content": "c", "created_at": "2026-01-01"}]

    result = run_voc_analysis_with_judge(generation_client, _BrokenJudgeClient(), board_posts, [], [])

    assert result["summary"] == _VALID_ANALYSIS_RESULT["summary"]  # 생성 결과는 살아있음
    assert result["judge"]["verdict"] == "ERROR"


def test_run_voc_analysis_with_judge_attaches_verdict():
    """test_full_voc_analysis_pipeline에 해당: 생성 -> 독립 검증까지 전체 흐름이 한 번에 성공."""
    generation_client = _FakeJudgeClient(_VALID_ANALYSIS_RESULT)
    judge_client = _FakeJudgeClient({"verdict": "PASS", "criteria": {"relevance": True, "root_cause_addressing": True, "feasibility": True, "measurability": True}, "reasoning": "타당함"})
    board_posts = [{"id": 1, "title": "t", "content": "c", "created_at": "2026-01-01"}]

    result = run_voc_analysis_with_judge(generation_client, judge_client, board_posts, [], [])

    assert result["summary"] == _VALID_ANALYSIS_RESULT["summary"]
    assert result["judge"]["verdict"] == "PASS"
    assert len(generation_client.calls) == 1
    assert len(judge_client.calls) == 1


def test_run_voc_analysis_with_judge_reports_fail_verdict():
    """test_pipeline_result_with_llm_judge에 해당: 독립 Judge가 FAIL을 매기면 그대로 노출됨(숨기지 않음)."""
    generation_client = _FakeJudgeClient(_VALID_ANALYSIS_RESULT)
    judge_client = _FakeJudgeClient({"verdict": "FAIL", "criteria": {"relevance": True, "root_cause_addressing": False, "feasibility": True, "measurability": False}, "reasoning": "근본 원인 대응 부족"})
    board_posts = [{"id": 1, "title": "t", "content": "c", "created_at": "2026-01-01"}]

    result = run_voc_analysis_with_judge(generation_client, judge_client, board_posts, [], [])

    assert result["judge"]["verdict"] == "FAIL"
    assert result["judge"]["criteria"]["root_cause_addressing"] is False


def test_run_voc_analysis_with_judge_propagates_generation_failure_without_calling_judge():
    class _FailingClient:
        enabled = True

        def judge(self, *a, **k):
            raise RuntimeError("생성 실패")

    judge_client = _FakeJudgeClient({"verdict": "PASS"})
    with pytest.raises(RuntimeError):
        run_voc_analysis_with_judge(_FailingClient(), judge_client, [{"id": 1, "title": "t", "content": "c", "created_at": "2026-01-01"}], [], [])
    assert judge_client.calls == []  # 생성이 실패하면 심사 자체가 호출되지 않음
