# 모니터링 애드온 가이드 (k6 성능테스트 · SQLite 이력관리 · Prometheus/Grafana)

이 파일은 **모니터링 애드온**(이번에 추가된 확장 기능)만 다룹니다. 플랫폼 자체의 설치/실행/
데이터셋/대시보드/API 사용법은 [docs/README.md](docs/README.md)와
[docs/사용자_매뉴얼.md](docs/사용자_매뉴얼.md)를 보세요.

## 이 애드온이 무엇인가

기존에 서비스 중인 **AI Agent 품질관리·운영 모니터링 플랫폼**은 절대 수정하지 않고, 그 옆에
별도로 얹은 확장 기능입니다:

- k6 성능테스트 결과를 조회하고 여러 번 실행한 이력을 SQLite(`data/monitoring_addon.db`)에 저장
- 기존 자체 운영 지표(`MetricsCollector`)를 1분 주기로 읽기 전용 스냅샷 저장 (장기 이력)
- Prometheus가 수집할 수 있는 `/metrics-addon` 엔드포인트 제공
- Grafana 대시보드로 위 지표들을 시각화

기존 `MetricsCollector`, `@app.middleware("http")`, `/api/monitoring/summary` 응답 구조,
기존 모니터링 화면(카드/차트/폴링), `HealthChecker`는 **한 줄도 수정하지 않았습니다** -
`app/main.py`에는 새 라우터/스레드를 등록하는 코드만 가드된 `try/except`로 추가되어 있고,
실패해도 기존 서비스 기동에는 영향이 없습니다.

## 빠른 시작

### 1. 서버 실행 (평소와 동일)

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

애드온은 기본적으로 켜져 있습니다(`.env`에 아무것도 안 써도 동작). 새 페이지는
`http://localhost:8000/monitoring-addon`이고, 기존 대시보드의 "모니터링" 탭에도 그곳으로
가는 링크가 한 줄 추가되어 있습니다.

**Prometheus/Grafana까지 한 번에 띄우고 싶다면** 아래 스크립트를 대신 쓰세요 - Docker로
Prometheus/Grafana를 먼저 기동한 뒤 같은 프로세스에서 이어서 FastAPI 앱을 실행합니다
(Docker가 없거나 꺼져 있어도 Prometheus/Grafana만 조용히 건너뛰고 앱은 정상적으로 뜹니다):

```bash
python scripts/start_platform.py
```

### 1-1. 전체를 Docker로 한 번에 실행 (앱 + Prometheus + Grafana)

로컬에 Python을 따로 설치하지 않고 전체 스택을 컨테이너로 띄우고 싶다면:

```bash
docker compose up -d
```

`qa-platform`(FastAPI 앱, 8000), `prometheus`(9090), `grafana`(3000) 세 컨테이너가 한 번에
뜹니다. 이미지에는 k6 실행 파일도 포함되어 있어 "웹에서 직접 k6 실행" 기능도 컨테이너 안에서
그대로 동작합니다. `reports/`는 호스트와 바인드 마운트되어 데이터셋/문서/k6 결과를 호스트에서
직접 확인할 수 있고, `data/`(모니터링 애드온 SQLite)는 도커 네임드 볼륨(`qa-platform-data`)에
저장됩니다 - SQLite WAL 모드가 Windows 호스트 바인드 마운트에서 파일 잠금 문제(`disk I/O error`)를
일으키기 때문에 일부러 이렇게 분리했습니다.

앱만 띄우고 Prometheus/Grafana는 빼고 싶으면:

```bash
docker compose up -d qa-platform
```

종료:

```bash
docker compose down
```

로그 확인:

```bash
docker compose logs -f qa-platform
```

코드를 수정한 뒤에는 다시 빌드해야 반영됩니다:

```bash
docker compose up -d --build
```

### 2. k6 성능테스트 실행

**웹에서 직접 실행**: `/monitoring-addon` 페이지의 "k6 성능테스트 실행" 카드에서 대상 URL/
동시사용자수(1~50)/지속시간(10초~2분)을 입력하고 "실행"을 누르면 됩니다. 대상 URL은
localhost/사내망 IP만 허용되고(외부 인터넷 주소는 400 거부), 한 번에 하나만 실행됩니다.
자세한 내용은 [docs/사용자_매뉴얼.md](docs/사용자_매뉴얼.md)의 "웹에서 직접 k6 실행" 참고.

**CLI로 실행**(URL/시간 제한 없음):

```bash
# k6 설치: https://k6.io/docs/get-started/installation/
k6 run tests/k6/load_test.js
```

실행이 끝나면 `reports/k6/latest.json`, `reports/k6/history/{run_id}.json`이 생성됩니다.
`/monitoring-addon` 페이지를 새로고침하면(또는 `GET /api/monitoring-addon/k6/latest` 호출 시)
자동으로 DB에 import되어 반영됩니다. 서버 없이 바로 DB에 넣고 싶으면:

```bash
python scripts/import_k6_result.py
```

대상 URL/VU 수/지속시간은 환경변수로 바꿀 수 있습니다:

```bash
LOAD_TARGET_URL=http://localhost:8000 LOAD_VUS=20 LOAD_DURATION=1m k6 run tests/k6/load_test.js
```

### 3. Prometheus / Grafana만 따로 실행 (앱은 Docker 밖에서 직접 띄운 경우)

앱을 Docker로 띄웠다면(`docker compose up -d`) 이 단계는 필요 없습니다(Prometheus/Grafana도
같이 뜸). 앱을 `uvicorn`으로 로컬에서 직접 띄운 경우에만 아래 별도 compose 파일을 씁니다:

```bash
docker compose -f infra/docker-compose.monitoring.yml up -d
```

- Prometheus: http://localhost:9090 (Targets 메뉴에서 `ai-agent-monitoring-addon` job이 UP인지 확인)
  - `/monitoring-addon` 페이지에는 Prometheus `/graph` 쿼리 화면도 iframe으로 직접
    임베딩되어 있습니다(별도 설정 불필요, Grafana처럼 X-Frame-Options 제약이 없음).
- Grafana: http://localhost:3000 - Prometheus 데이터소스와 "QA Platform Monitoring Addon"
  대시보드가 자동으로 provisioning되어 있습니다. 익명 Viewer 접속이 켜져 있어 로그인 없이
  바로 보이며(admin/admin으로 직접 로그인도 가능), `/monitoring-addon` 페이지에는 이
  대시보드의 6개 패널이 iframe으로 직접 임베딩되어 있어 Grafana를 따로 열지 않아도
  실시간 그래프를 바로 확인할 수 있습니다.

종료:

```bash
docker compose -f infra/docker-compose.monitoring.yml down
```

## Feature Flag (`.env`)

| 변수 | 기본값 | 끄면 어떻게 되는지 |
|---|---|---|
| `MONITORING_ADDON_ENABLED` | true | 애드온 라우터/스레드/페이지 전체가 등록되지 않음 (`/monitoring-addon`, `/api/monitoring-addon/*`, `/metrics-addon` 전부 404) |
| `K6_HISTORY_ENABLED` | true | `k6/runs`, `k6/runs/{id}` 이력 조회만 비활성(`/k6/latest`는 파일을 직접 읽어 계속 동작) |
| `MONITORING_ADDON_DB_ENABLED` | true | SQLite 저장/조회 전부 비활성 - 스냅샷 스레드도 시작하지 않음 |
| `PROMETHEUS_ADDON_ENABLED` | true | `/metrics-addon` 라우터 자체를 등록하지 않음 |
| `GRAFANA_LINK_ENABLED` | true | `/monitoring-addon` 페이지에서 Prometheus/Grafana 링크 섹션을 숨김 |

## 롤백 절차

1. `.env`에 `MONITORING_ADDON_ENABLED=false` 추가
2. 서버 재시작 - 애드온 라우트가 전부 사라지고 기존 플랫폼은 그대로 동작
3. 그래도 문제가 있으면 `monitoring_addon/`, `app/routers/monitoring_addon.py`,
   `app/templates/monitoring_addon.html`을 삭제
4. `data/monitoring_addon.db`(및 `-wal`/`-shm`) 삭제 (Docker로 띄운 경우 `docker volume rm ai_agent_quality_portfolio_qa-platform-data`)
5. `docker compose down`(전체 스택) 또는 `docker compose -f infra/docker-compose.monitoring.yml down`(Prometheus/Grafana만 따로 띄운 경우)으로 컨테이너 정리
6. 기존 `/`, `/api/monitoring/summary`, 기존 QA 파이프라인 API가 정상인지 확인

## 새 REST API

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/monitoring-addon/k6/latest` | 최신 k6 실행 결과 (DB에 없으면 파일에서 import 시도) |
| GET | `/api/monitoring-addon/k6/runs?page=&page_size=&result=` | k6 실행 이력 목록 (Pass/Fail 필터 가능) |
| GET | `/api/monitoring-addon/k6/runs/{run_id}` | 특정 실행 상세 + threshold 결과 |
| POST | `/api/monitoring-addon/k6/import` | `reports/k6/latest.json` 수동 import |
| POST | `/api/monitoring-addon/k6/trigger` | 웹에서 k6 실행 시작 (대상 URL/동시사용자수/지속시간, 내부망만 허용) |
| GET | `/api/monitoring-addon/k6/trigger/status` | 웹 트리거 실행의 진행 상태 폴링 |
| GET | `/api/monitoring-addon/history/summary?limit=` | 자체 운영 지표의 장기 스냅샷 이력 |
| GET | `/metrics-addon` | Prometheus 텍스트 노출 포맷 |

## 테스트

```bash
pytest tests/test_monitoring_addon_db.py tests/test_monitoring_addon_k6_import.py tests/test_monitoring_addon_api.py -q
```

전체 스위트(`pytest -q`)에는 기존 서비스가 애드온 적용 후에도 그대로 동작하는지 확인하는
회귀 테스트(`test_monitoring_summary_response_shape_is_unchanged_by_monitoring_addon`)가
포함되어 있습니다.
