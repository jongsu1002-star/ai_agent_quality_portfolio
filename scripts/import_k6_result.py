"""reports/k6/latest.json을 monitoring_addon DB로 수동 import하는 CLI.

서버가 떠 있지 않아도(API 없이) k6 실행 직후 바로 DB에 반영하고 싶을 때 사용합니다.
같은 run_id가 이미 저장되어 있으면 아무것도 하지 않습니다(설계서 8.4의 중복 방지 원칙과 동일).

실행: python scripts/import_k6_result.py [reports/k6 디렉터리 경로]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monitoring_addon.db import MonitoringAddonDB  # noqa: E402
from monitoring_addon.k6_result_importer import import_latest_k6_result  # noqa: E402


def main() -> None:
    reports_dir = sys.argv[1] if len(sys.argv) > 1 else "reports/k6"
    db = MonitoringAddonDB(path=str(Path("data") / "monitoring_addon.db"))
    result = import_latest_k6_result(db, reports_dir=reports_dir)

    if result["status"] == "no_data":
        print(f"[monitoring-addon] {reports_dir}/latest.json 파일이 없습니다.")
        sys.exit(1)
    if result["status"] == "invalid_json":
        print(f"[monitoring-addon] JSON 파싱 실패: {result.get('error')}")
        sys.exit(1)

    if result["imported"]:
        print(f"[monitoring-addon] run_id={result['run_id']} 를 새로 저장했습니다.")
    else:
        print(f"[monitoring-addon] run_id={result['run_id']} 는 이미 저장되어 있어 건너뛰었습니다.")


if __name__ == "__main__":
    main()
