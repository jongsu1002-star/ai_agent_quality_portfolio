"""테스트 케이스 데이터셋을 JSON/Excel로 읽고 쓰는 모듈."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Dict, List

import pandas as pd

from .models import GoldenCase


def load_dataset(path: str | Path) -> List[GoldenCase]:
    """JSON 또는 Excel 파일을 GoldenCase 리스트로 변환. 확장자로 형식을 판단."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("cases", [])
        return [GoldenCase.from_dict(item) for item in raw]
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
        records = df.to_dict(orient="records")
        # 빈 셀은 pandas가 NaN(float)으로 읽어옴 - "nan or None"은 nan이 truthy라 nan 자체가
        # 남아 JSON 직렬화 시 에러가 남(완전히 빈 컬럼은 float64 dtype이라 DataFrame.where()로
        # 미리 None을 넣어도 다시 NaN으로 되돌아가므로, dict로 변환된 뒤 값 단위로 정리해야 함)
        records = [{k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in row.items()} for row in records]
        return [GoldenCase.from_dict({
            "id": str(row.get("id") or row.get("case_id") or ""),
            "category": str(row.get("category") or "COM"),
            "question": str(row.get("question") or ""),
            "golden_answer": str(row.get("golden_answer") or row.get("expected_answer") or row.get("answer") or ""),
            # 배열 필드는 셀 안에서 "|" 구분자로 표현 (예: "DOC-001|DOC-002")
            "relevant_doc_ids": [item.strip() for item in str(row.get("relevant_doc_ids") or "").split("|") if item.strip()],
            "existing_answer": row.get("existing_answer") or None,
        }) for row in records]
    raise ValueError(f"Unsupported dataset format: {suffix}")


def save_dataset(cases: List[GoldenCase], path: str | Path) -> None:
    """GoldenCase 리스트를 JSON 파일로 저장 (현재 코드베이스에서 직접 호출하는 곳은 없지만
    데이터셋을 다시 파일로 내보내야 할 때 쓰는 유틸)."""
    payload = [
        {
            "id": case.id,
            "category": case.category,
            "question": case.question,
            "golden_answer": case.golden_answer,
            "relevant_doc_ids": case.relevant_doc_ids,
            "existing_answer": case.existing_answer,
        }
        for case in cases
    ]
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_testcase(path: str | Path) -> Dict[str, str]:
    """테스트 케이스 파일(id, question만 있는 발화문 목록)을 {id: question} 맵으로 변환.

    데이터셋(GoldenCase, 정답 포함)과 별도 파일 - golden_answer가 없어도 되는 훨씬 가벼운 스키마.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("cases", raw.get("testcases", []))
        return {str(item["id"]): str(item["question"]) for item in raw if item.get("id") and item.get("question")}
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
        records = df.to_dict(orient="records")
        # 빈 셀은 pandas가 NaN(float)으로 읽어옴 - "nan or None"은 nan이 truthy라 nan 자체가
        # 남아 JSON 직렬화 시 에러가 남(완전히 빈 컬럼은 float64 dtype이라 DataFrame.where()로
        # 미리 None을 넣어도 다시 NaN으로 되돌아가므로, dict로 변환된 뒤 값 단위로 정리해야 함)
        records = [{k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in row.items()} for row in records]
        return {
            str(row.get("id") or row.get("case_id") or ""): str(row.get("question") or "")
            for row in records
            if (row.get("id") or row.get("case_id")) and row.get("question")
        }
    raise ValueError(f"Unsupported test case format: {suffix}")


def build_testcase_template_workbook() -> BytesIO:
    """테스트 케이스(발화문) 전용 - id/question 2개 컬럼만 있는 간단한 양식."""
    template = pd.DataFrame([
        {"id": "TC-001", "question": "How do I reset my password?"},
        {"id": "TC-002", "question": "How do I update my profile?"},
    ])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        template.to_excel(writer, index=False, sheet_name="testcases")
    output.seek(0)
    return output


def build_voc_import_template_workbook() -> BytesIO:
    """VOC 자동분석에 외부 데이터를 얹을 때 쓰는 엑셀 양식 - source/date/category/content 4컬럼."""
    template = pd.DataFrame([
        {"source": "고객센터", "date": "2026-07-01", "category": "결제", "content": "결제 실패 후 재시도가 안 됩니다."},
        {"source": "앱스토어 리뷰", "date": "2026-07-02", "category": "UI", "content": "버튼이 너무 작아서 누르기 힘들어요."},
    ])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        template.to_excel(writer, index=False, sheet_name="voc")
    output.seek(0)
    return output


def load_voc_excel(path: str | Path) -> List[Dict[str, str]]:
    """VOC 외부 데이터 엑셀을 {source, date, category, content} 레코드 목록으로 변환.

    load_dataset()/load_testcase()와 동일한 NaN 스크럽 패턴 - content가 빈 행은 분석에
    의미가 없으므로 건너뜀."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise ValueError(f"Unsupported VOC excel format: {suffix}")
    df = pd.read_excel(path)
    records = df.to_dict(orient="records")
    records = [{k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in row.items()} for row in records]
    result = []
    for row in records:
        content = str(row.get("content") or row.get("내용") or "").strip()
        if not content:
            continue
        result.append({
            "source": str(row.get("source") or row.get("출처") or "excel"),
            "date": str(row.get("date") or row.get("일자") or ""),
            "category": str(row.get("category") or row.get("분류") or ""),
            "content": content,
        })
    return result


def build_template_workbook() -> BytesIO:
    """예시 2행이 담긴 빈 Excel 양식을 메모리(BytesIO)에서 생성 - /api/dataset/template이 반환."""
    template = pd.DataFrame([
        {
            "id": "TC-001",
            "category": "COM",
            "question": "How do I reset my password?",
            "golden_answer": "Use the password reset link",
            "relevant_doc_ids": "DOC-001|DOC-002",
            "required_keywords": "reset|password",
            "test_type": "functional",
            "existing_answer": "Use the password reset link",
            "existing_contexts": "Context A|Context B",
            "existing_doc_ids": "DOC-001|DOC-002",
        },
        {
            "id": "TC-002",
            "category": "ACC",
            "question": "How do I update my profile?",
            "golden_answer": "Open Profile Settings and save changes",
            "relevant_doc_ids": "DOC-101",
            "required_keywords": "profile|settings",
            "test_type": "regression",
            "existing_answer": "Open Profile Settings and save changes",
            "existing_contexts": "Context C",
            "existing_doc_ids": "DOC-101",
        },
    ])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        template.to_excel(writer, index=False, sheet_name="cases")
    output.seek(0)
    return output
