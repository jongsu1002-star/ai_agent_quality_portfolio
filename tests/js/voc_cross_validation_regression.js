// VOC 교차검증 매트릭스(A~D 조합 선택 + 실행 전용 API 키 입력) 최소 회귀 테스트 -
// voc_polling_regression.js와 동일한 방식(Node vm으로 index.html에서 해당 함수 블록만
// 그대로 잘라 실행, 사본을 따로 두지 않음)으로 실제 동작을 검증한다. 기존 프론트 배선
// 테스트(test_voc_cross_validation_group_selection_and_api_key_override_wired)는 HTML에
// 특정 문자열이 있는지만 확인할 뿐, 체크박스 선택/키 입력/전송 페이로드/실행 후 정리
// 같은 실제 동작은 검증하지 않는다는 한계가 있어 이 파일로 그 공백을 메운다.
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

// runVocCrossValidation/_renderVocCrossValidationResult는 voc_polling_regression.js가 쓰는
// 슬라이스(START_MARK~END_MARK) 범위 안에 이미 포함돼 있어(같은 <script> 블록 안, 폴링 코드
// 바로 뒤) 동일한 경계 마커를 그대로 재사용한다 - 별도 마커를 새로 만들면 두 파일이 같은
// 소스를 서로 다른 기준으로 잘라내다 한쪽만 갱신되는 드리프트가 생길 수 있음.
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
  .replace('let _vocPollFailureCount = 0;', 'var _vocPollFailureCount = 0;');

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
  const calls = { alerts: [], showToast: [], loadVocCrossValidationHistory: 0, fetchBodies: [] };

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
      if (opts && opts.body) calls.fetchBodies.push(JSON.parse(opts.body));
      return fetchImpl ? fetchImpl(url, opts) : Promise.reject(new Error('fetch not stubbed'));
    },
    setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {},
    alert: (msg) => { calls.alerts.push(msg); },
    confirm: () => true,
    escapeHtml: (s) => String(s),
    showToast: (msg, type) => { calls.showToast.push({ msg, type }); },
    loadVocCrossValidationHistory: () => { calls.loadVocCrossValidationHistory += 1; },
    _vocExcelPath: null,
    Date, JSON, Math, Object, Array, Number, String, Boolean, Promise, Error,
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(source, context, { filename: 'voc_cross_validation_slice.js' });
  return { context, elements, calls };
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

// ── 2. 일부만 선택하면 그 값만 정확히 요청 본문에 실림 ───────────────────────────
check('선택한 그룹만 정확히 요청 본문(groups)에 포함됨', async () => {
  const { context, calls } = buildSandbox({
    checkboxStates: [
      { value: 'A', checked: true }, { value: 'B', checked: false },
      { value: 'C', checked: true }, { value: 'D', checked: false },
    ],
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ id: 'x', result: { matrix: [] } }) }),
  });
  await context.runVocCrossValidation();
  assert.deepStrictEqual(calls.fetchBodies[0].groups, ['A', 'C']);
});

// ── 3. API 키 입력값이 trim되어 요청 본문에 실리고, 비어있으면 null ──────────────
check('입력한 API 키가 trim되어 요청 본문에 실리고 빈 값이면 null', async () => {
  const { context, calls } = buildSandbox({
    elementOverrides: {
      'voc-xval-openai-key': { value: '  sk-test-openai  ' },
      'voc-xval-anthropic-key': { value: '' },
    },
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ id: 'x', result: { matrix: [] } }) }),
  });
  await context.runVocCrossValidation();
  assert.strictEqual(calls.fetchBodies[0].openai_api_key, 'sk-test-openai');
  assert.strictEqual(calls.fetchBodies[0].anthropic_api_key, null);
});

// ── 4. 성공/실패와 무관하게 실행 후 API 키 입력란이 비워짐(보안 위생) ────────────
check('실행 성공 후 API 키 입력란이 비워짐', async () => {
  const { context, elements } = buildSandbox({
    elementOverrides: {
      'voc-xval-openai-key': { value: 'sk-test-openai' },
      'voc-xval-anthropic-key': { value: 'sk-test-anthropic' },
    },
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ id: 'x', result: { matrix: [] } }) }),
  });
  await context.runVocCrossValidation();
  assert.strictEqual(elements['voc-xval-openai-key'].value, '', '성공 후 OpenAI 키 입력란이 비어야 함');
  assert.strictEqual(elements['voc-xval-anthropic-key'].value, '', '성공 후 Anthropic 키 입력란이 비어야 함');
});

check('실행 실패(서버 에러 응답) 후에도 API 키 입력란이 비워짐', async () => {
  const { context, elements } = buildSandbox({
    elementOverrides: {
      'voc-xval-openai-key': { value: 'sk-test-openai' },
      'voc-xval-anthropic-key': { value: 'sk-test-anthropic' },
    },
    fetchImpl: async () => ({ ok: false, status: 502, json: async () => ({ error: '처리 실패' }) }),
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

// ── 5. 성공 시 이력 갱신 + 성공 토스트, 실패 시 에러 메시지 표시 ─────────────────
check('실행 성공 시 이력을 다시 불러오고 성공 토스트를 표시', async () => {
  const { context, calls } = buildSandbox({
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ id: 'x', result: { matrix: [] } }) }),
  });
  await context.runVocCrossValidation();
  assert.strictEqual(calls.loadVocCrossValidationHistory, 1);
  assert.ok(calls.showToast.some((c) => c.type === 'success' || c.type === undefined));
});

check('실행 실패(에러 응답) 시 결과 영역에 에러 메시지를 표시', async () => {
  const { context, elements } = buildSandbox({
    fetchImpl: async () => ({ ok: false, status: 502, json: async () => ({ error: '이런 이유로 실패' }) }),
  });
  await context.runVocCrossValidation();
  assert.ok(elements['voc-xval-result'].innerHTML.includes('이런 이유로 실패'));
});

// ── 6. 엑셀 사용을 켰는데 업로드가 안 됐으면 fetch 없이 안내만 ───────────────────
check('엑셀 사용 체크됐는데 업로드된 파일이 없으면 실행하지 않고 안내창만 표시', async () => {
  const { context, calls } = buildSandbox({
    elementOverrides: { 'voc-use-excel': { checked: true } },
    fetchImpl: async () => { throw new Error('fetch가 호출되면 안 됨'); },
  });
  await context.runVocCrossValidation();
  assert.strictEqual(calls.fetchBodies.length, 0);
  assert.ok(calls.alerts.some((m) => m.includes('엑셀')));
});

runAllChecks().then(() => {
  console.log(`\n${passed} passed, ${failed} failed (voc_cross_validation_regression.js)`);
  process.exit(failed > 0 ? 1 : 0);
}).catch((err) => {
  console.error('테스트 러너 자체가 실패했습니다:', err);
  process.exit(1);
});
