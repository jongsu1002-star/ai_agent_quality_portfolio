# Docker Active Portfolio Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix guided-demo navigation, add an explicitly labeled synthetic VOC Improved five-stage demonstration, verify the complete Docker stack, and record a 5–6 minute activity-driven video covering every menu.

**Architecture:** Keep production QA/VOC APIs unchanged. Guided-demo behavior remains opt-in through `?demo=1`; a small front-end demo fixture animates VOC Improved stages using the committed synthetic result and clearly labels the Judge as `SKIPPED`. Docker Compose supplies the real app, Prometheus, Grafana, QA upload/run, and k6 activity used during recording.

**Tech Stack:** FastAPI/Jinja, browser JavaScript, Node VM regression tests, pytest, Docker Compose, Prometheus, Grafana, k6, OBS WebSocket.

## Global Constraints

- Run without OpenAI or Anthropic network calls.
- Display `LLM Judge: SKIPPED` and “사전 생성 합성 결과” anywhere a synthetic VOC result is shown.
- Use only `demo/video/qa_dataset.json`, `qa_testcases.json`, `voc_samples.json`, and `voc_demo_result.json` as uploaded or seeded content.
- Never mutate users, roles, posts, allowlisted IPs, or external Jira/webhook systems during recording.
- Mask API keys, tokens, email addresses, webhook URLs, and internal/public IP addresses in every recorded frame.
- Final MP4 must be 1920×1080 and between 5:00 and 6:00.

---

### Task 1: Preserve Real Paths in Guided-Demo Navigation

**Files:**
- Modify: `tests/js/demo_mode_regression.js`
- Modify: `app/static/demo-mode.js:110-119`

**Interfaces:**
- Consumes: inline handlers shaped as `window.location.href='/path'` and `window.location.href='/#tab'`.
- Produces: `withDemoQuery(rawHref: string): string`, returning `/monitoring-addon?demo=1` for paths and `/?demo=1#tab` for root hashes.

- [ ] **Step 1: Write the failing navigation regression**

Add assertions that start demo mode with two buttons and verify their rewritten handlers:

```javascript
assert.equal(rewrite("window.location.href='/monitoring-addon'"),
  "window.location.href='/monitoring-addon?demo=1'");
assert.equal(rewrite("window.location.href='/#dashboard'"),
  "window.location.href='/?demo=1#dashboard'");
```

- [ ] **Step 2: Run the focused test and observe RED**

Run: `node tests/js/demo_mode_regression.js`

Expected: FAIL because `/monitoring-addon` is rewritten to `/?demo=1#monitoring-addon`.

- [ ] **Step 3: Implement path-aware rewriting**

Use the URL parser already used for anchor links:

```javascript
function withDemoQuery(rawHref) {
  const url = new URL(rawHref, window.location.href);
  url.searchParams.set('demo', '1');
  return `${url.pathname}${url.search}${url.hash}`;
}
```

Extract the quoted href from each inline handler, pass it to `withDemoQuery`, and replace only that href.

- [ ] **Step 4: Run focused and frontend tests**

Run: `node tests/js/demo_mode_regression.js`

Run: `pytest tests/test_frontend_js_regression.py -q`

Expected: both exit 0.

- [ ] **Step 5: Commit**

```powershell
git add tests/js/demo_mode_regression.js app/static/demo-mode.js
git commit -m "fix: preserve addon path in guided demo"
```

### Task 2: Expand the Guided Demo to Every Menu and Real Activity

**Files:**
- Modify: `tests/js/demo_mode_regression.js`
- Modify: `tests/test_frontend_js_regression.py`
- Modify: `app/static/demo-mode.js`
- Modify: `app/templates/index.html`
- Modify: `app/templates/monitoring_addon.html`
- Modify: `app/static/demo-mode.css`

**Interfaces:**
- Consumes: stable menu/tab/card IDs from both templates.
- Produces: a 15-step `steps` array whose items expose `target`, `title`, `description`, `action`, and `pauseForAction`.

- [ ] **Step 1: Add failing assertions for complete menu coverage**

Assert that the demo script contains stable targets for settings, run, dashboard, monitoring, addon, board, VOC analysis, VOC results, users, error log, and IP allowlist. Add missing stable IDs to expected template contracts, including:

```python
for target in (
    "tab-settings", "tab-run", "tab-dashboard", "tab-monitoring",
    "tab-board", "tab-voc-analysis", "tab-voc-results",
    "users-nav-btn", "error-log-nav-btn", "ip-allowlist-nav-btn",
):
    assert f'id="{target}"' in html
```

- [ ] **Step 2: Run the tests and observe RED**

Run: `pytest tests/test_frontend_js_regression.py -q`

Expected: FAIL on IDs or demo targets not yet present.

- [ ] **Step 3: Add stable IDs and the 15-step script**

Give each menu/content area one unique stable ID and expand `steps` in recording order. Mark upload/run/k6/VOC Improved steps with `pauseForAction: true`. Keep descriptions to one sentence so the lower-third does not cover controls.

- [ ] **Step 4: Verify action emphasis and masking**

Extend regression checks for `qa-demo-ripple`, file-upload targets, progress targets, and the existing secret-mask selectors. Ensure missing admin targets produce a visible “관리자 세션 필요” caption instead of silently highlighting nothing.

- [ ] **Step 5: Run focused tests**

Run: `node tests/js/demo_mode_regression.js`

Run: `pytest tests/test_frontend_js_regression.py tests/test_ui_and_env_docs.py -q`

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add tests/js/demo_mode_regression.js tests/test_frontend_js_regression.py app/static/demo-mode.js app/static/demo-mode.css app/templates/index.html app/templates/monitoring_addon.html
git commit -m "feat: cover every menu in active demo mode"
```

### Task 3: Add the Synthetic VOC Improved Demonstration

**Files:**
- Modify: `tests/js/voc_polling_regression.js`
- Modify: `tests/test_frontend_js_regression.py`
- Modify: `app/templates/index.html`
- Modify: `app/static/demo-mode.js`
- Modify: `demo/video/voc_demo_result.json`

**Interfaces:**
- Consumes: `window.QADemoMode.enabled`, existing `renderStepChecklist`, and the committed synthetic VOC result.
- Produces: `runSyntheticVocImprovedDemo(): Promise<void>`, a demo-only client flow that renders five labeled stages and finishes with `judge.verdict === "SKIPPED"`.

- [ ] **Step 1: Write a failing Node regression**

Exercise the VOC run handler in `?demo=1` with LLM provider disabled and assert the ordered visible stages:

```javascript
assert.deepEqual(stageNames, [
  '의도 분류 중',
  '개선안 생성 중',
  '자가 비평·교정 중',
  '내부 재점검 중',
  '독립 Judge 확인 중',
]);
assert.equal(result.judge.verdict, 'SKIPPED');
assert.match(result.summary, /사전 생성.*합성/);
```

- [ ] **Step 2: Run the focused test and observe RED**

Run: `node tests/js/voc_polling_regression.js`

Expected: FAIL because no synthetic improved runner exists.

- [ ] **Step 3: Implement the demo-only state machine**

When and only when `window.QADemoMode.enabled` is true and provider is disabled, animate the five checklist stages at deterministic intervals, render the committed fixture, and append a persistent banner:

```html
<div class="qa-demo-fixture-banner">
  촬영용 합성 실행 · 외부 LLM 미호출 · LLM Judge: SKIPPED
</div>
```

Do not call `/api/voc-analysis/run-async` in this branch. Leave the normal VOC production path untouched.

- [ ] **Step 4: Verify fixture honesty and production isolation**

Add assertions that the synthetic branch is guarded by demo mode, includes `SKIPPED`, and that the ordinary branch still calls `/api/voc-analysis/run-async`.

- [ ] **Step 5: Run VOC and frontend suites**

Run: `node tests/js/voc_polling_regression.js`

Run: `pytest tests/test_frontend_js_regression.py tests/test_voc_analysis.py tests/test_voc_analysis_api.py -q`

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add tests/js/voc_polling_regression.js tests/test_frontend_js_regression.py app/templates/index.html app/static/demo-mode.js demo/video/voc_demo_result.json
git commit -m "feat: add transparent synthetic VOC Improved demo"
```

### Task 4: Prepare and Verify the Docker Recording Stack

**Files:**
- Create: `demo/video/docker-recording-checklist.md`
- Modify: `demo/video/portfolio-demo.ko.srt`
- Create: `demo/video/portfolio-demo-docker.ko.srt`

**Interfaces:**
- Consumes: `docker-compose.yml`, `.env`, committed demo fixtures, and OBS WebSocket on `127.0.0.1:4455`.
- Produces: three healthy containers, seeded demo state, and a timestamped rehearsal record.

- [ ] **Step 1: Record the preflight checks**

Document exact commands and expected outcomes:

```powershell
docker compose up -d --build
docker compose ps
Invoke-WebRequest http://127.0.0.1:8000/health
Invoke-WebRequest http://127.0.0.1:8000/metrics-addon
```

- [ ] **Step 2: Start the stack and verify health**

Require `qa-platform`, `prometheus`, and `grafana` to be running/healthy. If any service fails, collect `docker compose logs --tail 200 <service>` and stop before recording.

- [ ] **Step 3: Rehearse real actions in Chrome**

Upload `qa_dataset.json`, `qa_testcases.json`, and `voc_samples.json`; run QA; navigate every menu; run `/health` at 1 VU for 10 seconds; verify VOC Improved reaches the synthetic `SKIPPED` result. Do not alter admin state.

- [ ] **Step 4: Write the Docker SRT**

Create 15 cues aligned to the approved 5:55 timeline, with a final cue ending no later than `00:05:55,000`.

- [ ] **Step 5: Run full targeted verification**

Run:

```powershell
pytest tests/test_frontend_js_regression.py tests/test_ui_and_env_docs.py tests/test_reporting_and_ui.py tests/test_voc_analysis.py tests/test_voc_analysis_api.py tests/test_monitoring_addon_api.py -q
docker compose ps
```

Expected: pytest exits 0; all three Docker services are running and required health checks pass.

- [ ] **Step 6: Commit**

```powershell
git add demo/video/docker-recording-checklist.md demo/video/portfolio-demo-docker.ko.srt
git commit -m "docs: prepare Docker portfolio recording"
```

### Task 5: Record and Validate the Final Video

**Files:**
- Create: `demo/video/portfolio-demo-docker.mp4`
- Modify: `demo/video/docker-recording-checklist.md`

**Interfaces:**
- Consumes: healthy Docker stack, verified Chrome demo, OBS scene with display capture, approved timeline.
- Produces: final MP4 and evidence of duration, size, resolution, menu coverage, and masking.

- [ ] **Step 1: Verify the OBS frame before recording**

Capture an OBS source screenshot and visually confirm Chrome fills the 1920×1080 canvas, no editor/chat window is visible, and settings are masked.

- [ ] **Step 2: Record the approved activity sequence**

Start OBS, execute the 15 steps, pause only for real uploads/runs, and stop between 5:00 and 6:00. If an upload, QA run, addon navigation, k6 run, or mask fails, retain the take as a draft and restart from step 1.

- [ ] **Step 3: Validate the output**

Check that the MP4 exists, is non-empty, reports 1920×1080, and is 300–360 seconds long. Spot-check frames from settings, QA progress, addon/k6, VOC Improved, and admin menus.

- [ ] **Step 4: Update the checklist with evidence**

Record the source file, final path, duration, byte size, Docker service status, test count, and any intentionally skipped external calls.

- [ ] **Step 5: Commit metadata, not a duplicate draft**

Keep only `portfolio-demo-docker.mp4` as the project deliverable; leave OBS's original recording in the Windows Videos directory as the recoverable source.

