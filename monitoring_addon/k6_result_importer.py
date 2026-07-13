"""k6 결과 JSON(dict)을 monitoring_addon DB에 저장하는 얇은 계층.

실제 저장 로직(중복 run_id 방지 등)은 db.py의 `insert_k6_run`이 갖고 있고, 여기서는
"reports/k6/latest.json을 읽어서 DB에 넣는다"는 상위 흐름만 담당합니다 - API 라우터와
scripts/import_k6_result.py(CLI) 양쪽에서 재사용합니다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .db import MonitoringAddonDB
from .k6_result_reader import K6ResultInvalid, K6ResultNotFound, read_k6_result


def import_latest_k6_result(db: MonitoringAddonDB, reports_dir: str = "reports/k6") -> Dict[str, Any]:
    """reports/k6/latest.json을 읽어 DB에 저장. 파일 없음/JSON 오류는 status로 알림."""
    latest_path = Path(reports_dir) / "latest.json"
    try:
        run = read_k6_result(latest_path)
    except K6ResultNotFound:
        return {"status": "no_data"}
    except K6ResultInvalid as exc:
        return {"status": "invalid_json", "error": str(exc)}

    inserted = db.insert_k6_run(run, raw_json_path=str(latest_path), thresholds=run.get("thresholds") or [])
    return {"status": "ok", "imported": inserted, "run_id": run.get("run_id")}
