// k6 부하 테스트 - QA 플랫폼 자체(운영 서비스)에 대한 성능 검증용.
// 실행: k6 run tests/k6/load_test.js  (대상 서버가 먼저 떠 있어야 함, 기본 http://127.0.0.1:8000)
// 실행 전 reports/k6/history/ 디렉터리가 미리 존재해야 함 - k6 샌드박스는 handleSummary()가
// 반환한 경로에 파일을 쓸 때 중간 디렉터리를 자동으로 만들어주지 않음(파일 쓰기 자체도
// fs API 없이 이 방식으로만 가능).
//
// 커스터마이즈용 환경변수는 일부러 K6_ 접두어를 피함 - K6_VUS/K6_DURATION은 k6 자체가
// 예약해서 쓰는 이름이라(설정하면 이 스크립트의 options.scenarios를 통째로 무시하고
// 자기 기본 실행 방식으로 덮어씀), 우리 것은 LOAD_*로 구분함.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const BASE_URL = __ENV.LOAD_TARGET_URL || 'http://127.0.0.1:8000';
const VUS = Number(__ENV.LOAD_VUS || 10);
const DURATION = __ENV.LOAD_DURATION || '30s';
// 기본값 /health는 이 QA 플랫폼 자신을 테스트할 때만 유효한 경로 - 다른 내부 서비스(예: 사내
// 챗봇 서버)를 테스트하려면 그 서비스가 실제로 갖고 있는 경로를 지정해야 함(없으면 전부 404).
const LOAD_PATH = __ENV.LOAD_PATH || '/';
// LOAD_UTTERANCE가 설정되면 GET 대신 POST로 전환해서 실제 챗봇 질의응답 엔드포인트를
// 부하테스트할 수 있게 함(예: {"message": "발화문"}) - 비어있으면 기존처럼 단순 GET 체크만 수행.
const LOAD_UTTERANCE = __ENV.LOAD_UTTERANCE || '';
const LOAD_REQUEST_FIELD = __ENV.LOAD_REQUEST_FIELD || 'message';

// VOC 분석 전용 모드 - 일반 모드는 단일 요청/응답이라 이 플랫폼에서 가장 무거운 엔드포인트
// (실제 LLM을 여러 번 순차 호출하는 VOC 분석, 통상 10~40초)를 제대로 부하테스트할 수 없었음
// (실행할 때마다 진짜 LLM 비용이 드는 엔드포인트라 VUS/반복 횟수를 일부러 작게 잡는 것을
// 권장 - LOAD_VOC_ITERATIONS 기본값 1). POST /api/voc-analysis/run-async로 시작해 실제
// 완료(done/error/canceled)까지 폴링하고, 완료까지 걸린 시간을 별도 트렌드로 남긴다.
const LOAD_VOC_MODE = __ENV.LOAD_VOC_MODE === '1';
const LOAD_VOC_ITERATIONS = Number(__ENV.LOAD_VOC_ITERATIONS || 1);
const LOAD_VOC_FOCUS = __ENV.LOAD_VOC_FOCUS || '';
const LOAD_VOC_POLL_INTERVAL_S = Number(__ENV.LOAD_VOC_POLL_INTERVAL_S || 2);
const LOAD_VOC_MAX_POLLS = Number(__ENV.LOAD_VOC_MAX_POLLS || 60); // 2초 * 60 = 최대 120초 대기

const vocCompletionTime = new Trend('voc_analysis_completion_seconds', true);
const vocSuccessRate = new Rate('voc_analysis_success_rate');

const SCENARIO_NAME = LOAD_VOC_MODE ? 'voc-analysis-load-test' : 'basic-load-test';

export const options = {
  scenarios: LOAD_VOC_MODE
    ? {
        [SCENARIO_NAME]: {
          executor: 'per-vu-iterations',
          vus: VUS,
          iterations: LOAD_VOC_ITERATIONS,
          maxDuration: `${LOAD_VOC_MAX_POLLS * LOAD_VOC_POLL_INTERVAL_S + 30}s`,
        },
      }
    : {
        [SCENARIO_NAME]: {
          executor: 'constant-vus',
          vus: VUS,
          duration: DURATION,
        },
      },
  thresholds: LOAD_VOC_MODE
    ? {
        voc_analysis_success_rate: ['rate>0.95'],
      }
    : {
        http_req_failed: ['rate<0.05'],
        http_req_duration: ['p(95)<1000'],
      },
  // k6 기본 summaryTrendStats는 avg/min/med/max/p(90)/p(95)까지만 포함 - p(99)는 명시해야 나옴
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

function runVocAnalysisOnce() {
  const startedAt = Date.now();
  const payload = { use_board: true, use_jira: false, use_excel: false };
  if (LOAD_VOC_FOCUS) payload.focus_instruction = LOAD_VOC_FOCUS;
  const start = http.post(`${BASE_URL}/api/voc-analysis/run-async`, JSON.stringify(payload), {
    headers: { 'Content-Type': 'application/json' },
  });
  const started = check(start, { 'run-async accepted (200)': (r) => r.status === 200 });
  if (!started) {
    vocSuccessRate.add(false);
    return;
  }
  const runId = JSON.parse(start.body).run_id;

  for (let attempt = 0; attempt < LOAD_VOC_MAX_POLLS; attempt++) {
    sleep(LOAD_VOC_POLL_INTERVAL_S);
    const statusRes = http.get(`${BASE_URL}/api/voc-analysis/run-async/${runId}/status`);
    const status = JSON.parse(statusRes.body).status;
    if (status === 'done') {
      vocCompletionTime.add((Date.now() - startedAt) / 1000);
      vocSuccessRate.add(true);
      return;
    }
    if (status === 'error' || status === 'canceled') {
      vocCompletionTime.add((Date.now() - startedAt) / 1000);
      vocSuccessRate.add(false);
      return;
    }
  }
  // 최대 폴링 횟수 안에 끝나지 않음(타임아웃) - 실패로 집계
  vocSuccessRate.add(false);
}

export default function () {
  if (LOAD_VOC_MODE) {
    runVocAnalysisOnce();
    return;
  }
  const url = `${BASE_URL}${LOAD_PATH}`;
  const res = LOAD_UTTERANCE
    ? http.post(url, JSON.stringify({ [LOAD_REQUEST_FIELD]: LOAD_UTTERANCE }), {
        headers: { 'Content-Type': 'application/json' },
      })
    : http.get(url);
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
  sleep(1);
}

function pad(n) {
  return String(n).padStart(2, '0');
}

function formatRunId(date) {
  return (
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  );
}

function round1(n) {
  return typeof n === 'number' ? Math.round(n * 10) / 10 : null;
}

function buildThresholds(data) {
  // metric.thresholds는 "조건 문자열"을 키로 갖는 객체({"rate<0.05": {ok: true}}) -
  // 비어있는 {}도 truthy라서 존재 여부가 아니라 Object.entries 결과로 판단해야 함
  const results = [];
  for (const [metricName, metric] of Object.entries(data.metrics)) {
    for (const [condition, outcome] of Object.entries(metric.thresholds || {})) {
      results.push({ name: metricName, condition, passed: !!outcome.ok });
    }
  }
  return results;
}

function buildSummary(data) {
  const dur = data.metrics.http_req_duration.values;
  const thresholds = buildThresholds(data);
  const thresholdsPassed = thresholds.every((t) => t.passed);

  const summary = {
    run_id: formatRunId(new Date()),
    tool: 'k6',
    target_url: BASE_URL,
    target_path: LOAD_VOC_MODE ? '/api/voc-analysis/run-async' : LOAD_PATH,
    utterance: LOAD_UTTERANCE || null,
    request_field: LOAD_UTTERANCE ? LOAD_REQUEST_FIELD : null,
    scenario: SCENARIO_NAME,
    vus: VUS,
    total_requests: data.metrics.http_reqs.values.count,
    failed_rate: data.metrics.http_req_failed.values.rate,
    checks_rate: data.metrics.checks ? data.metrics.checks.values.rate : null,
    http_req_duration: {
      avg_ms: round1(dur.avg),
      min_ms: round1(dur.min),
      med_ms: round1(dur.med),
      max_ms: round1(dur.max),
      p90_ms: round1(dur['p(90)']),
      p95_ms: round1(dur['p(95)']),
      p99_ms: round1(dur['p(99)']),
    },
    thresholds,
    thresholds_passed: thresholdsPassed,
    result: thresholdsPassed ? 'Pass' : 'Fail',
  };

  // VOC 모드에서는 개별 HTTP 요청 시간(폴링 GET 포함)보다 "분석 1건이 실제로 끝나는 데
  // 걸린 시간"이 의미 있는 지표라 별도 섹션으로 분리해 담는다(voc_analysis_completion_seconds
  // Trend/voc_analysis_success_rate Rate, 스크립트 상단 참고).
  if (LOAD_VOC_MODE) {
    const completion = data.metrics.voc_analysis_completion_seconds
      ? data.metrics.voc_analysis_completion_seconds.values
      : null;
    summary.voc_analysis = {
      iterations_per_vu: LOAD_VOC_ITERATIONS,
      total_runs: data.metrics.iterations ? data.metrics.iterations.values.count : null,
      success_rate: data.metrics.voc_analysis_success_rate ? data.metrics.voc_analysis_success_rate.values.rate : null,
      completion_seconds: completion && {
        avg: round1(completion.avg),
        min: round1(completion.min),
        max: round1(completion.max),
        p95: round1(completion['p(95)']),
      },
    };
  }

  return summary;
}

export function handleSummary(data) {
  const summary = buildSummary(data);
  const json = JSON.stringify(summary, null, 2);
  return {
    'reports/k6/latest.json': json,
    [`reports/k6/history/${summary.run_id}.json`]: json,
    stdout: `[k6] ${summary.run_id} result=${summary.result} total_requests=${summary.total_requests} p95_ms=${summary.http_req_duration.p95_ms}\n`,
  };
}
