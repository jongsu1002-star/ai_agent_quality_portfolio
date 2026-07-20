// renderStepChecklist(steps, activeIndex, { failedIndex }) 순수 함수 회귀 테스트 - 실행
// 버튼(QA 파이프라인/VOC 분석/교차검증 매트릭스/업로드 3종)이 전부 공유하는 단계 체크리스트
// 컴포넌트라, 다른 슬라이스 테스트들은 이 함수를 스텁으로 대체해 각자의 로직만 검증한다
// (voc_polling_regression.js, voc_cross_validation_regression.js 참고). 이 파일은 반대로
// 그 실제 구현 자체를 검증한다.
//
// 실행: node tests/js/step_checklist_regression.js (tests/test_frontend_js_regression.py가
// pytest에서 이 스크립트를 subprocess로 호출 - node가 없는 환경에서는 그 pytest가 스스로 skip).
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const INDEX_HTML_PATH = path.join(__dirname, '..', '..', 'app', 'templates', 'index.html');
const html = fs.readFileSync(INDEX_HTML_PATH, 'utf-8');

const START_MARK = 'function escapeHtml(value) {';
const END_MARK = '// 뷰어(브라우저)의 OS/브라우저 타임존';
const startIdx = html.indexOf(START_MARK);
const endIdx = html.indexOf(END_MARK);
if (startIdx === -1 || endIdx === -1) {
  console.error('FAIL: index.html에서 renderStepChecklist 코드 블록 경계 마커를 찾지 못했습니다 - ' +
    '리팩터링으로 위치가 바뀌었으면 이 스크립트의 START_MARK/END_MARK를 갱신하세요.');
  process.exit(1);
}
const source = html.slice(startIdx, endIdx);

const pendingChecks = [];
let passed = 0;
let failed = 0;

function check(name, fn) {
  pendingChecks.push({ name, fn });
}

function runAllChecks() {
  for (const { name, fn } of pendingChecks) {
    try {
      fn();
      passed += 1;
      console.log(`  ok - ${name}`);
    } catch (err) {
      failed += 1;
      console.log(`  FAIL - ${name}: ${err.stack || err.message}`);
    }
  }
}

function buildContext() {
  const sandbox = { console, String, Array };
  const context = vm.createContext(sandbox);
  vm.runInContext(source, context, { filename: 'step_checklist_slice.js' });
  return context;
}

check('첫 단계가 진행 중(activeIndex=0)이면 나머지는 전부 대기 상태', () => {
  const html = buildContext().renderStepChecklist(['A', 'B', 'C'], 0);
  assert.ok(html.includes('step-active'), '현재 단계는 active 클래스를 가져야 함');
  assert.strictEqual((html.match(/step-active/g) || []).length, 1, 'active는 정확히 1곳이어야 함');
  assert.strictEqual((html.match(/step-done/g) || []).length, 0, '첫 단계 진행 중일 땐 완료된 단계가 없어야 함');
});

check('activeIndex가 중간이면 그 앞은 완료, 자신은 진행 중, 뒤는 대기', () => {
  const html = buildContext().renderStepChecklist(['A', 'B', 'C', 'D'], 2);
  assert.strictEqual((html.match(/step-done/g) || []).length, 2, '앞의 2개 단계가 완료로 표시돼야 함');
  assert.strictEqual((html.match(/step-active/g) || []).length, 1);
});

check('activeIndex가 steps.length와 같으면 전부 완료(진행 중인 단계 없음)', () => {
  const html = buildContext().renderStepChecklist(['A', 'B'], 2);
  assert.strictEqual((html.match(/step-done/g) || []).length, 2);
  assert.strictEqual((html.match(/step-active/g) || []).length, 0);
});

check('failedIndex를 지정하면 그 단계만 실패로 표시되고 그 이후 단계는 대기 상태로 남음', () => {
  const html = buildContext().renderStepChecklist(['A', 'B', 'C'], 1, { failedIndex: 1 });
  assert.strictEqual((html.match(/step-error/g) || []).length, 1);
  assert.strictEqual((html.match(/step-done/g) || []).length, 1, '실패 단계 앞은 완료로 남아야 함');
  assert.strictEqual((html.match(/step-active/g) || []).length, 0, '실패 이후엔 진행 중 표시가 없어야 함');
});

check('단계 이름은 HTML 이스케이프돼 렌더링됨(저장형 XSS 방지)', () => {
  const html = buildContext().renderStepChecklist(['<img src=x onerror=alert(1)>'], 0);
  assert.ok(!html.includes('<img'), '스크립트성 태그가 그대로 삽입되면 안 됨');
  assert.ok(html.includes('&lt;img'));
});

check('단계 이름 순서가 그대로 유지됨', () => {
  const html = buildContext().renderStepChecklist(['첫단계', '둘째단계', '셋째단계'], 1);
  const firstIdx = html.indexOf('첫단계');
  const secondIdx = html.indexOf('둘째단계');
  const thirdIdx = html.indexOf('셋째단계');
  assert.ok(firstIdx < secondIdx && secondIdx < thirdIdx, '단계는 입력 순서 그대로 렌더링돼야 함');
});

runAllChecks();
console.log(`\n${passed} passed, ${failed} failed (step_checklist_regression.js)`);
process.exit(failed > 0 ? 1 : 0);
