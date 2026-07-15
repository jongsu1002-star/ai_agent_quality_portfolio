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
    """VOC 자동분석에 외부 데이터를 얹을 때 쓰는 엑셀 양식 - source/date/category/content 4컬럼.

    date는 필수 항목이 아님을 양식에서부터 보여주기 위해 두 번째 예시 행은 date를 비워둠."""
    template = pd.DataFrame([
        {"source": "고객센터", "date": "2026-07-01", "category": "결제", "content": "결제 실패 후 재시도가 안 됩니다."},
        {"source": "앱스토어 리뷰", "date": "", "category": "UI", "content": "버튼이 너무 작아서 누르기 힘들어요."},
    ])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        template.to_excel(writer, index=False, sheet_name="voc")
    output.seek(0)
    return output


def build_voc_json_template() -> bytes:
    """VOC 외부 데이터 JSON 양식 - 엑셀 양식과 동일한 필드/의미(date는 선택)."""
    template = [
        {"source": "고객센터", "date": "2026-07-01", "category": "결제", "content": "결제 실패 후 재시도가 안 됩니다."},
        {"source": "앱스토어 리뷰", "date": "", "category": "UI", "content": "버튼이 너무 작아서 누르기 힘들어요."},
    ]
    return json.dumps(template, ensure_ascii=False, indent=2).encode("utf-8")


def _is_na_scalar(value: object) -> bool:
    """pandas가 반환하는 결측치(NaN/NaT/None)를 형식에 관계없이 판별.

    date처럼 선택적인 열은 엑셀에서 빈 셀일 때 float NaN이 아니라 pandas Timestamp의
    NaT로 들어올 수 있어, `isinstance(v, float)`만으로는 걸러지지 않고 문자열 "NaT"가
    그대로 노출되는 문제가 있었다. pd.isna()는 스칼라 대부분(NaN/NaT/None)에 동작하지만
    리스트/배열류에는 모호한 진리값 오류를 낼 수 있어 예외 시 결측 아님으로 안전하게 처리."""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _field(row: Dict[str, object], *keys: str) -> str:
    """row에서 keys를 순서대로 찾아 결측치(NaN/NaT/None)가 아닌 첫 값을 문자열로 반환.

    Excel/JSON 왕복 시 빈 셀이 float NaN으로 들어올 수 있는데, NaN은 파이썬 진리값으로
    참(bool(float('nan')) == True)이라 `row.get(k) or ...` 방식의 폴백은 NaN을 걸러내지
    못하고 문자열 "nan"이 그대로 값으로 남는 문제가 있었다."""
    for key in keys:
        value = row.get(key)
        if not _is_na_scalar(value) and value not in (None, ""):
            return str(value)
    return ""


def _normalize_voc_record(row: Dict[str, object]) -> Dict[str, str] | None:
    """source/date/category/content 4필드로 정규화. content 없는 행은 None(호출부가 skip)."""
    content = _field(row, "content", "내용").strip()
    if not content:
        return None
    return {
        "source": _field(row, "source", "출처") or "excel",
        "date": _field(row, "date", "일자"),
        "category": _field(row, "category", "분류"),
        "content": content,
    }


def load_voc_excel(path: str | Path) -> List[Dict[str, str]]:
    """VOC 외부 데이터 엑셀을 {source, date, category, content} 레코드 목록으로 변환.

    date는 선택 항목 - 비어 있어도 오류 없이 빈 문자열로 처리한다(정렬 시 항상 뒤로 감,
    qa_agent/voc_analysis.py::_date_sort_key). content가 빈 행은 분석에 의미가 없으므로
    건너뛴다."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix != ".xlsx":
        # .xls(구형 바이너리 포맷)는 pandas가 읽으려면 xlrd 의존성이 별도로 필요한데
        # requirements.txt에 없어 실제로는 처리에 실패함 - 있지도 않은 지원을 광고하지
        # 않도록 .xlsx만 허용(라우터/화면의 허용 확장자와 반드시 일치시킬 것)
        raise ValueError(f"Unsupported VOC excel format: {suffix} (.xlsx만 지원합니다)")
    df = pd.read_excel(path)
    records = df.to_dict(orient="records")
    result = []
    for row in records:
        normalized = _normalize_voc_record(row)
        if normalized is not None:
            result.append(normalized)
    return result


def load_voc_json(path: str | Path) -> List[Dict[str, str]]:
    """VOC 외부 데이터 JSON(배열 형태)을 {source, date, category, content} 레코드 목록으로 변환.

    load_voc_excel()과 동일한 필드 규칙(date 선택)을 공유한다."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 형식이 올바르지 않습니다: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("JSON 최상위는 배열([...])이어야 합니다")
    result = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        normalized = _normalize_voc_record(row)
        if normalized is not None:
            result.append(normalized)
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
