# Active Captioned Portfolio Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a verified five-minute portfolio recording in which every checkbox is visibly stateful, every core public and administrative write flow is performed with synthetic data, captions explain each process, and no visually static interval reaches three seconds.

**Architecture:** Keep product behavior in the existing templates and add a reusable checkbox visual system in `ui-system.css`. Extend the opt-in `demo=1` layer with a two-line process/result caption API and checkbox-change feedback, then drive real UI actions through the connected browser while OBS records. Use a small Python verifier plus FFmpeg to enforce duration, resolution, frame rate, subtitle coverage, decode integrity, and the three-second freeze threshold.

**Tech Stack:** FastAPI/Jinja templates, vanilla JavaScript and CSS, Node-based JavaScript regression tests, pytest, Docker Compose, Chrome/Browser control, OBS WebSocket, FFmpeg, Python 3 standard library.

## Global Constraints

- Final output is exactly 5 minutes, 1920×1080, 60fps MP4.
- Every checkbox type must show a high-contrast selected and unselected state immediately after interaction.
- Mouse movement must take 0.4–0.8 seconds on a curved path, with a 0.2–0.4 second pre-click pause.
- No visually unchanged interval may last 3 seconds or longer.
- Burned-in Korean captions must identify the feature, current process, and result; the same text must be delivered as UTF-8 SRT.
- Registration, edit, approval, role/status change, upload, execution, query, download, comment, selection, and deletion must be performed through the visible UI.
- Only synthetic local data may be mutated. AWS remains read-only and all account-identifying areas remain cropped.
- VOC Improved must show the five-stage synthetic run, no external LLM call, and `LLM Judge: SKIPPED`.
- Prometheus and Grafana must display non-empty charts, and CloudFormation must display `CREATE_COMPLETE`.

## File Structure

- Modify `app/static/ui-system.css`: global high-contrast checkbox component shared by static and dynamically rendered inputs.
- Modify `app/static/demo-mode.css`: process/result caption rows, checkbox-change animation, and continuous activity indicator.
- Modify `app/static/demo-mode.js`: process caption state, checkbox feedback, toast-result bridge, and safe public methods used during recording.
- Modify `app/templates/index.html`: report real success/error toasts to the demo caption layer.
- Modify `app/templates/monitoring_addon.html`: report k6 process and completion states to the same caption layer.
- Modify `tests/js/demo_mode_regression.js`: direct behavior tests for process/result state and checkbox narration.
- Modify `tests/test_frontend_js_regression.py`: asset, selector, and template integration assertions.
- Create `scripts/verify_demo_video.py`: deterministic media metadata, freeze log, decode, and SRT coverage verification.
- Create `tests/test_verify_demo_video.py`: parser and policy unit tests for the video verifier.
- Create `demo/video/active-recording-runbook.md`: second-by-second real UI action and mouse movement runbook.
- Replace `demo/video/portfolio-demo-docker.ko.srt`: exact five-minute caption track.
- Modify `demo/video/docker-recording-checklist.md`: functional, privacy, activity, and media evidence checklist.
- Replace `demo/video/portfolio-demo-docker.mp4`: verified recording artifact; keep it out of Git unless repository policy explicitly changes.

---

### Task 1: High-Contrast Checkbox System

**Files:**
- Modify: `app/static/ui-system.css:423`
- Modify: `tests/test_frontend_js_regression.py:97`

**Interfaces:**
- Consumes: every native `input[type="checkbox"]` rendered by the main and monitoring-addon templates.
- Produces: CSS states `input[type="checkbox"]`, `:checked`, `:focus-visible`, and `.qa-demo-checkbox-changed` that later demo feedback reuses.

- [ ] **Step 1: Write the failing CSS contract test**

```python
def test_all_native_checkboxes_have_visible_checked_and_focus_states():
    css = (REPO_ROOT / "app" / "static" / "ui-system.css").read_text(encoding="utf-8")
    assert 'input[type="checkbox"]' in css
    assert 'input[type="checkbox"]:checked' in css
    assert 'input[type="checkbox"]:focus-visible' in css
    assert 'appearance: none' in css
    assert 'background: #2563eb' in css
    assert 'border-color: #1d4ed8' in css
    assert 'content: ""' in css
```

- [ ] **Step 2: Run the focused test and confirm the missing contract**

Run: `pytest tests/test_frontend_js_regression.py::test_all_native_checkboxes_have_visible_checked_and_focus_states -v`

Expected: FAIL because the shared checkbox selectors and custom checked mark are absent.

- [ ] **Step 3: Add the reusable checkbox visual component**

Append this focused block after the existing form-control rules in `app/static/ui-system.css`:

```css
.app-page input[type="checkbox"] {
  appearance: none;
  -webkit-appearance: none;
  display: inline-grid !important;
  place-content: center;
  width: 1.2rem !important;
  height: 1.2rem !important;
  min-width: 1.2rem;
  margin: 0 0.38rem 0 0;
  border: 2px solid #64748b;
  border-radius: 0.28rem;
  background: #ffffff;
  vertical-align: -0.2rem;
  cursor: pointer;
  transition: background-color 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
}

.app-page input[type="checkbox"]::before {
  content: "";
  width: 0.62rem;
  height: 0.34rem;
  border-left: 0.18rem solid #ffffff;
  border-bottom: 0.18rem solid #ffffff;
  transform: rotate(-45deg) scale(0);
  transform-origin: center;
  transition: transform 120ms ease;
}

.app-page input[type="checkbox"]:checked {
  border-color: #1d4ed8;
  background: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.2);
}

.app-page input[type="checkbox"]:checked::before {
  transform: rotate(-45deg) scale(1);
}

.app-page input[type="checkbox"]:focus-visible {
  outline: 3px solid #f59e0b;
  outline-offset: 2px;
}
```

Use `!important` only for dimensions because several existing inputs have inline `width:auto`; do not override their checked state or disabled behavior with JavaScript.

- [ ] **Step 4: Run checkbox and frontend regression tests**

Run: `pytest tests/test_frontend_js_regression.py -v`

Expected: all tests PASS, including the new checkbox contract.

- [ ] **Step 5: Commit the checkbox component**

```powershell
git add app/static/ui-system.css tests/test_frontend_js_regression.py
git commit -m "fix: make every checkbox state visible"
```

### Task 2: Process Caption and Checkbox Feedback API

**Files:**
- Modify: `tests/js/demo_mode_regression.js:20-120`
- Modify: `app/static/demo-mode.js:44-176`
- Modify: `app/static/demo-mode.css:13-99`

**Interfaces:**
- Consumes: native `change` events from checkboxes and process/result messages from templates.
- Produces: `QADemoMode.reportProcess(message: string, state?: "idle"|"running"|"success"|"error")`, `QADemoMode.reportResult(message: string, type?: "success"|"error")`, and read-only `QADemoMode.processState`.

- [ ] **Step 1: Add failing JavaScript state tests**

Add these checks to `tests/js/demo_mode_regression.js`:

```javascript
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
});
```

- [ ] **Step 2: Run the Node regression and confirm the missing API**

Run: `node tests/js/demo_mode_regression.js`

Expected: at least two FAIL lines because `reportProcess`, `reportResult`, and `describeCheckbox` are undefined.

- [ ] **Step 3: Implement process state and caption rendering**

In `app/static/demo-mode.js`, initialize and expose a state object:

```javascript
let processState = { message: '시연 준비 완료', state: 'idle' };

function updateProcessRow() {
  if (!caption) return;
  const row = caption.querySelector('[data-demo-process]');
  if (!row) return;
  row.textContent = processState.message;
  row.dataset.state = processState.state;
}

function reportProcess(message, state = 'running') {
  processState = { message: String(message || ''), state };
  updateProcessRow();
}

function reportResult(message, type = 'success') {
  reportProcess(message, type === 'error' ? 'error' : 'success');
}

function describeCheckbox(element) {
  const label = element.value || element.id || '항목';
  return `${label} ${element.checked ? '선택' : '선택 해제'}`;
}

function onCheckboxChange(event) {
  const target = event.target;
  if (!(target instanceof HTMLInputElement) || target.type !== 'checkbox') return;
  reportProcess(describeCheckbox(target), 'success');
  target.classList.remove('qa-demo-checkbox-changed');
  void target.offsetWidth;
  target.classList.add('qa-demo-checkbox-changed');
  setTimeout(() => target.classList.remove('qa-demo-checkbox-changed'), 800);
}
```

Add `<p class="qa-demo-process" data-demo-process data-state="idle"></p>` below the description in `ensureOverlay()`. Call `updateProcessRow()` from `render()`. Register `onCheckboxChange` with `document.documentElement.addEventListener('change', onCheckboxChange, true)` in `start()`, and remove the same listener with `removeEventListener('change', onCheckboxChange, true)` in `stop()`.

Expose the methods and a defensive getter:

```javascript
reportProcess,
reportResult,
describeCheckbox,
get processState() { return { ...processState }; },
```

- [ ] **Step 4: Style the process row and visible checkbox change**

Add to `app/static/demo-mode.css`:

```css
.qa-demo-process {
  display: flex;
  align-items: center;
  min-height: 1.45rem;
  margin-top: 0.3rem !important;
  color: #dbeafe;
  font-weight: 700;
}

.qa-demo-process::before {
  content: "";
  width: 0.58rem;
  height: 0.58rem;
  margin-right: 0.45rem;
  border-radius: 50%;
  background: #60a5fa;
  animation: qa-demo-activity 1.2s ease-in-out infinite;
}

.qa-demo-process[data-state="success"]::before { background: #34d399; }
.qa-demo-process[data-state="error"]::before { background: #fb7185; }
.qa-demo-checkbox-changed { animation: qa-demo-checkbox-confirm 0.8s ease-out; }

@keyframes qa-demo-activity { 50% { transform: scale(1.55); opacity: 0.55; } }
@keyframes qa-demo-checkbox-confirm { 50% { box-shadow: 0 0 0 8px rgba(37, 99, 235, 0.28); } }
```

- [ ] **Step 5: Run focused and Python-wrapped regressions**

Run: `node tests/js/demo_mode_regression.js`

Expected: the summary ends with `0 failed`.

Run: `pytest tests/test_frontend_js_regression.py::test_demo_mode_js_regression_suite_passes -v`

Expected: PASS.

- [ ] **Step 6: Commit the caption API**

```powershell
git add app/static/demo-mode.js app/static/demo-mode.css tests/js/demo_mode_regression.js
git commit -m "feat: explain live demo process states"
```

### Task 3: Bridge Real UI Results Into Captions

**Files:**
- Modify: `app/templates/index.html:857-876`
- Modify: `app/templates/monitoring_addon.html:150-260`
- Modify: `tests/test_frontend_js_regression.py:97-190`

**Interfaces:**
- Consumes: `QADemoMode.reportProcess()` and `QADemoMode.reportResult()` from Task 2.
- Produces: real success and error messages in the recording overlay for settings, board, users, IP allowlist, external monitoring, QA, VOC, and k6 actions.

- [ ] **Step 1: Write failing template integration tests**

```python
def test_main_toasts_are_forwarded_to_demo_result_caption():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "window.QADemoMode?.reportResult(message, type)" in html


def test_k6_reports_process_and_result_to_demo_caption():
    html = MONITORING_ADDON_HTML.read_text(encoding="utf-8")
    assert "window.QADemoMode?.reportProcess" in html
    assert "window.QADemoMode?.reportResult" in html
```

- [ ] **Step 2: Run the two tests and verify failure**

Run: `pytest tests/test_frontend_js_regression.py -k "toasts_are_forwarded or k6_reports_process" -v`

Expected: two FAIL results because the bridge calls are absent.

- [ ] **Step 3: Forward every main-page toast**

At the start of the existing `showToast(message, type = 'success')` function in `app/templates/index.html`, add:

```javascript
window.QADemoMode?.reportResult(message, type);
```

Do not duplicate messages at individual call sites; this single bridge covers all existing success and error toasts.

- [ ] **Step 4: Report k6 lifecycle from the addon page**

Immediately before the k6 request, call:

```javascript
window.QADemoMode?.reportProcess('k6 부하 테스트 실행 중 · /health · 1 VU · 10초', 'running');
```

On the existing successful response branch, call:

```javascript
window.QADemoMode?.reportResult('k6 실행 완료 · p95와 실패율을 확인합니다.', 'success');
```

On the existing failure/catch branch, call:

```javascript
window.QADemoMode?.reportResult(errorMessage, 'error');
```

Reuse the branch's displayed `errorMessage`; do not introduce a second error source.

- [ ] **Step 5: Run template and JavaScript regressions**

Run: `pytest tests/test_frontend_js_regression.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit the real-result bridge**

```powershell
git add app/templates/index.html app/templates/monitoring_addon.html tests/test_frontend_js_regression.py
git commit -m "feat: surface real action results in demo captions"
```

### Task 4: Deterministic Video and Subtitle Verifier

**Files:**
- Create: `scripts/verify_demo_video.py`
- Create: `tests/test_verify_demo_video.py`

**Interfaces:**
- Consumes: FFmpeg executable path, MP4 path, UTF-8 SRT path, and FFmpeg stderr text.
- Produces: exit code 0 only when duration is 300.00±0.05 seconds, video is 1920×1080 at 60fps, decode succeeds, no freeze reaches 3.0 seconds, and SRT cues cover 0:00–5:00 without gaps over 3.0 seconds.

- [ ] **Step 1: Write parser and policy tests**

```python
from scripts.verify_demo_video import parse_freezes, parse_srt, validate_cues


def test_parse_freezes_pairs_start_and_duration():
    log = "freeze_start: 12.4\nfreeze_end: 15.6 | freeze_duration: 3.2\n"
    assert parse_freezes(log) == [(12.4, 15.6, 3.2)]


def test_validate_cues_rejects_gap_of_three_seconds():
    cues = [(0.0, 2.0), (5.0, 8.0), (8.0, 300.0)]
    errors = validate_cues(cues, expected_end=300.0, max_gap=3.0)
    assert any("3.000초 자막 공백" in error for error in errors)


def test_parse_srt_accepts_utf8_korean_and_full_coverage():
    text = "1\n00:00:00,000 --> 00:02:30,000\n기능 설명\n\n2\n00:02:30,000 --> 00:05:00,000\n처리 완료\n"
    cues = parse_srt(text)
    assert validate_cues(cues, expected_end=300.0, max_gap=3.0) == []
```

- [ ] **Step 2: Run the new test module and verify import failure**

Run: `pytest tests/test_verify_demo_video.py -v`

Expected: collection ERROR because `scripts.verify_demo_video` does not exist.

- [ ] **Step 3: Implement the pure parsers**

Create these exact public functions in `scripts/verify_demo_video.py`:

```python
FREEZE_RE = re.compile(
    r"freeze_start:\s*(?P<start>[0-9.]+).*?"
    r"freeze_end:\s*(?P<end>[0-9.]+)\s*\|\s*freeze_duration:\s*(?P<duration>[0-9.]+)",
    re.S,
)


def parse_freezes(stderr: str) -> list[tuple[float, float, float]]:
    return [
        (float(m["start"]), float(m["end"]), float(m["duration"]))
        for m in FREEZE_RE.finditer(stderr)
    ]


def _timestamp_seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def parse_srt(text: str) -> list[tuple[float, float]]:
    pattern = re.compile(r"(?m)^(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})$")
    return [(_timestamp_seconds(a), _timestamp_seconds(b)) for a, b in pattern.findall(text)]


def validate_cues(cues: list[tuple[float, float]], expected_end: float, max_gap: float) -> list[str]:
    errors: list[str] = []
    if not cues:
        return ["자막 큐가 없습니다."]
    cursor = 0.0
    for start, end in cues:
        gap = start - cursor
        if gap >= max_gap:
            errors.append(f"{gap:.3f}초 자막 공백이 {cursor:.3f}초에서 시작합니다.")
        if end <= start:
            errors.append(f"자막 종료가 시작보다 늦지 않습니다: {start:.3f}-{end:.3f}")
        cursor = max(cursor, end)
    if abs(cursor - expected_end) > 0.05:
        errors.append(f"마지막 자막 종료 {cursor:.3f}초가 목표 {expected_end:.3f}초와 다릅니다.")
    return errors
```

- [ ] **Step 4: Implement FFmpeg probing, decode, freeze detection, and CLI**

Use `subprocess.run(..., capture_output=True, text=True, encoding="utf-8", errors="replace")` for three commands:

```text
ffmpeg -hide_banner -i VIDEO
ffmpeg -v error -i VIDEO -f null NUL
ffmpeg -hide_banner -i VIDEO -vf freezedetect=n=-50dB:d=3.0 -f null NUL
```

Parse the probe text for `Duration: 00:05:00.00`, `1920x1080`, and `60 fps`. Fail when any `parse_freezes()` item has duration `>= 3.0`. Read the SRT with `encoding="utf-8-sig"`, print every error on its own line, and return exit code 1 if any check fails.

- [ ] **Step 5: Run unit tests and CLI help**

Run: `pytest tests/test_verify_demo_video.py -v`

Expected: 3 passed.

Run: `python scripts/verify_demo_video.py --help`

Expected: exit 0 with required `--ffmpeg`, `--video`, and `--srt` arguments shown.

- [ ] **Step 6: Commit the verifier**

```powershell
git add scripts/verify_demo_video.py tests/test_verify_demo_video.py
git commit -m "test: verify portfolio video activity and captions"
```

### Task 5: Five-Minute Runbook, Captions, and Recording Checklist

**Files:**
- Create: `demo/video/active-recording-runbook.md`
- Replace: `demo/video/portfolio-demo-docker.ko.srt`
- Modify: `demo/video/docker-recording-checklist.md`
- Modify: `tests/test_docs_reference_integrity.py`

**Interfaces:**
- Consumes: the caption API and functional flows from Tasks 1–3.
- Produces: exact 300-second action schedule, UTF-8 subtitle track, synthetic naming rules, and evidence checklist used by the recording task.

- [ ] **Step 1: Write failing documentation integrity tests**

```python
def test_active_recording_runbook_covers_every_required_write_flow():
    text = (ROOT / "demo" / "video" / "active-recording-runbook.md").read_text(encoding="utf-8")
    for phrase in (
        "회원 등록", "설정 저장", "데이터셋 업로드", "테스트케이스 업로드",
        "QA 파이프라인 실행", "보고서 다운로드", "모니터링 대상 등록", "k6 실행",
        "게시글 등록", "댓글 등록", "선택 삭제", "VOC Improved", "사용자 승인",
        "역할 변경", "사용 중지", "허용 IP 등록", "허용 IP 삭제", "CREATE_COMPLETE",
    ):
        assert phrase in text


def test_docker_subtitle_track_ends_at_exactly_five_minutes():
    text = (ROOT / "demo" / "video" / "portfolio-demo-docker.ko.srt").read_text(encoding="utf-8-sig")
    assert "00:05:00,000" in text
```

- [ ] **Step 2: Run the documentation tests and verify failure**

Run: `pytest tests/test_docs_reference_integrity.py -k "active_recording or docker_subtitle" -v`

Expected: FAIL because the new runbook is absent and the current SRT ends after five minutes.

- [ ] **Step 3: Write the second-by-second runbook**

Create `demo/video/active-recording-runbook.md` with eight timed sections matching the approved design. For every action include:

```markdown
- `00:52.0–00:54.5` 화면: 실행 > 데이터셋 이력
  - 자막: `합성 정답 데이터셋을 업로드하고 활성 버전으로 전환합니다.`
  - 마우스: 업로드 버튼까지 0.6초 곡선 이동 → 0.3초 대기 → 클릭
  - 실제 동작: `qa_dataset.json` 선택, 업로드 완료 토스트 확인
  - 변화 제한: 파일명 표시까지 2.5초를 넘기면 대기 중간을 편집에서 제거
```

Use synthetic identifiers with prefix `video_demo_20260804_`. Include creation and cleanup pairs for user, post, comment, allowlist IP, and external monitor target. Specify a visual change at least every 2.5 seconds.

- [ ] **Step 4: Replace the SRT with exact five-minute coverage**

Write consecutive cues from `00:00:00,000` through `00:05:00,000`. Keep each cue between 1.5 and 4.0 seconds, use no gap of 3.0 seconds, and use two lines only when the second line reports a process/result. Include literal cues for `촬영용 합성 실행 · 외부 LLM 미호출` and `LLM Judge: SKIPPED`.

- [ ] **Step 5: Expand the recording checklist**

Add unchecked evidence items for all checkbox families, each write flow, human-speed cursor movement, maximum freeze duration, subtitle burn-in, SRT coverage, privacy crop, decode exit code, and final SHA-256. Keep preflight, recording, and final-evidence sections separate.

- [ ] **Step 6: Run documentation and verifier unit tests**

Run: `pytest tests/test_docs_reference_integrity.py tests/test_verify_demo_video.py -v`

Expected: all tests PASS.

- [ ] **Step 7: Commit the runbook and captions**

```powershell
git add demo/video/active-recording-runbook.md demo/video/portfolio-demo-docker.ko.srt demo/video/docker-recording-checklist.md tests/test_docs_reference_integrity.py
git commit -m "docs: script active five minute portfolio demo"
```

### Task 6: Functional Rehearsal Through the Visible UI

**Files:**
- Modify only if a rehearsal exposes a product defect; use the nearest existing test module for that feature.
- Evidence: `demo/video/docker-recording-checklist.md`

**Interfaces:**
- Consumes: running `qa-platform`, `prometheus`, and `grafana` containers plus the runbook.
- Produces: a clean, deterministic browser state in which every required UI action has succeeded before OBS recording starts.

- [ ] **Step 1: Run focused automated regressions before browser work**

Run:

```powershell
pytest tests/test_frontend_js_regression.py tests/test_board_api.py tests/test_users.py tests/test_ip_allowlist.py tests/test_external_monitor.py tests/test_monitoring_addon_api.py tests/test_voc_analysis_api.py -v
```

Expected: all selected tests PASS.

- [ ] **Step 2: Rebuild and verify the Docker stack**

Run:

```powershell
docker compose up -d --build
docker compose ps
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:9090/-/healthy -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:3000/api/health -UseBasicParsing
```

Expected: all three services report healthy and all HTTP requests return 200.

- [ ] **Step 3: Inspect every checkbox family in `/?demo=1`**

Use the connected browser to click selected and unselected states for settings categories, evaluation techniques, dataset rows, testcase rows, board rows, VOC sources, VOC A–D, and k6 options. After each click inspect the DOM `checked` property and capture a screenshot showing the matching visible mark. Record each family in the checklist.

- [ ] **Step 4: Rehearse all real write flows with synthetic data**

Follow `active-recording-runbook.md` once without OBS. Create a user through the registration form; approve it, change role, disable, and re-enable it as admin. Upload both QA files, run QA, download the report, create external monitoring target, run k6, create/edit/hide/show a post, add/delete a comment, select/delete a post, upload VOC, run VOC Improved, add/edit/delete an allowlist entry, and inspect the error log. Confirm each visible success toast is also copied to the process caption.

- [ ] **Step 5: Verify monitoring and AWS read-only screens**

Generate application traffic, then verify non-empty Prometheus and Grafana panels. Open the CloudFormation stack list, confirm `qa-platform-freetier` and `CREATE_COMPLETE`, and confirm the OBS crop excludes browser chrome and AWS account identifiers.

- [ ] **Step 6: Reset synthetic state for the actual take**

Delete rehearsal posts, comments, allowlist entries, monitor targets, uploaded duplicate files, and the rehearsal-only user through the supported UI or existing local admin endpoints. Do not delete non-prefixed records. Reset the demo step in session storage by exiting and reopening `/?demo=1`.

- [ ] **Step 7: Run the focused regressions again after any defect fix**

Run the exact command from Step 1 plus the nearest test module for every edited file.

Expected: all selected tests PASS before recording.

### Task 7: OBS Recording and Five-Minute Post-Production

**Files:**
- Replace: `demo/video/portfolio-demo-docker.mp4`
- Preserve: raw OBS recordings in `C:\Users\DWIT\Videos`

**Interfaces:**
- Consumes: rehearsed UI state, exact runbook, SRT, connected Chrome, OBS WebSocket, and FFmpeg.
- Produces: final captioned MP4 with real UI actions and privacy-safe AWS segment.

- [ ] **Step 1: Configure the recording surface**

Set Chrome to 1920×1080, 100% zoom, with only the app and AWS tabs needed for the take. Set OBS to 1920×1080, 60fps, monitor capture, MP4 output, and WebSocket port 4455 with authentication disabled for this local session. Confirm OBS reports `outputActive=false` before starting.

- [ ] **Step 2: Record the active application take**

Start OBS over WebSocket and follow the runbook. Use 0.4–0.8-second curved mouse movements and 0.2–0.4-second pre-click pauses. Keep the pointer moving toward the next described control while processes run. Stop and restart the take if a real action fails, a checkbox mark is invisible, a caption reports the wrong result, or sensitive data becomes visible.

- [ ] **Step 3: Record the AWS privacy-safe clip**

Apply the verified OBS crop before switching to CloudFormation. Hold the `qa-platform-freetier` and `CREATE_COMPLETE` row for the final ten seconds while moving the cursor slowly across the stack name, status, timestamp, and description. Stop OBS, store the returned output path, then restore crop and scale to the normal full-screen transform.

- [ ] **Step 4: Remove waits and compose exactly 300 seconds**

Use FFmpeg `trim`, `setpts`, `atrim`, and `atempo` filters to remove or speed only waiting intervals. Preserve clicks, form changes, progress starts, completion states, and success toasts at normal speed. Concatenate the application and AWS segments and trim the composed timeline to exactly `300.000` seconds.

- [ ] **Step 5: Burn in the Korean subtitles**

Render the SRT with a Windows Korean font and safe lower-third margins:

```powershell
ffmpeg -y -i composed.mp4 -vf "subtitles=demo/video/portfolio-demo-docker.ko.srt:fontsdir='C\:/Windows/Fonts':force_style='FontName=Malgun Gothic,FontSize=22,Outline=2,Shadow=0,MarginV=46,Alignment=2'" -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -c:a aac -b:a 160k -movflags +faststart demo/video/portfolio-demo-docker.new.mp4
```

Do not overwrite the current deliverable until `portfolio-demo-docker.new.mp4` passes Task 8.

### Task 8: Final Functional, Visual, and Media Verification

**Files:**
- Replace after verification: `demo/video/portfolio-demo-docker.mp4`
- Modify: `demo/video/docker-recording-checklist.md`

**Interfaces:**
- Consumes: `portfolio-demo-docker.new.mp4`, SRT, verifier, and approved design requirements.
- Produces: evidence-backed final MP4 and completed checklist.

- [ ] **Step 1: Run full product regressions**

Run: `pytest -q`

Expected: exit 0 with no failures.

- [ ] **Step 2: Run the full video verifier**

Run:

```powershell
python scripts/verify_demo_video.py --ffmpeg "$ffmpegPath" --video demo/video/portfolio-demo-docker.new.mp4 --srt demo/video/portfolio-demo-docker.ko.srt
```

Expected: exit 0, duration `300.000`, `1920x1080`, `60 fps`, decode OK, zero freezes `>= 3.000s`, and SRT coverage OK.

- [ ] **Step 3: Extract and inspect evidence frames**

Extract frames immediately before and after every checkbox click, registration, upload, execution start/completion, deletion, the `SKIPPED` result, Prometheus/Grafana charts, and AWS status. Inspect them at original resolution. Reject the video if a check mark, toast, caption, result, chart, or privacy crop cannot be read.

- [ ] **Step 4: Review the complete video in real time**

Play all five minutes once with audio enabled. Confirm cursor movement feels human rather than instantaneous, captions match the visible action, no transition feels stalled, and no external or private data appears.

- [ ] **Step 5: Promote the verified artifact**

Move `portfolio-demo-docker.new.mp4` over `portfolio-demo-docker.mp4` only after Steps 1–4 pass. Record file size, duration, resolution, frame rate, FFmpeg decode exit code, maximum detected freeze duration, and SHA-256 in the checklist.

- [ ] **Step 6: Confirm OBS and Docker final state**

Query OBS and confirm recording is stopped. Leave the Docker stack running only if the user is continuing the demo session; otherwise stop only the three project containers with `docker compose stop` after reporting the change.

- [ ] **Step 7: Commit source and checklist evidence**

```powershell
git add demo/video/docker-recording-checklist.md
git commit -m "docs: record verified active demo evidence"
```

Do not add raw OBS recordings. Do not add the MP4 unless the repository's existing large-file policy explicitly permits it.
