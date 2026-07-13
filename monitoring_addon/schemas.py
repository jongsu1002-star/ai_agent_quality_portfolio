"""API 응답 모양을 만드는 순수 함수 모음 - 새 의존성(pydantic 모델 추가) 없이 plain dict로 처리.

`db.py`는 SQLite 컬럼 그대로 평평한(flat) dict를 돌려주는데, 여기서는 그걸 k6 원본 JSON과
비슷하게(그리고 사용자_매뉴얼.md에 적어둔 것과 같게) `http_req_duration` 중첩 구조로 다시
포장해서 API 응답으로 내보냅니다.
"""

from __future__ import annotations

from typing import Any, Dict, List


def shape_k6_run(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": row.get("run_id"),
        "scenario": row.get("scenario"),
        "target_url": row.get("target_url"),
        "vus": row.get("vus"),
        "total_requests": row.get("total_requests"),
        "failed_rate": row.get("failed_rate"),
        "checks_rate": row.get("checks_rate"),
        "http_req_duration": {
            "avg_ms": row.get("avg_ms"),
            "min_ms": row.get("min_ms"),
            "med_ms": row.get("med_ms"),
            "max_ms": row.get("max_ms"),
            "p90_ms": row.get("p90_ms"),
            "p95_ms": row.get("p95_ms"),
            "p99_ms": row.get("p99_ms"),
        },
        "thresholds": row.get("thresholds", []),
        "thresholds_passed": bool(row.get("thresholds_passed")),
        "result": row.get("result"),
        "raw_json_path": row.get("raw_json_path"),
        "created_at": row.get("created_at"),
    }


def shape_k6_run_list(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [shape_k6_run(row) for row in rows]
