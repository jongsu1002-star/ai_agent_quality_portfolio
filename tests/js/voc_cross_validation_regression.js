// VOC 교차검증 매트릭스(A~D 조합 선택 + 실행 전용 API 키 입력 + 비동기 단계 폴링) 최소
// 회귀 테스트 - voc_polling_regression.js와 동일한 방식(Node vm으로 index.html에서 해당
// 함수 블록만 그대로 잘라 실행, 사본을 따로 두지 않음)으로 실제 동작을 검증한다. 기존
// 프론트 배선 테스트(test_voc_cross_validation_group_selection_and_api_key_override_wired)는
// HTML에 특정 문자열이 있는지만 확인할 뿐, 체크박스 선택/키 입력/전송 페이로드/실행 후
// 정리/단계별 진행 표시 같은 실제 동작은 검증하지 않는다는 한계가 있어 이 파일로 그 공백을
// 메운다.
//
// runVocCrossValidation()은 이제 POST .../run-async로 시작만 하고 _pollVocXvalRun(...)을
// await 없이 fire-and-forget으로 호출한다(runVocAnalysis/_pollVocRun과 동일한 기존 관용구).
// 그래서 "시작 직후" 상태(입력값 검증, 요청 페이로드, 키 입력란 정리)는
// runVocCrossValidation()을 통해 검증하고, "폴링 완료 후" 상태(결과 렌더링, 이력 갱신,
// 토스트)는 voc_polling_regression.js가 _pollVocRun을 직접 부르는 것과 동일하게
// _pollVocXvalRun을 직접 호출해 검증한다(entry 함수를 await해도 fire-and-forget 호출까지
// 기다려주지 않기 때문).
//
// 실행: node tests/js/voc_cross_validation_regression.js (tests/test_frontend_js_regression.py가
// pytest에서 이 스크립트를 subprocess로 호출 - node가 없는 환경에서는 그 pytest가 스스로 skip).
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const INDEX_HTML_PATH = path.join(__dirname, '..', '..', 'app', 'templates', 'index.html');
const html = fs.readFileSync(INDEX_HTML_PATH, 'utf-8');

// runVocCrossValidation/_pollVocXvalRun/_renderVocCrossValidationResult는
// voc_polling_regression.js가 쓰는 슬라이스(START_MARK~END_MARK) 범위 안에 이미 포함돼
// 있어(같은 <script> 블록 안, 폴링 코드 바로 뒤) 동일한 경계 마커를 그대로 재사용한다 -
// 별도 마커를 새로 만들면 두 파일이 같은 소스를 서로 다른 기준으로 잘라내다 한쪽만
// 갱신되는 드리프트가 생길 수 있음.
const START_MARK = 'let _vocActiveRunId = null;';
const END_MARK = 'function _renderVocResult(record) {';
const startIdx = html.indexOf(START_MARK);
const endIdx = html.indexOf(END_MARK);
if (startIdx === -1 || endIdx === -1) {
  console.error('FAIL: index.html에서 VOC 코드 블록 경계 마커를 찾지 못했습니다 - ' +
    '리팩터링으로 함수 이름/위치가 바뀌었으면 이 스크립트의 START_MARK/END_MARK를 갱신하세요.');
  process.exit(1);
}
const source = html.slice(startIdx, endIdx)
  .replace('let _vocActiveRunId = null;', 'var _vocActiveRunId = null;')
  .replace('let _vocPollTimer = null;', 'var _vocPollTimer = null;')
  .replace('let _vocPollFailureCount = 0;', 'var _vocPollFailureCount = 0;')
  .replace('let _vocXvalActiveRunId = null;', 'var _vocXvalActiveRunId = null;')
  .replace('let _vocXvalPollTimer = null;', 'var _vocXvalPollTimer = null;');

const pendingChecks = [];
let passed = 0;
let failed = 0;

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
      console.log(`  FAIL - ${name}: ${err.stack || err.message}`);
    }
  }
}

function makeElement(overrides = {}) {
  return Object.assign({ textContent: '', innerHTML: '', value: '', checked: false, disabled: false, style: {} }, overrides);
}

// checkboxStates: [{ value: 'A', checked: true }, ...] - 기본은 A~D 전부 선택된 상태
// (실제 화면의 기본값과 동일, index.html의 checkbox 마크업에 checked 속성이 이미 있음).
function buildSandbox({ checkboxStates, elementOverrides = {}, fetchImpl } = {}) {
  const ids = [
    'voc-xval-btn', 'voc-xval-result', 'voc-xval-openai-key', 'voc-xval-anthropic-key',
    'voc-use-board', 'voc-use-jira', 'voc-jira-jql', 'voc-use-excel',
    'voc-focus-instruction', 'voc-item-limit',
  ];
  const elements = {};
  ids.forEach((id) => { elements[id] = makeElement(elementOverrides[id] || {}); });

  const boxes = checkboxStates || ['A', 'B', 'C', 'D'].map((value) => ({ value, checked: true }));
  const calls = { alerts: [], showToast: [], loadVocCrossValidationHistory: 0, fetchBodies: [], fetchUrls: [], renderJudgeBadgeCalls: [] };

  const sandbox = {
    console,
    document: {
      getElementById: (id) => elements[id] || makeElement(),
      querySelectorAll: (selector) => {
        if (selector === '.voc-xval-group-checkbox:checked') return boxes.filter((b) => b.checked);
        if (selector === '.voc-xval-group-checkbox') return boxes;
        return [];
      },
    },
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    fetch: (url, opts) => {
      calls.fetchUrls.push(String(url));
      if (opts && opts.body) calls.fetchBodies.push(JSON.parse(opts.body));
      return fetchImpl ? fetchImpl(url, opts) : Promise.reject(new Error('fetch not stubbed'));
    },
    // 실제 폴링 간격(1200ms)을 기다리지 않고 즉시 다음 틱을 실행 - 무한 재귀를 피하려면
    // fetchImpl이 'running'을 무한정 반환하지 않도록(테스트마다 최종 상태로 수렴하도록) 설계할 것.
    setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {},
    alert: (msg) => { calls.alerts.push(msg); },
    confirm: () => true,
    escapeHtml: (s) => String(s),
    showToast: (msg, type) => { calls.showToast.push({ msg, type }); },
    loadVocCrossValidationHistory: () => { calls.loadVocCrossValidationHistory += 1; },
    // _renderJudgeBadge는 _renderVocCrossValidationResult가 호출하지만, 정의 자체는 이
    // 슬라이스(START_MARK~END_MARK) 밖(_renderVocResult 뒤)에 있어 여기 없음 - 실제
    // 브라우저에서는 같은 <script> 안이라 함수 선언 호이스팅으로 정상 동작하지만, 이
    // 슬라이스 테스트에서는 voc_polling_regression.js가 _renderVocResult를 스텁 처리하는
    // 것과 동일한 이유로 스텁이 필요하다. 반환값(HTML)까지 검증하는 게 아니라 "올바른
    // judge 객체로 호출됐는가"(= 근거 데이터가 실제로 전달되는가)만 기록한다.
    _renderJudgeBadge: (judge) => { calls.renderJudgeBadgeCalls.push(judge); return '<div class="judge-badge-stub"></div>'; },
    // renderStepChecklist는 escapeHtml 근처(START_MARK 이전)에 정의돼 있어 이 슬라이스
    // 밖이다 - 실제 브라우저에서는 같은 <script> 안이라 호이스팅으로 정상 동작하지만, 이
    // 테스트에서는 단계 이름이 렌더링된 HTML에 그대로 나타나는지만 확인하면 되므로
    // 스타일/아이콘 없이 이름만 이어붙이는 최소 스텁으로 대체한다.
    renderStepChecklist: (steps) => steps.map((s) => `<li>${s}</li>`).join(''),
    _vocExcelPath: null,
    Date, JSON, Math, Object, Array, Number, String, Boolean, Promise, Error,
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(source, context, { filename: 'voc_cross_validation_slice.js' });
  return { context, elements, calls };
}

function jsonRes(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body };
}

// ── 1. 조합을 하나도 선택하지 않으면 alert만 뜨고 fetch는 아예 발생하지 않음 ──────
check('그룹을 하나도 선택하지 않으면 실행하지 않고 안내창만 표시', async () => {
  const { context, calls } = buildSandbox({
    checkboxStates: ['A', 'B', 'C', 'D'].map((value) => ({ value, checked: false })),
    fetchImpl: async () => { throw new Error('fetch가 호출되면 안 됨'); },
  });
  await context.runVocCrossValidation();
  assert.strictEqual(calls.alerts.length, 1, 'alert가 정확히 1회 떠야 함');
  assert.ok(calls.alerts[0].includes('최소 1개'), '최소 1개 선택 안내 문구여야 함');
  assert.strictEqual(calls.fetchBodies.length, 0, '그룹 미선택이면 fetch 자체를 하면 안 됨');
});

// ── 2. 시작 요청이 올바른 비동기 엔드포인트로, 선택한 그룹만 실어 전송됨 ──────────
check('시작 요청이 run-async 엔드포인트로 가고 선택한 그룹만 정확히 포함됨', async () => {
  const { context, calls } = buildSandbox({
    checkboxStates: [
      { value: 'A', checked: true }, { value: 'B', checked: false },
      { value: 'C', checked: true }, { value: 'D', checked: false },
    ],
    fetchImpl: async () => jsonRes({ run_id: 'x', status: 'queued' }),
  });
  await context.runVocCrossValidation();
  assert.ok(calls.fetchUrls[0].endsWith('/cross-validation-matrix/run-async'), `첫 요청은 시작 엔드포인트여야 함(실제: ${calls.fetchUrls[0]})`);
  assert.deepStrictEqual(calls.fetchBodies[0].groups, ['A', 'C']);
});

// ── 3. API 키 입력값이 trim되어 요청 본문에 실리고, 비어있으면 null ──────────────
check('입력한 API 키가 trim되어 요청 본문에 실리고 빈 값이면 null', async () => {
  const { context, calls } = buildSandbox({
    elementOverrides: {
      'voc-xval-openai-key': { value: '  sk-test-openai  ' },
      'voc-xval-anthropic-key': { value: '' },
    },
    fetchImpl: async () => jsonRes({ run_id: 'x', status: 'queued' }),
  });
  await context.runVocCrossValidation();
  assert.strictEqual(calls.fetchBodies[0].openai_api_key, 'sk-test-openai');
  assert.strictEqual(calls.fetchBodies[0].anthropic_api_key, null);
});

// ── 4. 시작 성공/실패와 무관하게 실행 후 API 키 입력란이 비워짐(보안 위생) ────────
check('시작 요청 성공 후 API 키 입력란이 비워짐', async () => {
  const { context, elements } = buildSandbox({
    elementOverrides: {
      'voc-xval-openai-key': { value: 'sk-test-openai' },
      'voc-xval-anthropic-key': { value: 'sk-test-anthropic' },
    },
    fetchImpl: async () => jsonRes({ run_id: 'x', status: 'queued' }),
  });
  await context.runVocCrossValidation();
  assert.strictEqual(elements['voc-xval-openai-key'].value, '', '성공 후 OpenAI 키 입력란이 비어야 함');
  assert.strictEqual(elements['voc-xval-anthropic-key'].value, '', '성공 후 Anthropic 키 입력란이 비어야 함');
});

check('시작 요청 실패(서버 에러 응답) 후에도 API 키 입력란이 비워짐', async () => {
  const { context, elements } = buildSandbox({
    elementOverrides: {
      'voc-xval-openai-key': { value: 'sk-test-openai' },
      'voc-xval-anthropic-key': { value: 'sk-test-anthropic' },
    },
    fetchImpl: async () => jsonRes({ error: '처리 실패' }, { ok: false, status: 502 }),
  });
  await context.runVocCrossValidation();
  assert.strictEqual(elements['voc-xval-openai-key'].value, '', '실패해도 OpenAI 키 입력란이 비어야 함');
  assert.strictEqual(elements['voc-xval-anthropic-key'].value, '', '실패해도 Anthropic 키 입력란이 비어야 함');
});

check('네트워크 예외가 나도 API 키 입력란이 비워짐', async () => {
  const { context, elements } = buildSandbox({
    elementOverrides: { 'voc-xval-openai-key': { value: 'sk-test-openai' } },
    fetchImpl: async () => { throw new TypeError('Failed to fetch'); },
  });
  await context.runVocCrossValidation();
  assert.strictEqual(elements['voc-xval-openai-key'].value, '', '네트워크 예외 후에도 키 입력란이 비어야 함');
});

// ── 5. 시작 요청이 거부(예: 409 동시실행 충돌)되면 에러 메시지를 즉시 표시 ────────
check('시작 요청이 실패 응답을 받으면 결과 영역에 에러 메시지를 즉시 표시', async () => {
  const { context, elements } = buildSandbox({
    fetchImpl: async () => jsonRes({ error: '이미 실행 중인 작업이 있습니다' }, { ok: false, status: 409 }),
  });
  await context.runVocCrossValidation();
  assert.ok(elements['voc-xval-result'].innerHTML.includes('이미 실행 중인 작업이 있습니다'));
});

// ── 6. 폴링 진행 중(running) 단계 체크리스트가 현재 stage에 맞게 갱신됨 ───────────
// _pollVocXvalRun을 직접 호출 - runVocCrossValidation은 폴링을 await 없이(fire-and-forget)
// 시작하므로, 폴링 완료 이후 상태를 확인하려면 폴링 함수 자체를 직접 불러야 한다
// (voc_polling_regression.js가 _pollVocRun을 직접 테스트하는 것과 동일한 이유).
check('진행 중(running) 상태에서 현재 stage에 해당하는 단계가 활성 표시됨', async () => {
  const { context, elements } = buildSandbox({
    fetchImpl: async (url) => {
      if (String(url).endsWith('/status')) return jsonRes({ status: 'running', stage: 'C 조합 평가 중' });
      throw new Error('running 상태 테스트에서는 상태 조회 외의 fetch가 없어야 함');
    },
  });
  context._vocXvalActiveRunId = 'run-running';
  // setTimeout이 즉시 재귀 호출하면 매번 같은 'running'을 반환해 무한 재귀에 빠지므로,
  // 이 테스트에서만 재귀를 막기 위해 활성 run id를 다르게 바꿔 다음 틱을 조용히 무시시킨다.
  const originalSetTimeout = context.setTimeout;
  let timerFired = false;
  context.setTimeout = (fn) => { if (!timerFired) { timerFired = true; context._vocXvalActiveRunId = 'changed'; } return 1; };
  await context._pollVocXvalRun('run-running', ['OpenAI 생성 중', 'A 조합 평가 중', 'C 조합 평가 중', '저장 중']);
  context.setTimeout = originalSetTimeout;
  assert.ok(elements['voc-xval-result'].innerHTML.includes('C 조합 평가 중'));
});

// ── 7. 완료 시 결과를 불러와 렌더링하고 이력 갱신 + 성공 토스트 ──────────────────
check('완료(done) 시 결과를 불러와 렌더링하고 이력을 갱신하며 성공 토스트를 표시', async () => {
  const matrix = [
    {
      group: 'A', purpose: '기본 품질검증',
      summary: '상담 대기시간과 불친절에 대한 직접적인 언급이 없습니다.',
      judge: { verdict: 'FAIL', criteria: {}, reasoning: '관련 근거가 없어 top_issues(개선안)가 비어 있습니다', cross_model: false, cross_model_configured: true, invalid_example_ids: [] },
      quality_gate: { status: 'REJECTED', usable_for_policy_decision: false },
    },
  ];
  const { context, elements, calls } = buildSandbox({
    fetchImpl: async (url) => {
      if (String(url).endsWith('/status')) return jsonRes({ status: 'done', stage: '저장 중' });
      if (String(url).endsWith('/result')) return jsonRes({ id: 'x', created_at: '2026-07-20T11:00:17', created_by: 'jongsu1002', result: { matrix } });
      throw new Error(`예상치 못한 fetch: ${url}`);
    },
  });
  context._vocXvalActiveRunId = 'run-done';
  await context._pollVocXvalRun('run-done', ['OpenAI 생성 중', 'A 조합 평가 중', '저장 중']);

  assert.strictEqual(calls.loadVocCrossValidationHistory, 1, '완료 시 이력을 다시 불러와야 함');
  assert.ok(calls.showToast.some((c) => c.type === 'success' || c.type === undefined), '성공 토스트가 떠야 함');
  assert.strictEqual(calls.renderJudgeBadgeCalls.length, 1, '매트릭스 항목 수만큼 _renderJudgeBadge가 호출돼야 함');
  assert.strictEqual(calls.renderJudgeBadgeCalls[0].reasoning, matrix[0].judge.reasoning, '판정 근거(reasoning)가 그대로 전달돼야 함');
  assert.ok(elements['voc-xval-result'].innerHTML.includes(matrix[0].summary), '조합별 요약이 화면에 표시돼야 함');
  assert.ok(elements['voc-xval-result'].innerHTML.includes('REJECTED'), '품질 게이트 상태가 화면에 표시돼야 함');
  assert.strictEqual(elements['voc-xval-btn'].disabled, false, '완료 후 버튼이 복구돼야 함');
});

// ── 8. 실패(error) 시 실패한 단계까지 체크리스트에 표시하고 에러 메시지를 보여줌 ──
check('실패(error) 시 실패한 단계를 표시하고 에러 메시지를 보여줌', async () => {
  const { context, elements } = buildSandbox({
    fetchImpl: async (url) => {
      if (String(url).endsWith('/status')) return jsonRes({ status: 'error', stage: 'A 조합 평가 중', error: '이런 이유로 실패' });
      throw new Error(`예상치 못한 fetch: ${url}`);
    },
  });
  context._vocXvalActiveRunId = 'run-error';
  await context._pollVocXvalRun('run-error', ['OpenAI 생성 중', 'A 조합 평가 중', '저장 중']);
  assert.ok(elements['voc-xval-result'].innerHTML.includes('이런 이유로 실패'));
  assert.ok(elements['voc-xval-result'].innerHTML.includes('A 조합 평가 중'));
  assert.strictEqual(elements['voc-xval-btn'].disabled, false, '실패 후 버튼이 복구돼야 함');
});

// ── 9. 엑셀 사용을 켰는데 업로드가 안 됐으면 fetch 없이 안내만 ───────────────────
check('엑셀 사용 체크됐는데 업로드된 파일이 없으면 실행하지 않고 안내창만 표시', async () => {
  const { context, calls } = buildSandbox({
    elementOverrides: { 'voc-use-excel': { checked: true } },
    fetchImpl: async () => { throw new Error('fetch가 호출되면 안 됨'); },
  });
  await context.runVocCrossValidation();
  assert.strictEqual(calls.fetchBodies.length, 0);
  assert.ok(calls.alerts.some((m) => m.includes('엑셀')));
});

// ── 10. 예상 단계 목록 계산(_buildXvalStepList)이 백엔드와 동일한 결정적 순서를 냄 ─
check('_buildXvalStepList이 선택한 그룹 순서대로 provider 생성 단계 → 조합 평가 단계 → 저장을 만듦', () => {
  const { context } = buildSandbox({});
  // Node vm은 별도 realm이라 vm 안에서 만든 배열을 바깥의 deepStrictEqual로 비교하면
  // 구조는 같아도 Array 생성자 정체성이 달라 실패한다(cross-realm 값) - Array.from으로
  // 바깥 realm의 새 배열로 다시 담아 비교한다(원소는 문자열 primitive라 값 자체는 그대로).
  assert.deepStrictEqual(
    Array.from(context._buildXvalStepList(['A', 'B', 'C', 'D'])),
    ['OpenAI 생성 중', 'Anthropic 생성 중', 'A 조합 평가 중', 'B 조합 평가 중', 'C 조합 평가 중', 'D 조합 평가 중', '저장 중'],
  );
  // C만 고르면 OpenAI 생성만 필요(judge_provider도 openai) - Anthropic 생성 단계가 없어야 함
  assert.deepStrictEqual(Array.from(context._buildXvalStepList(['C'])), ['OpenAI 생성 중', 'C 조합 평가 중', '저장 중']);
});

runAllChecks().then(() => {
  console.log(`\n${passed} passed, ${failed} failed (voc_cross_validation_regression.js)`);
  process.exit(failed > 0 ? 1 : 0);
}).catch((err) => {
  console.error('테스트 러너 자체가 실패했습니다:', err);
  process.exit(1);
});
