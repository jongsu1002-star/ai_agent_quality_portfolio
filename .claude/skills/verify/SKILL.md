---
name: verify
description: Build/launch/drive recipe for AI Agent Quality Platform (FastAPI + vanilla-JS dashboard). Use to verify runtime behavior end-to-end via a real browser.
---

# Verify recipe: AI Agent Quality Platform

Single FastAPI app (`app/main.py`) serving a self-contained HTML dashboard (`app/templates/index.html`, no build step, no external JS deps). No CI config in this repo; no upstream git remote (verify uncommitted state = whatever is on disk).

## Launch

```bash
# free the port if a previous run is still bound (Windows/Git Bash)
netstat -ano 2>/dev/null | grep "LISTENING" | grep ":8000" | awk '{print $5}' | sort -u | while read pid; do taskkill //F //PID "$pid"; done

# clean cross-run state that pollutes ACTIVE_DATASET / "latest run" between verify passes
rm -f reports/cases.json reports/.active_dataset.json

(.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp_uvicorn.log 2>&1 &)
timeout 30 bash -c 'until curl -sf http://127.0.0.1:8000/health >/dev/null; do sleep 1; done' && echo "SERVER UP"
```

`load_dotenv()` runs at import time, so the live app (unlike pytest, which strips secrets via `conftest.py`'s autouse fixture) uses the **real** `.env` keys -- an `llm_quality` run against OpenAI will make a real network call. As of this writing the `OPENAI_API_KEY` in `.env` returns `401 Unauthorized` -- that's an environment fact, not a code bug; evaluators still degrade gracefully (`errored: true`) so pipeline runs complete regardless.

## Drive (Playwright / chromium-cli)

No `chromium-cli` binary in this environment; use `playwright` via Node (already installed once this session: `npx playwright install chromium`).

```js
const { chromium } = require('playwright');
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1300, height: 1000 } });
await page.goto('http://127.0.0.1:8000/', { waitUntil: 'networkidle' });
```

Kill the server and re-run after any `app/templates/index.html` or `app/main.py` change -- templates are read fresh per-request, but a running uvicorn process must still be current code (restart after `.py` edits; template edits alone don't need a restart, only a page reload).

## Surfaces worth driving

- **3 tabs**: `button[data-tab="settings"|"run"|"dashboard"]`, panel `#tab-<name>` gets class `active`.
- **설정 tab**: LLM provider select `#llm-provider` (`openai`/`anthropic`/`custom`/`none`) toggles `#llm-openai-fields`/`#llm-anthropic-fields`/`#llm-custom-fields`; password reveal via `.password-toggle` buttons (flips `input[type]` password↔text); doc viewer `[data-doc="user_manual"|"design_spec"|"process_spec"|"network_guide"]` renders `GET /api/docs/{key}` through a hand-rolled markdown-to-HTML renderer in the page's own `<script>` (watch for the escaped-pipe-in-table-cell class of bug: `\|` inside a code span).
- **실행 tab**: `#file-run` + button `테스트 케이스 업로드` uploads a dataset (JSON/xlsx); `엑셀 템플릿 다운로드` hits `/api/dataset/template`; `QA 파이프라인 실행` posts `/api/run` then polls `/api/run/{id}/status` until `done`/`error`, table id `#run-result` holds the JSON report.
- **대시보드 tab**: 5 SVG charts (`#chart-timeseries`, `#chart-category-bar`, `#chart-scatter`, `#chart-bubble`, `#chart-radar`); clicking any (`.chart-clickable`) opens `#chart-modal` with a cloned, enlarged SVG; Esc or clicking the overlay background closes it.
- **API surface directly** (`page.evaluate(() => fetch(...))` or `curl`) for endpoints with no dedicated UI probe path: `/api/docs/{key}` (404 for unknown key), `/api/runs/{run_id}` (path-traversal guarded).

## Gotchas hit so far

- `RUN_REGISTRY` and `list_run_history()` share the on-disk `reports/` dir across every process launch in this session -- run IDs keep incrementing across restarts (file `run_run_7.json` after 7 cumulative runs, not 1). Don't assume `run_1` after a fresh launch.
- Multipart file upload via `curl -F "file=@/tmp_x.json"` intermittently returns connect-fail (`HTTP_CODE:000`) on this Windows/Git-Bash host even though the server is up (`/health` succeeds) -- copy the file into the project dir and reference it with a relative path instead of `/tmp/...`.
- Clean up `reports/cases.json` / `reports/.active_dataset.json` after driving uploads -- otherwise the *next* verify pass (or the next real user) inherits a stale "active dataset".
