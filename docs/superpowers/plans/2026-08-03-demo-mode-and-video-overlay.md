# Demo Mode and Video Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe guided browser demo with click highlighting, captions, controls, privacy masking, and matching five-minute SRT assets for OBS recording.

**Architecture:** A standalone static JS module activates only when `demo=1` is present, owns step state in `sessionStorage`, and injects its overlay without changing application APIs. A standalone stylesheet provides highlighting, caption, controls, click ripple, and masking; both main and monitoring-addon templates load the same assets.

**Tech Stack:** Vanilla JavaScript, CSS, Jinja/HTML templates, Node VM regression tests, pytest, SRT, OBS WebSocket

## Global Constraints

- Never auto-click uploads, API executions, settings saves, or destructive controls.
- Never read secret field values; mask with CSS only.
- Demo UI is absent unless `demo=1` is in the URL.
- Preserve the step across page navigation with `sessionStorage`.
- Label the VOC fallback as a pre-generated synthetic result and Judge `SKIPPED`.
- Keep the subtitle timeline at or below five minutes.

---

### Task 1: Add failing demo-mode wiring tests

**Files:**
- Modify: `tests/test_frontend_js_regression.py`
- Create: `tests/js/demo_mode_regression.js`

**Interfaces:**
- Consumes: `app/static/demo-mode.js`, `index.html`, `monitoring_addon.html`
- Produces: regression gates for opt-in activation, safe steps, masking selectors, controls, and shared template wiring

- [ ] Add a pytest test asserting both templates load `/static/demo-mode.css?v=1` and `/static/demo-mode.js?v=1`.
- [ ] Add a pytest wrapper that runs `node tests/js/demo_mode_regression.js` and requires `0 failed`.
- [ ] In the Node script, load `demo-mode.js` in a VM with DOM/sessionStorage stubs and verify: no initialization without `demo=1`; initialization with `demo=1`; 12 steps; risky steps have `pauseForAction: true`; masking selectors include secret inputs and `#my-ip-display`; next/previous clamp; stored step restoration; VOC fallback copy includes `사전 생성` and `SKIPPED`.
- [ ] Run `python -m pytest tests/test_frontend_js_regression.py -q` and confirm failure because assets and wiring do not exist.
- [ ] Commit with `test: specify guided demo mode behavior`.

### Task 2: Implement the guided browser demo

**Files:**
- Create: `app/static/demo-mode.css`
- Create: `app/static/demo-mode.js`
- Modify: `app/templates/index.html`
- Modify: `app/templates/monitoring_addon.html`

**Interfaces:**
- Produces: `window.QADemoMode` with `start()`, `stop()`, `next()`, `previous()`, `togglePlayback()`, `goTo(index)`, and read-only `steps`
- Persists: `qa-demo-step` and `qa-demo-playing` in `sessionStorage`

- [ ] Implement CSS classes `.qa-demo-active`, `.qa-demo-highlight`, `.qa-demo-caption`, `.qa-demo-controls`, `.qa-demo-mask`, and `.qa-demo-ripple`, including reduced-motion behavior.
- [ ] Implement 12 declarative steps with stable selectors already present in the templates.
- [ ] Implement URL gating, overlay injection, current-target highlighting, safe scrolling, step counter, previous/play-next/exit controls, and timer cancellation.
- [ ] Implement capture-phase click ripple without canceling or replacing the application click.
- [ ] Apply masking to password inputs, Jira URL/email/token fields, webhook fields, and `#my-ip-display`; never access their values.
- [ ] Preserve `demo=1` and current step in addon/main navigation links.
- [ ] Load versioned CSS and deferred JS in both templates.
- [ ] Run `python -m pytest tests/test_frontend_js_regression.py tests/test_ui_and_env_docs.py tests/test_reporting_and_ui.py -q` and confirm pass.
- [ ] Commit with `feat: add guided portfolio demo mode`.

### Task 3: Create video subtitle and overlay timeline assets

**Files:**
- Create: `demo/video/portfolio-demo.ko.srt`
- Create: `demo/video/overlay-timeline.md`
- Modify: `demo/video/recording-checklist.md`

**Interfaces:**
- Consumes: approved 5-minute script and 12 demo steps
- Produces: editor-ready Korean subtitles and an OBS operator timeline

- [ ] Write sequential SRT cues covering 00:00:00,000 through no later than 00:05:00,000.
- [ ] Include explicit `사전 생성 합성 데모 결과` wording for the VOC fallback cue.
- [ ] Map every SRT cue to a demo step, target screen, click action, and OBS note in `overlay-timeline.md`.
- [ ] Add checklist items for enabling `?demo=1`, verifying masks, starting OBS, recording the 12 steps, stopping OBS, and restoring WebSocket authentication.
- [ ] Validate cue sequence and end time with a small inline Python parser.
- [ ] Commit with `docs: add demo subtitles and overlay timeline`.

### Task 4: Browser verification and OBS capture

**Files:**
- Modify: `demo/video/recording-checklist.md`
- Output: OBS-configured recording directory, filename reported by `GetRecordStatus`

**Interfaces:**
- Consumes: `/?demo=1`, synthetic demo files, OBS WebSocket on localhost:4455
- Produces: one recorded video file and a checked capture record

- [ ] Start the isolated app and open `http://127.0.0.1:8000/?demo=1`.
- [ ] Verify at 1920×1080 that controls render, target highlight advances, click ripple appears, and sensitive fields/IP are unreadable.
- [ ] Verify main/addon navigation preserves demo mode and the current step.
- [ ] Query OBS `GetRecordStatus`; only start when not already recording.
- [ ] Send `StartRecord`, perform the 12-step browser sequence, then send `StopRecord`.
- [ ] Query the final output path and confirm the file exists and has nonzero size.
- [ ] Record duration, file path, fallback usage, and privacy audit in the checklist.
- [ ] Run `python -m pytest tests/test_frontend_js_regression.py tests/test_ui_and_env_docs.py tests/test_reporting_and_ui.py -q`.
- [ ] Commit the final checklist with `docs: record guided demo capture results`.
