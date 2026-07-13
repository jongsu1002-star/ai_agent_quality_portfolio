# AI Agent Quality Portfolio

AI 에이전트/챗봇을 위한 자동 QA 파이프라인입니다. 골든 테스트 케이스(질문 + 기대 정답)를
사람이 수작업으로 확인하는 대신 자동으로 채점합니다. `qa_agent/`가 공유 코어 엔진이며,
`app/`이 이를 FastAPI 대시보드/REST API로 노출합니다. `quality/`, `tests/`,
`performance/`, `monitoring/`, Docker 관련 파일은 이 엔진을 둘러싼 보조 레이어입니다.

## 문서 목록

| 문서 | 성격 | 갱신 방식 |
|---|---|---|
| [사용자_매뉴얼.md](사용자_매뉴얼.md) | 설치/실행/데이터셋/대시보드/API 사용법 | 수동 (코드 변경 시 함께 갱신) |
| [설계서.md](설계서.md) | 아키텍처/데이터모델/설정/모듈별 상세 | 수동 |
| [프로세스_명세서.md](프로세스_명세서.md) | 파이프라인 단계별 처리 흐름 | 수동 |
| [테스트_결과.md](테스트_결과.md) | 최근 `pytest` 실행 결과 | **자동** — `pytest` 실행 시마다 `conftest.py`가 재생성 |
| [결함보고서.md](결함보고서.md) | 최근 QA 파이프라인 실행의 결함 목록 | **자동** — 파이프라인 실행 완료 시마다 `qa_agent/reporter.py`가 재생성 |
| [팀원용_접속가이드.md](팀원용_접속가이드.md) | 같은 네트워크의 다른 PC에서 대시보드에 접속하는 방법 | 수동 |

위 5개 문서는 웹 대시보드에서도 항상 최신 내용을 그대로 볼 수 있습니다 (`GET /api/docs/{key}`가 매 요청마다 파일을 새로 읽음) — 수동 관리 문서 3종(사용자_매뉴얼/설계서/프로세스_명세서)과 접속 가이드는 "설정" 탭 → "문서" 카드, 자동 생성 2종(테스트_결과/결함보고서)은 "대시보드" 탭 → "테스트 결과 · 결함보고서" 카드.

수동 관리 문서 3종은 `tests/test_docs_reference_integrity.py`가 그 안에서 언급하는 파일 경로/클래스/함수/API 경로가 실제 코드에 여전히 존재하는지 `pytest` 실행 시마다 검사합니다. 코드에서 이름을 바꾸거나 삭제했는데 문서를 안 고치면 이 테스트가 실패해 드러납니다 — 자동 생성은 아니지만 "괴리를 방치할 수 없게" 만드는 장치입니다.

## 구조

- `qa_agent/` — 코어 파이프라인 및 데이터 모델 (엔진 원본; 로직을 다른 곳에 중복 구현하지 말 것)
- `app/` — FastAPI 서비스 진입점 + 대시보드
- `quality/` — CI 품질 게이트 헬퍼 (`QualityCheckRunner.run_gate`)
- `tests/` — 엔진 및 API에 대한 회귀 테스트
- `performance/` — 경량 파이프라인 벤치마킹 (부하/TPS 도구 아님 — 아래 참고)
- `monitoring/` — 헬스체크
- `desktop_app/` — 윈도우 데스크톱 실행 파일(pywebview로 기존 서버에 접속만 하는 래퍼, 4.1장 참고)
- `Dockerfile`, `docker-compose.yml` — 컨테이너 배포 지원

## 코어 엔진 (`qa_agent/`)

- **Connector** (`config.connector.mode`): 케이스별 답변을 얻는 방식.
  - `dataset_only` (기본값) — `case.existing_answer/existing_contexts/existing_doc_ids`를 그대로 재사용.
  - `mock` — 키워드 매칭 기반 임시 답변. 실제 챗봇 없이 배선을 검증할 때 유용.
  - `api` — `config.connector.api_endpoint`에 `{question, case_id}`로 호출하고
    `{answer, contexts, doc_ids}` 응답을 기대. 커넥터 실패는 `ChatbotResponse.error`로
    캡처되어 해당 케이스의 평가를 즉시 건너뜀(인프라 오류이므로 품질 실패로 집계되지 않음).
- **Evaluators** (`qa_agent/evaluators.py`): 검색품질(Recall/Precision/MRR), 근거성 및
  컨텍스트 관련성(룰 우선, 애매한 경우에만 LLM 보정), LLM-as-a-Judge(정확성/관련성/일관성/유해성),
  루브릭 채점, 골든 답변 대비 회귀 테스트, 그리고 항상 실행되는 유해성/PII 가드레일로 구성됩니다.
  대부분의 항목은 "이원 평가(Dual Evaluation)"로, 빠른 룰 기반 판정과 선택적 LLM 판정을
  `comparison_mode.pass_policy`(`either_pass`/`both_must_pass`/`rule_only`/`llm_only`)로
  조정합니다. LLM 판정이 수행되지 않은 경우(`OPENAI_API_KEY` 미설정) 룰 판정만으로 결정되어,
  LLM 장애가 전체 실행을 막지 않습니다.
- **Techniques** (실행 시 선택): `rag`, `llm_quality`, `rubric`, `regression`,
  `functional`(실행 단위 커넥터 계약 검사 — 빈 질문/과도한 길이/특수문자 입력),
  `dual_compare`(저장된 `existing_answer`와 이번 실행의 실시간 응답을 같은 케이스에 대해 비교).
- **Reporter** (`qa_agent/reporter.py`): `run_{id}.json/.csv`, `latest.json`,
  `final_quality_report.md`를 생성합니다 — 마크다운 리포트의 결함 목록/개선 제안/종합 의견은
  고정 텍스트가 아니라 실제 리포트 데이터로부터 계산됩니다.
- **JiraNotifier**: 카테고리별 실패율이 `category_fail_rate_threshold`(기본 20%)를 넘으면
  카테고리당 1건의 티켓을 생성하며, `run:{id}`/`category:{name}` 라벨로 중복 생성을 방지합니다.

## 웹 앱 (`app/main.py`)

`POST /api/run`은 백그라운드 스레드로 실행을 시작하고 즉시 `run_id`를 반환합니다.
`GET /api/run/{run_id}/status`로 `{status, progress}`를 폴링하고,
`GET /api/run/{run_id}/result`로 완료된 리포트를 조회합니다. 그 외 엔드포인트:
데이터셋 업로드/현재상태/템플릿, `/api/runs`(이력), `/api/runs/{run_id}`(경로조작 방지가
적용된 과거 리포트 조회), `/api/jira/tickets`, `/api/config/connector-defaults`,
그리고 `/health`(`monitoring.HealthChecker` 기반).

## 품질 게이트 / 성능 / 모니터링

- `quality.QualityCheckRunner.run_gate(pass_rate_threshold)` — 최신 리포트 기준
  CI 친화적 pass/fail 판정. 실패 시 0이 아닌 코드로 종료하는 CI 단계에 연결하세요.
- `performance.pipeline_benchmark.benchmark_pipeline(...)` — 케이스 목록에 대해 실제
  `PipelineOrchestrator.run()`의 소요 시간을 측정합니다. 의도적으로 부하/동시성 테스트
  도구가 아니며, 그런 용도로는 k6/locust를 사용하세요.
- `monitoring.HealthChecker` — 리포트 디렉터리 쓰기 가능 여부와 `OPENAI_API_KEY` 설정
  여부를 점검합니다.

## 모니터링 애드온 (`monitoring_addon/`, 선택 기능)

기존 플랫폼(위 내용 전부)을 수정하지 않고 별도로 얹은 확장 모듈입니다 - k6 성능테스트
결과를 SQLite에 저장/조회하고, 기존 `MetricsCollector` 요약값을 1분 주기로 읽기 전용
스냅샷 저장하며, `/metrics-addon`으로 Prometheus/Grafana 연동을 제공합니다. 별도 페이지
(`/monitoring-addon`)로 노출되며, `.env`의 `MONITORING_ADDON_ENABLED=false`로 언제든
통째로 끌 수 있습니다. 실행/롤백 방법은 최상위 [README.md](../README.md), 사용자 관점
설명은 [사용자_매뉴얼.md](사용자_매뉴얼.md) 11장을 참고하세요.

## 윈도우 데스크톱 앱 (`desktop_app/`, 선택 기능)

브라우저 대신 네이티브 윈도우 창으로 같은 화면을 쓰고 싶을 때를 위한 실행 파일입니다.
서버/API/화면은 전혀 건드리지 않고, 이미 떠있는 서버에 pywebview로 접속만 하는 래퍼라서
브라우저와 항상 동일한 데이터를 봅니다. 사용법은
[사용자_매뉴얼.md](사용자_매뉴얼.md) 4.1장을 참고하세요.

## 범위 외 (의도적 제외)

응답속도/동시사용자 부하 테스트, 프롬프트 A/B 테스트 자동화, 데이터셋 드리프트 모니터링은
이 파이프라인의 의도적인 범위 외 사항입니다 — 전용 도구를 사용하세요.
