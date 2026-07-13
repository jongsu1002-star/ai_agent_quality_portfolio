"""기존 AI Agent 품질관리·운영 모니터링 플랫폼에 얹는 별도 확장 모듈.

이 패키지는 k6 성능테스트 결과 조회, SQLite 기반 실행 이력 관리, Prometheus/Grafana 연동을
제공합니다. `monitoring/`(기존 자체 지표·외부 URL 모니터링)과는 완전히 분리되어 있으며,
기존 MetricsCollector/미들웨어/`/api/monitoring/summary`는 여기서 읽기 전용으로만 조회합니다.

`monitoring_addon/config.py`의 feature flag가 꺼져 있으면 `app/main.py`가 이 패키지를 아예
import하지 않으므로(가드된 try/except), 이 패키지에 문제가 생겨도 기존 서비스는 영향을
받지 않습니다.
"""
