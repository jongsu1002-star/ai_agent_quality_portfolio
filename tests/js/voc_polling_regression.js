// VOC 폴링(런/취소/새로고침 복구) 최소 회귀 테스트 - 실제 브라우저 없이 Node vm으로
// app/templates/index.html에서 해당 함수 블록만 그대로 잘라 실행한다(사본을 따로 두지
// 않음 - 원본이 바뀌면 이 테스트도 그 바뀐 코드를 그대로 검증하게 됨). 이 프로젝트에는
// 아직 Playwright 등 실제 브라우저 E2E 인프라(package.json 등)가 없어 이번엔 새로
// 만들지 않고, 최소 요구사항인 "JS 함수 + HTML 배선" 회귀 테스트로 대응한다.
//
// 실행: node tests/js/voc_polling_regression.js (tests/test_frontend_js_regression.py가
// pytest에서 이 스크립트를 subprocess로 호출해 결과를 픽업함 - node가 없는 환경에서는
// 그 pytest가 스스로 skip 처리).
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const INDEX_HTML_PATH = path.join(__dirname, '..', '..', 'app', 'templates', 'index.html');
const html = fs.readFileSync(INDEX_HTML_PATH, 'utf-8');

const START_MARK = 'let _vocActiveRunId = null;';
const END_MARK = 'function _renderVocResult(record) {';
const startIdx = html.indexOf(START_MARK);
const endIdx = html.indexOf(END_MARK);
if (startIdx === -1 || endIdx === -1) {
  console.error('FAIL: index.html에서 VOC 폴링 코드 블록 경계 마커를 찾지 못했습니다 - ' +
    '리팩터링으로 함수 이름/위치가 바뀌었으면 이 스크립트의 START_MARK/END_MARK를 갱신하세요.');
  process.exit(1);
}
// Node vm의 널리 알려진 함정: 최상위 let/const 선언은 vm 컨텍스트 객체의 "속성"으로
// 노출되지 않는다(var/함수 선언만 노출됨) - 그래서 테스트가 밖에서 context._vocActiveRunId
// 를 읽고 쓰려면 원본 소스의 mutable state 선언(let)만 var로 바꿔서 컴파일해야 한다.
// 함수 본문 로직 자체는 원본 그대로이므로 이 치환이 검증 대상 동작을 바꾸지 않는다.
const vocPollingSource = html.slice(startIdx, endIdx)
  .replace('let _vocActiveRunId = null;', 'var _vocActiveRunId = null;')
  .replace('let _vocPollTimer = null;', 'var _vocPollTimer = null;')
  .replace('let _vocPollFailureCount = 0;', 'var _vocPollFailureCount = 0;');

// check()는 fn을 즉시 실행하지 않고 큐에 쌓아둔다 - 대부분의 fn이 async라 top-level에서
// 바로 실행하면 결과를 기다리지 않고 다음 check로 넘어가 버리는 문제가 있어, 아래
// runAllChecks()가 순서대로 await하며 하나씩 실행한다.
const pendingChecks = [];
let passed = 0;
let failed = 0;
const failures = [];

function check(name, fn) {
  pendingChecks.push({ name, fn });
}

async function runAllChecks() {
  for (const { name, fn } of pendingChecks) {
    try {
      await fn();
      passed += 1;
      console.log(`  ok - ${name}`);
    } catch (err) {
      failed += 1;
      failures.push({ name, err });
      console.log(`  FAIL - ${name}: ${err.stack || err.message}`);
    }
  }
}

function makeElement() {
  return { textContent: '', innerHTML: '', value: '', checked: false, disabled: false, style: {} };
}

// 매 체크마다 완전히 새로운 sandbox를 만들어 이전 체크의 타이머/상태가 섞이지 않게 한다.
function buildSandbox({ elementOverrides = {}, fetchImpl, demoEnabled = false } = {}) {
  const elements = {};
  const ids = [
    'voc-run-btn', 'voc-cancel-btn', 'voc-result',
    'voc-use-jira', 'voc-use-excel', 'voc-use-board',
    'voc-jira-jql', 'voc-focus-instruction', 'voc-item-limit',
  ];
  ids.forEach((id) => { elements[id] = Object.assign(makeElement(), elementOverrides[id] || {}); });

  const storage = {};
  const pendingTimers = []; // { id, fn, ms }
  let nextTimerId = 1;
  const calls = { showToast: [], processStates: [], renderVocResult: [], loadVocHistory: 0, escapeHtmlCalls: 0, stageLists: [] };

  const sandbox = {
    console,
    window: {
      QADemoMode: {
        enabled: demoEnabled,
        reportProcess: (message, state) => { calls.processStates.push({ message, state }); },
      },
    },
    document: { getElementById: (id) => elements[id] || makeElement() },
    sessionStorage: {
      getItem: (k) => (Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null),
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    },
    fetch: (...args) => (fetchImpl ? fetchImpl(...args) : Promise.reject(new Error('fetch not stubbed'))),
    setTimeout: (fn, ms) => { const id = nextTimerId++; pendingTimers.push({ id, fn, ms }); return id; },
    clearTimeout: (id) => { const i = pendingTimers.findIndex((t) => t.id === id); if (i !== -1) pendingTimers.splice(i, 1); },
    alert: () => {}, confirm: () => true,
    escapeHtml: (s) => { calls.escapeHtmlCalls += 1; return String(s); },
    // renderStepChecklist는 START_MARK 이전(escapeHtml 근처)에 정의돼 있어 이 슬라이스
    // 밖이다 - 이 파일의 체크들은 단계 이름 자체가 아니라 폴링/재시도/버튼 상태를
    // 검증하므로, 스타일 없이 아무 문자열이나 반환하는 최소 스텁으로 충분하다.
    renderStepChecklist: (steps, activeIndex) => {
      calls.stageLists.push({ steps: Array.from(steps), activeIndex });
      return `<div data-stage-index="${activeIndex}"></div>`;
    },
    showToast: (msg, type) => { calls.showToast.push({ msg, type }); },
    _renderVocResult: (record) => { calls.renderVocResult.push(record); },
    loadVocHistory: () => { calls.loadVocHistory += 1; },
    _vocExcelPath: null,
    Date, JSON, Math, Object, Array, Number, String, Boolean, Promise, Error,
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(vocPollingSource, context, { filename: 'voc_polling_slice.js' });

  // pending setTimeout들을 "실제 시간 흐름 없이" 순서대로 즉시 실행하는 헬퍼(재귀적으로
  // 다음 폴링이 또 setTimeout을 걸면 그것도 이어서 처리) - 백오프 시간 자체를 검증하고
  // 싶을 때는 pendingTimers를 직접 들여다본다.
  async function flushTimers(maxSteps = 20) {
    for (let i = 0; i < maxSteps && pendingTimers.length > 0; i++) {
      const timer = pendingTimers.shift();
      await timer.fn();
    }
  }

  return { context, elements, storage, pendingTimers, calls, flushTimers };
}

check('데모 모드 VOC Improved는 외부 API 없이 5단계와 SKIPPED 결과를 표시함', async () => {
  let fetchCount = 0;
  const { context, elements, calls, flushTimers } = buildSandbox({
    demoEnabled: true,
    fetchImpl: async () => { fetchCount += 1; throw new Error('데모 모드는 외부 API를 호출하면 안 됨'); },
  });
  await context.runVocAnalysis();
  await flushTimers(10);
  assert.strictEqual(fetchCount, 0);
  assert.deepStrictEqual(Array.from(calls.stageLists[0].steps), [
    '의도 분류 중', '개선안 생성 중', '자가 비평·교정 중', '내부 재점검 중', '독립 Judge 확인 중',
  ]);
  assert.strictEqual(calls.renderVocResult[0].result.judge.verdict, 'SKIPPED');
  assert.strictEqual(calls.processStates.length, 5);
  assert.deepStrictEqual(calls.processStates.at(-1), { message: 'VOC Improved · 독립 Judge 확인 중', state: 'running' });
  assert.match(elements['voc-result'].innerHTML, /촬영용 합성 실행/);
  assert.match(elements['voc-result'].innerHTML, /LLM Judge: SKIPPED/);
});

// ── 1. 상태 API 500 처리: 백오프로 재시도하다가 버튼이 복구되는지 ──────────────
check('상태 API가 500을 반환하면 지수 백오프로 재시도하다 최대 실패 횟수에서 버튼 복구', async () => {
  let callCount = 0;
  const { context, elements, pendingTimers, flushTimers } = buildSandbox({
    fetchImpl: async (url) => {
      callCount += 1;
      return { ok: false, status: 500, json: async () => ({}) };
    },
  });
  context._vocActiveRunId = 'run-1';
  await context._pollVocRun('run-1');
  await flushTimers(10);
  assert.ok(callCount >= 5, `500 응답에 최소 5회는 재시도해야 함(실제 ${callCount}회)`);
  assert.strictEqual(elements['voc-run-btn'].disabled, false, '최대 실패 후 실행 버튼이 다시 활성화돼야 함');
  assert.strictEqual(context._vocActiveRunId, null, '최대 실패 후 활성 run id가 초기화돼야 함');
  assert.strictEqual(pendingTimers.length, 0, '더 이상 재시도 타이머가 남아있으면 안 됨');
});

// ── 2. 상태 API 404 처리: 즉시 중단 + 안내 문구 + 버튼 복구 ────────────────────
check('상태 API가 404를 반환하면 재시도 없이 즉시 중단하고 만료 안내를 표시', async () => {
  let callCount = 0;
  const { context, elements } = buildSandbox({
    fetchImpl: async () => { callCount += 1; return { ok: false, status: 404, json: async () => ({ error: 'not found' }) }; },
  });
  context._vocActiveRunId = 'run-2';
  await context._pollVocRun('run-2');
  assert.strictEqual(callCount, 1, '404는 재시도하지 않고 1회만 호출해야 함');
  assert.ok(elements['voc-result'].innerHTML.includes('만료'), '만료/재시작 안내 문구가 표시돼야 함');
  assert.strictEqual(elements['voc-run-btn'].disabled, false, '404 이후 실행 버튼이 복구돼야 함');
  assert.strictEqual(context._vocActiveRunId, null, '404 이후 활성 run id가 초기화돼야 함');
});

// ── 3. 네트워크 예외 후 재시도 ────────────────────────────────────────────────
check('fetch가 예외를 던지면(네트워크 오류) 백오프로 재시도함', async () => {
  let callCount = 0;
  const { context, pendingTimers, flushTimers } = buildSandbox({
    fetchImpl: async () => {
      callCount += 1;
      if (callCount < 3) { throw new TypeError('Failed to fetch'); }
      return { ok: true, status: 200, json: async () => ({ status: 'running' }) };
    },
  });
  context._vocActiveRunId = 'run-3';
  await context._pollVocRun('run-3');
  await flushTimers(5);
  assert.ok(callCount >= 3, `네트워크 예외 후 재시도해서 결국 성공 응답까지 도달해야 함(실제 ${callCount}회 호출)`);
});

// ── 4. 최대 연속 실패 도달 후 버튼 복구(2번과 별개로 카운터 자체를 명시적으로 확인) ──
check('연속 실패가 최대치를 넘으면 폴링을 종료하고 버튼을 복구', async () => {
  const { context, elements, pendingTimers, flushTimers } = buildSandbox({
    fetchImpl: async () => ({ ok: false, status: 503, json: async () => ({}) }),
  });
  context._vocActiveRunId = 'run-4';
  await context._pollVocRun('run-4');
  await flushTimers(10);
  assert.strictEqual(pendingTimers.length, 0, '재시도 타이머가 더 이상 없어야 함(폴링 종료)');
  assert.strictEqual(elements['voc-cancel-btn'].style.display, 'none', '취소 버튼도 숨겨져야 함');
});

// ── 5. 페이지 재로드 시 폴링 재개(sessionStorage로부터) ────────────────────────
check('_resumeVocRunIfAny가 sessionStorage에 저장된 run id로 폴링을 재개함', async () => {
  let requestedUrl = null;
  const { context, elements, storage } = buildSandbox({
    fetchImpl: async (url) => { requestedUrl = url; return { ok: true, status: 200, json: async () => ({ status: 'running' }) }; },
  });
  storage['voc_active_run'] = JSON.stringify({ runId: 'resumed-run-1', startedAt: Date.now() - 5000 });
  context._resumeVocRunIfAny();
  await new Promise((resolve) => setImmediate(resolve)); // 내부 async _pollVocRun이 첫 await까지 진행되게 함
  assert.strictEqual(context._vocActiveRunId, 'resumed-run-1', '저장된 run id가 활성 run으로 복구돼야 함');
  assert.strictEqual(elements['voc-run-btn'].disabled, true, '재개 시 실행 버튼이 비활성화돼야 함');
  assert.ok(requestedUrl && requestedUrl.includes('resumed-run-1'), '저장된 run id로 상태 API를 호출해야 함');
});

check('_resumeVocRunIfAny는 저장된 실행이 없으면 아무 일도 하지 않음', () => {
  const { context, elements } = buildSandbox({ fetchImpl: async () => { throw new Error('호출되면 안 됨'); } });
  context._resumeVocRunIfAny();
  assert.strictEqual(context._vocActiveRunId, null);
  assert.strictEqual(elements['voc-run-btn'].disabled, false);
});

// ── 6. 완료/실패/취소/404 이후 sessionStorage 정리 ─────────────────────────────
check('분석이 done으로 완료되면 sessionStorage의 활성 run 기록이 삭제됨', async () => {
  const { context, storage } = buildSandbox({
    fetchImpl: async (url) => {
      if (String(url).endsWith('/status')) return { ok: true, status: 200, json: async () => ({ status: 'done' }) };
      return { ok: true, status: 200, json: async () => ({ id: 'voc_1', result: { summary: 's', judge: {} } }) };
    },
  });
  storage['voc_active_run'] = JSON.stringify({ runId: 'run-done', startedAt: Date.now() });
  context._vocActiveRunId = 'run-done';
  await context._pollVocRun('run-done');
  assert.strictEqual(storage['voc_active_run'], undefined, 'done 처리 후 sessionStorage 항목이 지워져야 함');
});

check('404(만료) 처리 후에도 sessionStorage의 활성 run 기록이 삭제됨', async () => {
  const { context, storage } = buildSandbox({
    fetchImpl: async () => ({ ok: false, status: 404, json: async () => ({}) }),
  });
  storage['voc_active_run'] = JSON.stringify({ runId: 'run-404', startedAt: Date.now() });
  context._vocActiveRunId = 'run-404';
  await context._pollVocRun('run-404');
  assert.strictEqual(storage['voc_active_run'], undefined, '404 처리 후 sessionStorage 항목이 지워져야 함');
});

// ── 7. 이전 run ID의 늦은 응답이 새 실행 화면을 덮어쓰지 않음 ──────────────────
check('활성 run id와 다른 run id의 폴링 응답은 화면을 건드리지 않고 무시됨', async () => {
  const { context, elements } = buildSandbox({
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ status: 'error', error: '오래된 실행의 에러' }) }),
  });
  elements['voc-result'].innerHTML = '<p>새 실행 진행 중</p>';
  context._vocActiveRunId = 'run-new';  // 이미 다른(새) 실행이 활성 상태
  await context._pollVocRun('run-old'); // 늦게 도착한 이전 실행의 폴링
  assert.strictEqual(elements['voc-result'].innerHTML, '<p>새 실행 진행 중</p>', '이전 run의 응답이 현재 화면을 덮어쓰면 안 됨');
  assert.strictEqual(context._vocActiveRunId, 'run-new', '활성 run id도 그대로 유지돼야 함');
});

// ── 8. 취소 API 실패가 사용자에게 표시됨 ────────────────────────────────────────
check('cancelVocAnalysis가 실패 응답을 받으면 showToast로 에러를 표시', async () => {
  const { context, calls } = buildSandbox({
    fetchImpl: async () => ({ ok: false, status: 500, json: async () => ({ error: '취소 실패 원인' }) }),
  });
  context._vocActiveRunId = 'run-cancel';
  await context.cancelVocAnalysis();
  assert.ok(calls.showToast.some((c) => c.type === 'error'), '취소 실패는 error 타입 토스트로 표시돼야 함');
});

check('cancelVocAnalysis가 네트워크 예외를 던지면 showToast로 에러를 표시', async () => {
  const { context, calls } = buildSandbox({ fetchImpl: async () => { throw new TypeError('Failed to fetch'); } });
  context._vocActiveRunId = 'run-cancel-2';
  await context.cancelVocAnalysis();
  assert.ok(calls.showToast.some((c) => c.type === 'error'), '네트워크 예외도 error 토스트로 표시돼야 함');
});

runAllChecks().then(() => {
  console.log(`\n${passed} passed, ${failed} failed (voc_polling_regression.js)`);
  process.exit(failed > 0 ? 1 : 0);
}).catch((err) => {
  console.error('테스트 러너 자체가 실패했습니다:', err);
  process.exit(1);
});
