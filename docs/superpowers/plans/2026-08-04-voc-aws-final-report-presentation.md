# VOC Improve × AWS Final Report Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the approved PDF-based final report into a polished, editable 22-slide PowerPoint whose facts match the current repository and whose Korean text renders without corruption.

**Architecture:** Use the original PPTX as the editable template source and the PDF as the visual/content evidence source. Inspect every source slide with the presentation template-following tools, duplicate mapped source slides into a starter deck, edit inherited elements with `@oai/artifact-tool`, then render and validate every final slide before delivery.

**Tech Stack:** Node.js ES modules, `@oai/artifact-tool`, presentation template-following scripts, PyMuPDF/pypdf for PDF inspection, PowerPoint PPTX, pytest repository evidence.

## Global Constraints

- Preserve both source files unchanged:
  - `C:/Users/DWIT/Desktop/교육/8월/4일_48차/VOC_Improve_AWS_확장_최종완료보고_2조.pptx`
  - `C:/Users/DWIT/Desktop/교육/8월/4일_48차/VOC_Improve_AWS_확장_최종완료보고_2조.pdf`
- Produce only `C:/ai_agent_quality_portfolio/output/presentations/VOC_Improve_AWS_확장_최종완료보고_2조_완성본.pptx` outside the temporary directory.
- Keep all intermediate files under `C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/`.
- Use 22 slides, 16:9, Malgun Gothic, minimum 36pt slide titles, 24pt subheads, and 16pt body text.
- Use the approved palette: `#F5F7FA`, `#0F172A`, `#2563EB`, `#16A34A`, `#F97316`, and `#DC2626`.
- State `650/650 PASS`, `0 FAIL`, `100%`, and `LLM Judge SKIPPED`; never present SKIPPED as PASS.
- State the release decision as conditional staging readiness, with full production rollout held until independent Judge validation.
- Remove authoring instructions, blank lines, blank checkboxes, empty capture frames, and sample-only wording.
- Remove or redact account IDs, emails, IP addresses, bucket identifiers, and credentials.
- Do not invent E2E timing, Judge scores, cost savings, or external outcomes.
- Use `@oai/artifact-tool`; do not use python-pptx or direct OOXML mutation.
- If the bundled artifact runtime is unavailable, stop at the preflight gate and report the exact missing path; do not fall back to a flattened or theme-matched rebuild.

---

### Task 1: Runtime preflight and complete source inventory

**Files:**
- Read: `C:/Users/DWIT/Desktop/교육/8월/4일_48차/VOC_Improve_AWS_확장_최종완료보고_2조.pptx`
- Read: `C:/Users/DWIT/Desktop/교육/8월/4일_48차/VOC_Improve_AWS_확장_최종완료보고_2조.pdf`
- Create: `.pptx-work/voc-final-report/template-audit.txt`
- Create: `.pptx-work/voc-final-report/source-notes.txt`
- Create: `.pptx-work/voc-final-report/deviation-log.txt`

**Interfaces:**
- Consumes: source PPTX, source PDF, presentation skill directory.
- Produces: initialized artifact workspace, complete source renders/layout inventory, and a written template audit used by all later tasks.

- [ ] **Step 1: Verify both sources and initialize the artifact workspace**

Run:

```powershell
$deck = 'C:/Users/DWIT/Desktop/교육/8월/4일_48차/VOC_Improve_AWS_확장_최종완료보고_2조.pptx'
$pdf = 'C:/Users/DWIT/Desktop/교육/8월/4일_48차/VOC_Improve_AWS_확장_최종완료보고_2조.pdf'
$work = 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report'
Get-Item -LiteralPath $deck, $pdf
node 'C:/Users/DWIT/.codex/plugins/cache/openai-primary-runtime/presentations/26.730.11710/skills/presentations/container_tools/setup_artifact_tool_workspace.mjs' --workspace $work
```

Expected: both files exist and setup exits 0. If `@oai/artifact-tool/package.json` is missing, stop and report the blocker.

- [ ] **Step 2: Read the required artifact-tool and template APIs completely**

Read:

```text
artifact_tool_docs/API_QUICK_START.md
artifact_tool_docs/api/API_DOCS.md
artifact_tool_docs/api/references/master.spec.md
artifact_tool_docs/api/references/layout.spec.md
artifact_tool_docs/api/references/inspect.md
artifact_tool_docs/api/references/cookbook/imported-deck.md
```

- [ ] **Step 3: Inspect every source PPTX slide**

Run:

```powershell
node 'C:/Users/DWIT/.codex/plugins/cache/openai-primary-runtime/presentations/26.730.11710/skills/presentations/template_following_scripts/inspect_template_deck.mjs' `
  --workspace 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report' `
  --pptx 'C:/Users/DWIT/Desktop/교육/8월/4일_48차/VOC_Improve_AWS_확장_최종완료보고_2조.pptx'
```

Expected: 28 source slide renders, per-slide layout JSON, `template-inspect.ndjson`, extracted media, font evidence, and `template-manifest.json`.

- [ ] **Step 4: Compare all 28 PPTX renders with all 28 PDF renders**

Record in `template-audit.txt`:

```text
Source size: 16:9, 960x540 PDF points
Reusable patterns: cover, four-metric summary, two-column evidence, process flow, table, command/evidence split, conclusion
Typography: Malgun Gothic; dark navy headings; gray body; colored left rail
Known source defects: blank timing values, blank QA numbers, blank S3 actual-result cells, blank video URL, page sequence 26/28/29, Cam/oCam inconsistency
Required preserved assets: authentic AWS console captures, local test captures, original color rails
```

- [ ] **Step 5: Record source provenance and intentional deviations**

Write `source-notes.txt` with the source PDF/PPTX paths and repository documents. Write `deviation-log.txt` with the approved consolidation from 28 to 22 slides, removal of authoring prompts, sensitive-data redaction, and Judge correction.

---

### Task 2: Evidence-backed Korean content manifest

**Files:**
- Create: `.pptx-work/voc-final-report/content.json`
- Create: `.pptx-work/voc-final-report/validate-content.mjs`
- Read: `docs/테스트_결과.md`
- Read: `docs/사용자_매뉴얼.md`
- Read: `docs/프로세스_명세서.md`
- Read: `docs/AWS_배포_운영_매뉴얼.md`

**Interfaces:**
- Consumes: approved 22-slide design and repository evidence.
- Produces: UTF-8 `content.json` with one title, claim, body/evidence blocks, status, and source list for each of 22 slides.

- [ ] **Step 1: Write a failing manifest validator**

Create `validate-content.mjs` that loads `content.json` and fails unless:

```javascript
assert.equal(content.slides.length, 22);
assert.deepEqual(content.metrics, { total: 650, passed: 650, failed: 0, passRate: 100 });
assert.equal(content.judge.verdict, "SKIPPED");
assert.equal(content.judge.gate, "UNVERIFIED");
assert.equal(content.slides.at(-1).releaseDecision, "CONDITIONAL_STAGING");
assert.ok(!JSON.stringify(content).match(/수치 입력|캡처 삽입|_{3,}|□|oCam|Cam 영상/));
assert.ok(!JSON.stringify(content).match(/AKIA[0-9A-Z]{16}|\b\d{12}\b|\.env|api[_ -]?key/i));
```

- [ ] **Step 2: Run the validator and confirm it fails because `content.json` is missing**

Run:

```powershell
node .pptx-work/voc-final-report/validate-content.mjs
```

Expected: non-zero exit stating that `content.json` is missing.

- [ ] **Step 3: Create the complete UTF-8 Korean content manifest**

Use the exact 22-slide sequence in the approved design. The final verdict copy must be:

```text
합성 데이터 기반 QA와 AWS 운영 경로는 검증 완료. 전체 테스트 650건은 모두 통과했으나 외부 LLM Judge가 SKIPPED이므로 전면 운영 배포는 보류하고 조건부 스테이징 가능으로 판정한다.
```

Slide 8 must distinguish:

```text
결정론적 검증: PASS
독립 LLM Judge: SKIPPED
품질 게이트: UNVERIFIED
```

- [ ] **Step 4: Run the validator and confirm all content constraints pass**

Run:

```powershell
node .pptx-work/voc-final-report/validate-content.mjs
```

Expected: `PASS: 22 slides, verified facts, no authoring placeholders or sensitive tokens`.

---

### Task 3: Template frame map and starter deck

**Files:**
- Create: `.pptx-work/voc-final-report/template-frame-map.json`
- Create: `.pptx-work/voc-final-report/template-starter.pptx`
- Create: `.pptx-work/voc-final-report/template-starter-preview/`
- Create: `.pptx-work/voc-final-report/template-starter-layout/`

**Interfaces:**
- Consumes: `template-inspect.ndjson`, source element IDs, and `content.json`.
- Produces: validated 22-slide starter deck whose every slide is duplicated from a suitable source slide.

- [ ] **Step 1: Map every output slide to a source slide**

Use this narrative mapping, then resolve each `editTargets` entry to actual IDs from `template-inspect.ndjson`:

```json
{
  "sourceSlides": [1, 2, 4, 6, 7, 8, 9, 10, 10, 11, 12, 13, 17, 18, 19, 20, 24, 21, 27, 26, 25, 28]
}
```

Classify every inherited element as `keep`, `rewrite`, `replace`, or `delete`. Explicitly delete inherited empty title/body/footer/date/slide-number placeholders that will not be filled.

- [ ] **Step 2: Validate the frame map**

Run the validation built into `prepare_template_starter_deck.mjs` and fix unresolved IDs before continuing. No `action: "add"` may be used to bypass a source placeholder.

- [ ] **Step 3: Build the starter deck**

Run:

```powershell
node 'C:/Users/DWIT/.codex/plugins/cache/openai-primary-runtime/presentations/26.730.11710/skills/presentations/template_following_scripts/prepare_template_starter_deck.mjs' `
  --workspace 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report' `
  --pptx 'C:/Users/DWIT/Desktop/교육/8월/4일_48차/VOC_Improve_AWS_확장_최종완료보고_2조.pptx' `
  --map 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/template-frame-map.json' `
  --out 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/template-starter.pptx' `
  --preview-dir 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/template-starter-preview' `
  --layout-dir 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/template-starter-layout' `
  --contact-sheet 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/template-starter-contact-sheet.png'
```

Expected: 22-slide editable starter deck and zero unresolved edit targets.

---

### Task 4: Edit inherited slide elements with artifact-tool

**Files:**
- Create: `.pptx-work/voc-final-report/build-deck.mjs`
- Create: `.pptx-work/voc-final-report/edit-ledger.json`
- Create: `output/presentations/VOC_Improve_AWS_확장_최종완료보고_2조_완성본.pptx`

**Interfaces:**
- Consumes: `template-starter.pptx`, `content.json`, `template-frame-map.json`.
- Produces: final editable PPTX and an edit ledger listing every rewritten, replaced, deleted, or redacted inherited object.

- [ ] **Step 1: Import the starter deck and inspect masters/layouts**

Use:

```javascript
const presentation = await PresentationFile.importPptx(
  await FileBlob.load(starterPath)
);
const layoutInspection = presentation.inspect({ kind: "layout" });
```

Confirm the imported slides retain their parent layouts and masters before editing.

- [ ] **Step 2: Rewrite inherited text elements from `content.json`**

For each mapped slide, locate targets by resolved source element ID and replace text while preserving the inherited font family, size, weight, line spacing, paragraph spacing, text inset, alignment, and vertical anchor. Shorten copy or remap the slide instead of reducing font below the global minimum.

- [ ] **Step 3: Replace source evidence images only through inherited image frames**

Use authentic extracted source captures. Crop to exclude account IDs, email addresses, IP addresses, bucket-specific identifiers, browser bookmarks, Windows taskbars, and unrelated browser chrome. Do not fabricate AWS or application screenshots.

- [ ] **Step 4: Apply the approved visual cleanup**

Keep the original colored rail, light background, and restrained palette. Delete authoring prompts and unused blank capture boxes identified in the frame map. Use the existing metric, process, table, and evidence layouts; do not overlay a parallel card system.

- [ ] **Step 5: Add `[Sources]` notes to every slide containing claims or images**

Use source blocks such as:

```text
[Sources]
- C:/Users/DWIT/Desktop/교육/8월/4일_48차/VOC_Improve_AWS_확장_최종완료보고_2조.pdf
- C:/ai_agent_quality_portfolio/docs/테스트_결과.md
- C:/ai_agent_quality_portfolio/docs/프로세스_명세서.md
[/Sources]
```

- [ ] **Step 6: Export the final PPTX**

Use:

```javascript
const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(finalPath);
```

Expected: final output exists, contains 22 slides, and remains editable.

---

### Task 5: Render, inspect, and correct all 22 slides

**Files:**
- Create: `.pptx-work/voc-final-report/final-render/`
- Create: `.pptx-work/voc-final-report/final-layout/`
- Create: `.pptx-work/voc-final-report/final-contact-sheet.png`
- Create: `.pptx-work/voc-final-report/qa-ledger.txt`

**Interfaces:**
- Consumes: final PPTX.
- Produces: slide renders, layout inspection, overflow report, fidelity report, and a zero-defect QA ledger.

- [ ] **Step 1: Render all final slides and generate the montage**

Expected: 22 PNG files plus one contact sheet.

- [ ] **Step 2: Inspect every slide individually at full size**

For slides 1 through 22, record pass/fail for title wrapping, Korean glyph rendering, body wrapping, screenshot crop, table fit, object overlap, footer/page number, and sensitive-data exposure.

- [ ] **Step 3: Run canvas and overflow validation**

Run:

```powershell
python 'C:/Users/DWIT/.codex/plugins/cache/openai-primary-runtime/presentations/26.730.11710/skills/presentations/container_tools/slides_test.py' `
  'C:/ai_agent_quality_portfolio/output/presentations/VOC_Improve_AWS_확장_최종완료보고_2조_완성본.pptx'
```

Expected: zero elements outside the slide canvas.

- [ ] **Step 4: Run template fidelity validation**

Run:

```powershell
node 'C:/Users/DWIT/.codex/plugins/cache/openai-primary-runtime/presentations/26.730.11710/skills/presentations/template_following_scripts/check_template_fidelity.mjs' `
  --workspace 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report' `
  --starter-pptx 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/template-starter.pptx' `
  --final-pptx 'C:/ai_agent_quality_portfolio/output/presentations/VOC_Improve_AWS_확장_최종완료보고_2조_완성본.pptx' `
  --map 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/template-frame-map.json' `
  --starter-layout-dir 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/template-starter-layout' `
  --final-layout-dir 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report/final-layout' `
  --edit-dir 'C:/ai_agent_quality_portfolio/.pptx-work/voc-final-report'
```

Expected: zero unplanned deletions, unresolved placeholders, or template-fidelity failures.

- [ ] **Step 5: Correct defects and repeat all QA gates**

Do not deliver until the latest 22 individual renders, overflow test, placeholder audit, and fidelity check all pass.

---

### Task 6: Final evidence and delivery

**Files:**
- Verify: `output/presentations/VOC_Improve_AWS_확장_최종완료보고_2조_완성본.pptx`
- Modify: `docs/테스트_결과.md` only if a full repository test run intentionally regenerates it.

**Interfaces:**
- Consumes: validated final PPTX and QA ledger.
- Produces: final file hash, size, slide count, and concise delivery summary.

- [ ] **Step 1: Reopen and inspect the exported PPTX**

Confirm slide count 22, all slide titles present, `650/650`, `SKIPPED`, `UNVERIFIED`, and `CONDITIONAL_STAGING` visible in the intended slides, with no blank placeholders.

- [ ] **Step 2: Verify the source PDF and PPTX hashes are unchanged**

Compute SHA-256 for both sources before and after authoring and require exact equality.

- [ ] **Step 3: Run the relevant repository regression tests**

Run:

```powershell
pytest -q
```

Expected: all repository tests pass. Preserve unrelated user modifications in CloudFormation and test files.

- [ ] **Step 4: Report the output with one presentation file citation**

Include the final slide count, representative corrections, evidence sources, and the validated PPTX path. Do not cite temporary renders or build scripts.
