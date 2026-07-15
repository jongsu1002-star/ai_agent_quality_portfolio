"""VOC(불만/요구사항) 자동 분석 - 게시판 VOC 글 + (선택)Jira 백로그 + (선택)엑셀 업로드를
하나의 형태로 합쳐 LLM에게 개선안을 뽑아달라고 요청하는 파이프라인.

qa_agent 안의 모든 LLM 호출은 llm_client.OpenAIJudgeClient를 통해서만 이뤄져야 한다는
기존 규칙을 그대로 따름(evaluators.py와 동일).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .llm_client import OpenAIJudgeClient

MAX_ITEMS_FOR_PROMPT = 150
MAX_CONTENT_CHARS = 500


def _truncate(text: str, limit: int = MAX_CONTENT_CHARS) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "..."


def normalize_board_post(post: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": "board",
        "id": f"post-{post.get('id')}",
        "date": post.get("created_at", ""),
        "content": _truncate(f"[{post.get('title', '')}] {post.get('content', '')}"),
    }


def normalize_jira_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": "jira",
        "id": issue.get("key", ""),
        "date": issue.get("updated", ""),
        "content": _truncate(f"{issue.get('summary', '')} - {issue.get('description', '')}"),
    }


def normalize_excel_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": "excel",
        "id": f"excel-{row.get('source', '')}",
        "date": row.get("date", ""),
        "content": _truncate(row.get("content", "")),
    }


def build_voc_items(
    board_posts: List[Dict[str, Any]],
    jira_issues: List[Dict[str, Any]],
    excel_rows: List[Dict[str, Any]],
    item_limit: int = MAX_ITEMS_FOR_PROMPT,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """세 소스를 정규화 -> 최신순 정렬 -> item_limit(기본 150건)으로 절단.

    item_limit을 사용자가 더 작게 지정하면("최근 20건만 분석해줘") 그 값을 그대로 씀 -
    상한(MAX_ITEMS_FOR_PROMPT)보다 크게는 지정할 수 없음(라우터에서 clamp).

    source_counts에는 절단 이전의 실제 건수를 담아서, 분석 응답이 "무엇을 실제로 보고
    판단했는지"를 투명하게 알 수 있게 함(LLM이 건수를 스스로 세게 두지 않음).
    """
    board_items = [normalize_board_post(p) for p in board_posts]
    jira_items = [normalize_jira_issue(i) for i in jira_issues]
    excel_items = [normalize_excel_row(r) for r in excel_rows]

    source_counts = {
        "board": len(board_items),
        "jira": len(jira_items),
        "excel": len(excel_items),
    }
    all_items = board_items + jira_items + excel_items
    all_items.sort(key=lambda item: item.get("date") or "", reverse=True)
    source_counts["total_available"] = len(all_items)

    truncated = all_items[:item_limit]
    source_counts["total_considered"] = len(truncated)
    return truncated, source_counts


def build_prompts(items: List[Dict[str, Any]], source_counts: Dict[str, int], focus_instruction: str = "") -> Tuple[str, str]:
    system_prompt = (
        "당신은 제품 VOC(고객의 소리) 분석 전문가입니다. 주어진 VOC 항목들을 분석해 반드시 "
        "다음 JSON 스키마로만 답하세요(마크다운 코드펜스 없이 순수 JSON 객체 하나만):\n"
        '{"summary": "2~4문장 총평", "top_issues": ['
        '{"theme": "이슈 주제", "frequency": 빈도(정수), "severity": "high|medium|low", '
        '"suggestion": "구체적 개선안", "example_ids": ["근거로 삼은 항목 id들"]}]}\n'
        "top_issues는 빈도/심각도가 높은 순으로 최대 10개까지 정리하세요."
    )
    if focus_instruction.strip():
        # 사용자가 특정 주제/관점으로 좁혀서 요청한 경우("상담 대기시간과 불친절 중심으로
        # 정책 개선안 제시" 등) - 관련 없는 항목은 top_issues에서 제외하고 지시사항에 맞춰
        # summary/suggestion을 작성하도록 시스템 프롬프트에 명시적으로 반영
        system_prompt += f"\n\n사용자 지시사항(반드시 우선 반영): {focus_instruction.strip()}"
    lines = [f"- [{item['id']}] ({item['source']}, {item['date']}) {item['content']}" for item in items]
    user_prompt = (
        f"총 {source_counts['total_available']}건 중 최신 {source_counts['total_considered']}건을 분석합니다.\n\n"
        + "\n".join(lines)
    )
    return system_prompt, user_prompt


def run_voc_analysis(
    judge_client: OpenAIJudgeClient,
    board_posts: List[Dict[str, Any]],
    jira_issues: List[Dict[str, Any]],
    excel_rows: List[Dict[str, Any]],
    focus_instruction: str = "",
    item_limit: int = MAX_ITEMS_FOR_PROMPT,
) -> Dict[str, Any]:
    """정규화 -> 프롬프트 구성 -> LLM 호출 -> raw_source_counts 보강.

    focus_instruction: "상담 대기시간과 불친절 중심으로 정책 개선안을 제시해줘" 같은 자연어
    지시사항(선택) - 시스템 프롬프트에 최우선 반영 지시로 추가됨.
    item_limit: "최근 20건만 분석해줘" 같은 건수 제한(선택, 기본 MAX_ITEMS_FOR_PROMPT).

    입력이 하나도 없으면 ValueError. judge_client.judge()가 던지는 예외(키 미설정, 네트워크
    오류, JSON 파싱 실패 등)는 잡지 않고 그대로 전파 - 호출부(HTTP 라우트)가 잡아서 사용자
    에게 우아하게(500이 아니라 안내 메시지로) 보여줘야 함.
    """
    items, source_counts = build_voc_items(board_posts, jira_issues, excel_rows, item_limit=item_limit)
    if not items:
        raise ValueError("분석할 VOC 데이터가 없습니다 (게시판/Jira/엑셀 모두 비어 있음)")

    system_prompt, user_prompt = build_prompts(items, source_counts, focus_instruction=focus_instruction)
    result = judge_client.judge(system_prompt, user_prompt)
    # LLM이 건수를 스스로 세게 두지 않고, 실제 집계값으로 항상 덮어씀
    result["raw_source_counts"] = source_counts
    return result


def build_judge_prompts(analysis_result: Dict[str, Any], focus_instruction: str = "") -> Tuple[str, str]:
    """독립 검증(Judge) 프롬프트 구성 - 분석을 생성한 모델과는 다른 모델/시각으로
    결과물 자체(요약+개선안)를 채점하기 위한 프롬프트. 원본 VOC 데이터가 아니라 이미
    생성된 summary/top_issues만 입력으로 받음(자기평가 편향 방지 목적상, 생성 근거를
    재확인하는 것이 아니라 산출물의 타당성 자체를 외부 시각에서 평가)."""
    system_prompt = (
        "당신은 독립적인 QA 심사관(Judge)입니다. 다른 AI가 생성한 VOC 분석 결과(요약+개선안)를 "
        "다음 4가지 기준으로 냉정하게 심사하세요:\n"
        "1. relevance: 개선안이 실제 불만 내용과 직접 연계되는가(엉뚱한 제안 아님)\n"
        "2. root_cause_addressing: 표면적 증상이 아니라 근본 원인에 대응하는가\n"
        "3. feasibility: 대상/우선순위가 구체적이어서 실행 가능한가(구호성 문구가 아님)\n"
        "4. measurability: 개선 효과를 검증할 수 있는 방식이 제시되었거나 유추 가능한가\n"
        "반드시 다음 JSON 스키마로만 답하세요(마크다운 코드펜스 없이 순수 JSON 객체 하나만):\n"
        '{"verdict": "PASS"|"FAIL", "criteria": {"relevance": true|false, '
        '"root_cause_addressing": true|false, "feasibility": true|false, "measurability": true|false}, '
        '"reasoning": "2~3문장 판정 근거"}\n'
        "4개 기준 중 하나라도 false면 verdict는 FAIL이어야 합니다."
    )
    if focus_instruction.strip():
        system_prompt += f"\n\n원 분석 지시사항(이 관점에 실제로 부합하는지도 함께 판단): {focus_instruction.strip()}"
    user_prompt = json.dumps({"summary": analysis_result.get("summary"), "top_issues": analysis_result.get("top_issues")}, ensure_ascii=False, indent=2)
    return system_prompt, user_prompt


def run_independent_judge(judge_client: Optional[OpenAIJudgeClient], analysis_result: Dict[str, Any], focus_instruction: str = "", cross_model: bool = True) -> Dict[str, Any]:
    """생성된 분석 결과(summary/top_issues)를 독립적으로 재검증.

    필수 항목(summary/top_issues)이 비어있으면 LLM을 호출하지 않고 즉시 FAIL - 품질 미달
    산출물이 LLM 채점 비용까지 쓰며 통과되는 일을 막음(결정적 사전 게이트).

    judge_client가 None이거나 비활성화면(교차검증용 provider 키 미설정 등) 호출 자체를
    건너뛰고 verdict="SKIPPED"로 표시 - 검증 불가 상태를 "통과"로 위장하지 않음.

    독립 검증 LLM 호출 자체가 실패하면(네트워크 오류, 잘못된 모델명 등) verdict="ERROR"로
    우아하게 표시하고 예외를 삼킴 - 2차 검증 실패가 이미 성공적으로 생성된 1차 분석 결과
    전체를 무너뜨려서는 안 됨(evaluators.py의 LLMJudgeResult.errored와 동일한 원칙).
    """
    if not analysis_result.get("summary"):
        return {"verdict": "FAIL", "criteria": {}, "reasoning": "summary가 비어 있습니다", "cross_model": cross_model}
    if not analysis_result.get("top_issues"):
        return {"verdict": "FAIL", "criteria": {}, "reasoning": "top_issues(개선안)가 비어 있습니다", "cross_model": cross_model}

    if judge_client is None or not judge_client.enabled:
        return {"verdict": "SKIPPED", "criteria": {}, "reasoning": "독립 검증용 LLM이 설정되지 않았습니다", "cross_model": False}

    system_prompt, user_prompt = build_judge_prompts(analysis_result, focus_instruction=focus_instruction)
    try:
        verdict = judge_client.judge(system_prompt, user_prompt)
    except Exception as exc:
        return {"verdict": "ERROR", "criteria": {}, "reasoning": f"독립 검증 호출 실패: {exc}", "cross_model": cross_model}
    verdict["cross_model"] = cross_model
    return verdict


def run_voc_analysis_with_judge(
    generation_client: OpenAIJudgeClient,
    judge_client: Optional[OpenAIJudgeClient],
    board_posts: List[Dict[str, Any]],
    jira_issues: List[Dict[str, Any]],
    excel_rows: List[Dict[str, Any]],
    focus_instruction: str = "",
    item_limit: int = MAX_ITEMS_FOR_PROMPT,
    cross_model: bool = True,
) -> Dict[str, Any]:
    """run_voc_analysis()로 생성 -> run_independent_judge()로 다른 모델이 재검증 -> 결과에 병합.

    "생성 주체와 심사 주체를 분리"하는 것이 핵심이라, generation_client와 judge_client는
    서로 다른 provider로 구성되는 것이 정상 경로(app/main.py::_independent_judge_kwargs가
    가능하면 반대 provider를 고름). 생성이 실패하면(ValueError/LLM 오류) 그대로 전파되어
    Judge 호출 자체가 일어나지 않음 - 실패한 산출물을 심사할 필요가 없음.
    """
    result = run_voc_analysis(generation_client, board_posts, jira_issues, excel_rows, focus_instruction=focus_instruction, item_limit=item_limit)
    result["judge"] = run_independent_judge(judge_client, result, focus_instruction=focus_instruction, cross_model=cross_model)
    return result
