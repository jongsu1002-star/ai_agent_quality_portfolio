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

const BASE_URL = __ENV.LOAD_TARGET_URL || 'http://127.0.0.1:8000';
const SCENARIO_NAME = 'basic-load-test';
const VUS = Number(__ENV.LOAD_VUS || 10);
const DURATION = __ENV.LOAD_DURATION || '30s';
// 기본값 /health는 이 QA 플랫폼 자신을 테스트할 때만 유효한 경로 - 다른 내부 서비스(예: 사내
// 챗봇 서버)를 테스트하려면 그 서비스가 실제로 갖고 있는 경로를 지정해야 함(없으면 전부 404).
const LOAD_PATH = __ENV.LOAD_PATH || '/';
// LOAD_UTTERANCE가 설정되면 GET 대신 POST로 전환해서 실제 챗봇 질의응답 엔드포인트를
// 부하테스트할 수 있게 함(예: {"message": "발화문"}) - 비어있으면 기존처럼 단순 GET 체크만 수행.
const LOAD_UTTERANCE = __ENV.LOAD_UTTERANCE || '';
const LOAD_REQUEST_FIELD = __ENV.LOAD_REQUEST_FIELD || 'message';

export const options = {
  scenarios: {
    [SCENARIO_NAME]: {
      executor: 'constant-vus',
      vus: VUS,
      duration: DURATION,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<1000'],
  },
  // k6 기본 summaryTrendStats는 avg/min/med/max/p(90)/p(95)까지만 포함 - p(99)는 명시해야 나옴
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

export default function () {
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

  return {
    run_id: formatRunId(new Date()),
    tool: 'k6',
    target_url: BASE_URL,
    target_path: LOAD_PATH,
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
