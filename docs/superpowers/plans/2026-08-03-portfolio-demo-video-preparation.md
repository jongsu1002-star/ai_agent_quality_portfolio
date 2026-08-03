# Portfolio Demo Video Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce validated demo data, a verified local runtime, and a timed rehearsal checklist for recording the approved five-minute portfolio video.

**Architecture:** Keep recording-only assets under `demo/video/` so they cannot be confused with production or test fixtures. Validate the three JSON inputs independently, then verify application health and upload flows before running one timed rehearsal against the approved script.

**Tech Stack:** JSON, PowerShell, Python 3.12, FastAPI/Uvicorn, pytest, k6, Markdown

## Global Constraints

- The final recording target is 4 minutes 45 seconds to 5 minutes 10 seconds at 1920×1080.
- Use only synthetic data and a dedicated demo account.
- Never expose API keys, webhooks, user email addresses, contact details, or internal network addresses.
- QA and VOC execution each have a 20-second live wait budget before switching to a pre-generated result.
- Run k6 only against `http://localhost:8000/health`, with a 10-second duration and low concurrency.
- Do not modify or delete existing user datasets, run history, VOC history, or unrelated working-tree changes.

---

### Task 1: Create and validate recording data

**Files:**
- Create: `demo/video/qa_dataset.json`
- Create: `demo/video/qa_testcases.json`
- Create: `demo/video/voc_samples.json`

**Interfaces:**
- Consumes: dataset schema from `qa_agent/excel_io.py::load_dataset`, testcase schema from `qa_agent/excel_io.py::load_testcase`, and VOC schema from `qa_agent/excel_io.py::load_voc_json`
- Produces: three upload-ready JSON arrays used by the browser rehearsal

- [ ] **Step 1: Create the QA reference dataset**

Create `demo/video/qa_dataset.json` with exactly this content:

```json
[
  {
    "id": "TC-001",
    "category": "ACC",
    "question": "수강료는 얼마인가요?",
    "golden_answer": "수강료는 월 10만원입니다.",
    "existing_answer": "수강료는 월 10만원입니다.",
    "required_keywords": ["10만원"]
  },
  {
    "id": "TC-002",
    "category": "REG",
    "question": "환불은 언제까지 가능한가요?",
    "golden_answer": "수업 시작 7일 전까지 전액 환불할 수 있습니다.",
    "existing_answer": "수업 시작 7일 전까지 전액 환불할 수 있습니다.",
    "required_keywords": ["7일 전", "전액 환불"]
  },
  {
    "id": "TC-003",
    "category": "RPT",
    "question": "필요한 제출 서류를 알려주세요.",
    "golden_answer": "신청서와 신분증 사본을 제출해야 합니다.",
    "existing_answer": "신청서와 신분증 사본을 제출해야 합니다.",
    "required_keywords": ["신청서", "신분증 사본"]
  }
]
```

- [ ] **Step 2: Create the QA utterance file**

Create `demo/video/qa_testcases.json` with exactly this content:

```json
[
  {"id": "TC-001", "question": "수강료는 얼마인가요?"},
  {"id": "TC-002", "question": "환불은 언제까지 가능한가요?"},
  {"id": "TC-003", "question": "필요한 제출 서류를 알려주세요."}
]
```

- [ ] **Step 3: Create the VOC import file**

Create `demo/video/voc_samples.json` with exactly this content:

```json
[
  {"source": "고객센터", "date": "2026-08-01", "category": "상담", "content": "상담 연결까지 너무 오래 걸렸습니다."},
  {"source": "웹 문의", "date": "2026-08-01", "category": "환불", "content": "환불 절차 안내가 화면마다 달라 혼란스럽습니다."},
  {"source": "앱 피드백", "date": "2026-08-02", "category": "서류", "content": "답변은 빨랐지만 필요한 제출 서류가 누락됐습니다."},
  {"source": "고객센터", "date": "2026-08-03", "category": "일관성", "content": "같은 질문을 다시 하니 이전과 다른 답변을 받았습니다."}
]
```

- [ ] **Step 4: Validate JSON syntax and schemas**

Run:

```powershell
Get-Content -Raw -Encoding UTF8 demo/video/qa_dataset.json | ConvertFrom-Json | Out-Null
Get-Content -Raw -Encoding UTF8 demo/video/qa_testcases.json | ConvertFrom-Json | Out-Null
Get-Content -Raw -Encoding UTF8 demo/video/voc_samples.json | ConvertFrom-Json | Out-Null
python -m pytest tests/test_excel_io.py tests/test_voc_analysis_api.py -q
```

If `tests/test_excel_io.py` is absent, run this existing loader-focused set instead:

```powershell
python -m pytest tests/test_ui_config_and_exports.py tests/test_voc_analysis_api.py -q
```

Expected: all three `ConvertFrom-Json` commands exit without output and the selected pytest files pass.

- [ ] **Step 5: Commit the recording data**

```powershell
git add demo/video/qa_dataset.json demo/video/qa_testcases.json demo/video/voc_samples.json
git commit -m "docs: add synthetic portfolio demo data"
```

### Task 2: Verify the local recording runtime

**Files:**
- Inspect: `.env`
- Inspect: `scripts/start_platform.py`
- Inspect: `app/main.py`
- Inspect: `tests/k6/load_test.js`

**Interfaces:**
- Consumes: application port 8000, health endpoint `/health`, optional Prometheus port 9090, and Grafana port 3000
- Produces: a running application and a written pass/fail inventory for the rehearsal checklist

- [ ] **Step 1: Check dependencies without printing secrets**

Run:

```powershell
.venv\Scripts\python.exe --version
.venv\Scripts\python.exe -c "import fastapi, uvicorn, pandas, openpyxl; print('python dependencies: OK')"
Get-Command k6 -ErrorAction SilentlyContinue | Select-Object Name,Source
docker compose ps
```

Expected: Python is 3.12 or newer, imports succeed, and the output establishes whether k6 and Docker services are available. Do not print `.env`.

- [ ] **Step 2: Run the fast application test set**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_api_features.py tests/test_ui_config_and_exports.py tests/test_voc_analysis_api.py tests/test_monitoring_addon_api.py -q
```

Expected: all selected tests pass before opening the recording browser.

- [ ] **Step 3: Start the platform if it is not already running**

Run in a dedicated terminal:

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: Uvicorn reports that it is running on `http://127.0.0.1:8000` without a startup traceback.

- [ ] **Step 4: Verify health and page availability**

Run:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
(Invoke-WebRequest http://127.0.0.1:8000/ -UseBasicParsing).StatusCode
(Invoke-WebRequest http://127.0.0.1:8000/monitoring-addon -UseBasicParsing).StatusCode
```

Expected: health reports a healthy status and both pages return HTTP 200, or the main page returns a documented authentication redirect when accounts are enabled.

- [ ] **Step 5: Verify optional monitoring services**

Run:

```powershell
Test-NetConnection 127.0.0.1 -Port 9090 -InformationLevel Quiet
Test-NetConnection 127.0.0.1 -Port 3000 -InformationLevel Quiet
```

Expected: record each result as available or unavailable. Unavailable services trigger the approved fallback of showing built-in charts only; they do not block recording.

### Task 3: Create the operator checklist and shot sheet

**Files:**
- Create: `demo/video/recording-checklist.md`
- Reference: `docs/superpowers/specs/2026-08-03-portfolio-demo-video-design.md`

**Interfaces:**
- Consumes: validated files from Task 1 and runtime results from Task 2
- Produces: the single document kept beside the recorder during rehearsal and final capture

- [ ] **Step 1: Add the privacy and desktop setup checklist**

The document must include unchecked items for: dedicated demo account, synthetic data only, hidden bookmarks bar, disabled notifications, disabled password manager overlays, cleared downloads tray, masked keys/webhooks/email/contact/IP values, 1920×1080 capture, and browser zoom between 110% and 125% without clipped controls.

- [ ] **Step 2: Add the exact upload and run sequence**

List these paths and actions in order:

1. `설정` → upload `demo/video/qa_dataset.json`.
2. `실행` → upload `demo/video/qa_testcases.json`.
3. Select `rag` and `llm_quality`; select `regression` only after a successful timed rehearsal.
4. Run the QA pipeline and start the 20-second fallback timer.
5. Open `대시보드`, one `표로 보기`, report downloads, and execution history.
6. `VOC 분석` → enable file upload → upload `demo/video/voc_samples.json` → enter the approved focus sentence → run and start the 20-second fallback timer.
7. Open `VOC 분석 결과`, `모니터링`, and `/monitoring-addon`.
8. Configure k6 for `http://localhost:8000`, `/health`, low VUs, and `10초`.
9. Sweep administrator pages and return to the dashboard.

- [ ] **Step 3: Add a timestamped shot checklist**

Copy the ten approved time windows from the design spec and put an unchecked box beside each scene. Add a cumulative checkpoint at 2:45 and 4:30 so the operator can detect drift before the ending.

- [ ] **Step 4: Add explicit fallback decisions**

Document these decisions verbatim:

- At 20 seconds without QA completion: show the live progress state, say that execution continues asynchronously, and open the pre-generated execution from history.
- At 20 seconds without VOC completion: show the cancel control, then open the pre-generated VOC result from history.
- If k6 is unavailable: show the configured 10-second run form and the latest stored result without clicking Run.
- If Grafana or Prometheus is unavailable: do not scroll to an error panel; show the built-in monitoring charts and explain the optional integration verbally.

- [ ] **Step 5: Commit the checklist**

```powershell
git add demo/video/recording-checklist.md
git commit -m "docs: add portfolio recording checklist"
```

### Task 4: Perform and certify one full rehearsal

**Files:**
- Modify: `demo/video/recording-checklist.md`

**Interfaces:**
- Consumes: the running platform, three validated demo files, approved narration, and the operator checklist
- Produces: a checked rehearsal record with measured duration and resolved fallback choices

- [ ] **Step 1: Generate fallback results before the timed rehearsal**

Upload the three assets and complete one QA run and one VOC run. Confirm both appear in their respective history screens. Do not delete older user data or histories.

- [ ] **Step 2: Reset only transient browser state**

Return to the settings tab, close modals, collapse advanced VOC controls, stop active polling by allowing runs to finish, and ensure no password field contains visible text. Do not reset the application database or active user datasets.

- [ ] **Step 3: Record a scratch rehearsal**

Run the complete sequence once with a stopwatch and spoken narration. Record the start time, finish time, QA live wait, VOC live wait, and whether each fallback was used in `demo/video/recording-checklist.md`.

- [ ] **Step 4: Apply deterministic timing corrections**

If longer than 5:10, remove scrolling from settings first, then omit the Jira ticket panel, then show only one dashboard chart. If shorter than 4:45, spend the extra time on the QA progress stages and failed-case detail. Do not add new features or screens.

- [ ] **Step 5: Run the final privacy frame audit**

Scrub the scratch recording at the settings screen, top-right account area, monitoring URLs, file chooser, and download tray. Mark the audit failed if any secret, personal detail, or internal address is readable for even one frame.

- [ ] **Step 6: Certify readiness**

Mark recording-ready only when the duration is 4:45–5:10, all ten scene checkboxes pass, QA and VOC fallbacks exist, pages render at 1920×1080 without clipped controls, and the privacy audit passes.

- [ ] **Step 7: Commit the rehearsal record**

```powershell
git add demo/video/recording-checklist.md
git commit -m "docs: record portfolio video rehearsal"
```
