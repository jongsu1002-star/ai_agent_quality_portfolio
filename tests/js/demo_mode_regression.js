'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SCRIPT_PATH = path.join(__dirname, '..', '..', 'app', 'static', 'demo-mode.js');

let passed = 0;
let failed = 0;

function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  ok - ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  FAIL - ${name}: ${error.message}`);
  }
}

function storage(initial = {}) {
  const data = { ...initial };
  return {
    getItem(key) { return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null; },
    setItem(key, value) { data[key] = String(value); },
    removeItem(key) { delete data[key]; },
  };
}

function loadDemo(search = '?demo=1', initialStorage = {}) {
  assert.ok(fs.existsSync(SCRIPT_PATH), 'demo-mode.js가 존재해야 함');
  const source = fs.readFileSync(SCRIPT_PATH, 'utf-8');
  const document = {
    body: { classList: { add() {}, remove() {} }, appendChild() {}, querySelectorAll() { return []; } },
    documentElement: { addEventListener() {}, removeEventListener() {} },
    readyState: 'loading',
    addEventListener() {},
    createElement() { return { className: '', dataset: {}, style: {}, classList: { add() {}, remove() {} }, appendChild() {}, addEventListener() {}, remove() {} }; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const sandbox = {
    window: { location: { search, href: `http://localhost/${search}` }, addEventListener() {}, removeEventListener() {} },
    document,
    sessionStorage: storage(initialStorage),
    URLSearchParams,
    URL,
    setTimeout() { return 1; },
    clearTimeout() {},
    console,
  };
  sandbox.window.window = sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'demo-mode.js' });
  return sandbox.window.QADemoMode;
}

check('demo=1이 없으면 공개 API가 비활성 상태다', () => {
  const demo = loadDemo('');
  assert.strictEqual(demo.enabled, false);
});

check('demo=1이면 전체 메뉴 15개 단계가 활성화된다', () => {
  const demo = loadDemo('?demo=1');
  assert.strictEqual(demo.enabled, true);
  assert.strictEqual(demo.steps.length, 15);
  const targets = demo.steps.map((step) => step.target);
  for (const target of [
    '#tab-settings', '#tab-run', '#tab-dashboard', '#tab-monitoring',
    '#monitoring-addon-nav-btn', '#tab-board', '#tab-voc-analysis', '#tab-voc-results',
    '#users-tab-btn', '#error-log-tab-btn', '#ip-allowlist-tab-btn',
  ]) assert.ok(targets.includes(target), `${target} 단계가 필요합니다`);
});

check('외부 동작 단계는 자동 진행하지 않는다', () => {
  const demo = loadDemo('?demo=1');
  const risky = demo.steps.filter((step) => step.action === 'upload' || step.action === 'execute');
  assert.ok(risky.length >= 3);
  assert.ok(risky.every((step) => step.pauseForAction === true));
});

check('마스킹 선택자는 비밀 입력과 IP 표시를 포함한다', () => {
  const demo = loadDemo('?demo=1');
  assert.ok(demo.maskSelectors.includes('input[type="password"]'));
  assert.ok(demo.maskSelectors.includes('#my-ip-display'));
  assert.ok(demo.maskSelectors.includes('#jira-email'));
});

check('이전과 다음은 단계 범위를 벗어나지 않는다', () => {
  const demo = loadDemo('?demo=1');
  demo.goTo(0);
  demo.previous();
  assert.strictEqual(demo.currentIndex, 0);
  demo.goTo(99);
  assert.strictEqual(demo.currentIndex, 14);
  demo.next();
  assert.strictEqual(demo.currentIndex, 14);
});

check('저장된 단계가 새 페이지에서 복원된다', () => {
  const demo = loadDemo('?demo=1', { 'qa-demo-step': '7' });
  assert.strictEqual(demo.currentIndex, 7);
});

check('VOC 대체 단계는 사전 생성과 SKIPPED를 정직하게 표시한다', () => {
  const demo = loadDemo('?demo=1');
  const copy = demo.steps.map((step) => `${step.title} ${step.description}`).join(' ');
  assert.ok(copy.includes('사전 생성'));
  assert.ok(copy.includes('SKIPPED'));
});

check('데모 이동은 실제 경로와 해시를 구분해 보존한다', () => {
  const demo = loadDemo('?demo=1');
  assert.strictEqual(demo.withDemoQuery('/monitoring-addon'), '/monitoring-addon?demo=1');
  assert.strictEqual(demo.withDemoQuery('/#dashboard'), '/?demo=1#dashboard');
});

check('숨겨진 관리자 메뉴는 촬영 가능한 대상으로 취급하지 않는다', () => {
  const demo = loadDemo('?demo=1');
  assert.strictEqual(demo.isTargetAvailable(null), false);
  assert.strictEqual(demo.isTargetAvailable({ offsetParent: null, getClientRects() { return []; } }), false);
  assert.strictEqual(demo.isTargetAvailable({ offsetParent: {}, getClientRects() { return [{}]; } }), true);
});

check('프로세스 설명은 실행과 결과 상태를 공개 API에 보존한다', () => {
  const demo = loadDemo('?demo=1');
  demo.reportProcess('QA 데이터 준비 중', 'running');
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(demo.processState)),
    { message: 'QA 데이터 준비 중', state: 'running' }
  );
  demo.reportResult('QA 실행이 완료되었습니다.', 'success');
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(demo.processState)),
    { message: 'QA 실행이 완료되었습니다.', state: 'success' }
  );
});

check('체크박스 설명은 선택과 해제를 구분한다', () => {
  const demo = loadDemo('?demo=1');
  assert.strictEqual(demo.describeCheckbox({ checked: true, value: 'rag', id: '' }), 'rag 선택');
  assert.strictEqual(demo.describeCheckbox({ checked: false, value: 'rag', id: '' }), 'rag 선택 해제');
  assert.strictEqual(demo.describeCheckbox({
    checked: true,
    value: 'on',
    id: '',
    getAttribute(name) { return name === 'aria-label' ? '데이터셋 전체 선택' : ''; },
  }), '데이터셋 전체 선택');
  assert.strictEqual(demo.describeCheckbox({
    checked: true,
    value: 'on',
    id: 'voc-use-board',
    getAttribute() { return ''; },
    closest() { return { textContent: ' 게시판 VOC ' }; },
  }), '게시판 VOC 선택');
});

console.log(`\n${passed} passed, ${failed} failed (demo_mode_regression.js)`);
process.exit(failed > 0 ? 1 : 0);
