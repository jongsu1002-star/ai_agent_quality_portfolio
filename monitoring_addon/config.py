"""모니터링 애드온의 feature flag - 전부 `.env`(환경변수)로 켜고 끔.

기본값은 전부 True(opt-out 방식)라서 별도 설정 없이도 바로 동작하지만, 문제가 생기면
`.env`에 `MONITORING_ADDON_ENABLED=false`만 추가하고 서버를 재시작하면 이 애드온 전체가
꺼지고 기존 플랫폼은 그대로 남습니다(롤백 절차는 사용자_매뉴얼.md 11장 참고).
"""

from __future__ import annotations

import os


def _flag(name: str, default: bool = True) -> bool:
    """환경변수를 불리언으로 해석. 값이 아예 없으면 default, 있으면 "true/1/yes/on"만 참."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


MONITORING_ADDON_ENABLED = _flag("MONITORING_ADDON_ENABLED")  # 전체 마스터 스위치
K6_HISTORY_ENABLED = _flag("K6_HISTORY_ENABLED")  # false면 DB 없이 latest.json만 직접 읽어 반환
MONITORING_ADDON_DB_ENABLED = _flag("MONITORING_ADDON_DB_ENABLED")  # false면 스냅샷 스레드/DB 저장 비활성
PROMETHEUS_ADDON_ENABLED = _flag("PROMETHEUS_ADDON_ENABLED")  # false면 /metrics-addon 라우터 자체를 등록 안 함
GRAFANA_LINK_ENABLED = _flag("GRAFANA_LINK_ENABLED")  # false면 애드온 페이지에서 Grafana/Prometheus 링크 섹션 숨김
