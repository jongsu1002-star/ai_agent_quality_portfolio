"""모니터링 애드온 SQLite 스키마를 미리 만들어두는 초기화 스크립트.

`data/monitoring_addon.db`는 서버가 처음 뜰 때 자동으로 생성되므로 평소에는 이 스크립트를
따로 실행할 필요가 없습니다 - CI 환경이나 서버 시작 전에 DB 파일/스키마 존재를 미리
보장해두고 싶을 때 사용합니다. `CREATE TABLE IF NOT EXISTS` 기반이라 여러 번 실행해도
안전합니다.

실행: python scripts/init_monitoring_addon_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monitoring_addon.db import MonitoringAddonDB  # noqa: E402


def main() -> None:
    db_path = Path("data") / "monitoring_addon.db"
    MonitoringAddonDB(path=str(db_path))
    print(f"[monitoring-addon] DB 스키마 준비 완료: {db_path}")


if __name__ == "__main__":
    main()
