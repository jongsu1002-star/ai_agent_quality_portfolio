# Korean Demo Narration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a 24-second three-design animated system introduction, synchronize Korean female narration for the intro and 75 subtitle cues, and produce a separate 5-minute-24-second portfolio MP4.

**Architecture:** Pillow renders highlighted vector-style frames for three intro designs and FFmpeg turns them into a 24-second 60fps intro. A Python orchestrator parses the SRT, invokes one PowerShell SAPI batch for offline speech synthesis, fits the three intro utterances and 75 subtitle utterances into their exact slots, concatenates the intro with the unchanged five-minute demo, and muxes normalized AAC audio. A separate verifier checks the 324-second video and audio streams without changing the silent source.

**Tech Stack:** Python 3.12, Pillow 10.4, Malgun Gothic, Windows PowerShell/System.Speech, Microsoft Heami Desktop, FFmpeg 7.1, pytest.

## Global Constraints

- Preserve `demo/video/portfolio-demo-docker.mp4` unchanged.
- Produce `demo/video/portfolio-demo-docker-narrated.mp4` as a separate artifact.
- Use only the installed `Microsoft Heami Desktop` `ko-KR` female voice; do not call external APIs.
- Preserve the original 300-second, 1920x1080, 60fps video and its existing burned captions, step counter, and taskbar removal.
- Prepend a 24-second 1920x1080, 60fps intro: 7 seconds horizontal flow, 7 seconds layered architecture, and 10 seconds feature cards.
- Produce a final duration of exactly 324 seconds.
- Never truncate speech; speed up a cue only when required to fit its slot, then pad the remainder with silence.
- Encode audio as AAC, 48kHz, stereo, 160kbps.

---

### Task 1: Subtitle cue model and validation

**Files:**
- Create: `scripts/build_demo_narration.py`
- Create: `tests/test_build_demo_narration.py`

**Interfaces:**
- Produces: `Cue(index: int, start: float, end: float, text: str)`, `parse_srt(path: Path) -> list[Cue]`, `validate_cues(cues: list[Cue]) -> None`, `tempo_for(duration: float, slot_duration: float) -> float`.
- Consumes: `demo/video/portfolio-demo-docker.ko.srt` encoded as UTF-8.

- [ ] **Step 1: Write failing parser and timing tests**

```python
def test_parse_srt_removes_ass_position_tags(tmp_path):
    path = tmp_path / "one.srt"
    path.write_text("1\n00:00:00,000 --> 00:00:04,000\n{\\an8}첫 문장입니다.\n", encoding="utf-8")
    assert parse_srt(path) == [Cue(1, 0.0, 4.0, "첫 문장입니다.")]

def test_tempo_only_accelerates_speech_that_exceeds_safe_slot():
    assert tempo_for(3.0, 4.0) == 1.0
    assert tempo_for(5.0, 4.0) == pytest.approx(5.0 / 3.7)
```

- [ ] **Step 2: Run the tests and confirm they fail because the module is missing**

Run: `pytest tests/test_build_demo_narration.py -q`
Expected: collection error for `scripts.build_demo_narration`.

- [ ] **Step 3: Implement the cue parser and exact five-minute validation**

```python
@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str

def tempo_for(duration: float, slot_duration: float) -> float:
    safe_duration = max(0.1, slot_duration - 0.3)
    return max(1.0, duration / safe_duration)
```

`validate_cues` must require 75 sequential cues, a zero start, no gaps or overlaps, positive four-second slots, and a final end of exactly 300 seconds.

- [ ] **Step 4: Run the focused tests**

Run: `pytest tests/test_build_demo_narration.py -q`
Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/build_demo_narration.py tests/test_build_demo_narration.py
git commit -m "feat: parse synchronized demo narration cues"
```

### Task 2: Offline SAPI batch synthesis and slot fitting

**Files:**
- Create: `scripts/synthesize_korean_tts.ps1`
- Modify: `scripts/build_demo_narration.py`
- Modify: `tests/test_build_demo_narration.py`

**Interfaces:**
- Consumes: UTF-8 JSON manifest containing `index`, `text`, and absolute `wav_path` fields.
- Produces: one PCM WAV per cue using `Microsoft Heami Desktop`.
- Produces: `INTRO_CUES` with exact durations `7.0`, `7.0`, `10.0`, `slot_filter(duration: float, slot_duration: float) -> str`, and `build_slots(...) -> list[Path]`.

Use these exact intro narration strings:

```python
INTRO_CUES = (
    Cue(1001, 0.0, 7.0, "합성 데이터 입력부터 QA와 VOC 분석, 운영 모니터링을 거쳐 AWS 서비스 배포까지 연결됩니다."),
    Cue(1002, 7.0, 14.0, "사용자와 운영자는 FastAPI 품질 플랫폼에서 QA, VOC, 케이식스를 실행하고 프로메테우스와 그라파나로 관측합니다."),
    Cue(1003, 14.0, 24.0, "등록과 업로드, 실제 QA, VOC Improved, 관측과 운영 안전, AWS 서비스까지 주요 기능을 순서대로 시연합니다."),
)
```

- [ ] **Step 1: Write failing filter-generation tests**

```python
def test_slot_filter_pads_short_cue_to_four_seconds():
    value = slot_filter(2.0, 4.0)
    assert "atempo=" not in value
    assert "apad" in value
    assert "atrim=duration=4" in value

def test_slot_filter_accelerates_long_cue_without_truncating_words():
    value = slot_filter(5.0, 4.0)
    assert "atempo=1.351351" in value
```

- [ ] **Step 2: Run the tests and confirm the missing functions fail**

Run: `pytest tests/test_build_demo_narration.py -q`
Expected: import or assertion failure for `slot_filter`.

- [ ] **Step 3: Implement the PowerShell synthesizer**

```powershell
Add-Type -AssemblyName System.Speech
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$synth.SelectVoice('Microsoft Heami Desktop')
$synth.Rate = 0
$synth.Volume = 100
foreach ($cue in $manifest) {
  $synth.SetOutputToWaveFile($cue.wav_path)
  $synth.Speak([string]$cue.text)
  $synth.SetOutputToNull()
}
```

The script must fail if the requested voice is not installed and must dispose the synthesizer in `finally`.

- [ ] **Step 4: Implement WAV duration measurement and FFmpeg slot conversion**

Use Python's `wave` module for PCM duration. Convert every slot to mono PCM 48kHz with `aresample=48000`, optional `atempo`, short fades, `apad`, and `atrim=duration=4`.

- [ ] **Step 5: Run the focused tests and one two-cue synthesis probe**

Run: `pytest tests/test_build_demo_narration.py -q`
Run: `python scripts/build_demo_narration.py --probe 2`
Expected: tests pass and two four-second slot WAVs are generated with non-zero samples.

- [ ] **Step 6: Commit**

```powershell
git add scripts/build_demo_narration.py scripts/synthesize_korean_tts.ps1 tests/test_build_demo_narration.py
git commit -m "feat: synthesize and fit Korean narration slots"
```

### Task 3: Three-design animated system introduction

**Files:**
- Create: `scripts/build_system_intro.py`
- Create: `tests/test_build_system_intro.py`

**Interfaces:**
- Produces: `INTRO_SCENES`, three `IntroScene` values with durations `7.0`, `7.0`, and `10.0` seconds.
- Produces: `render_intro_frames(output_dir: Path) -> list[FrameSpec]` and `build_intro_video(ffmpeg: Path, output: Path) -> None`.
- Produces artifact: a 24-second, 1920x1080, 60fps H.264 intro video.

- [ ] **Step 1: Write failing scene and frame-sequence tests**

```python
def test_intro_scenes_cover_24_seconds_and_all_three_designs():
    assert [scene.duration for scene in INTRO_SCENES] == [7.0, 7.0, 10.0]
    assert [scene.kind for scene in INTRO_SCENES] == ["flow", "layers", "cards"]
    assert sum(scene.duration for scene in INTRO_SCENES) == 24.0

def test_cards_scene_exposes_every_portfolio_capability():
    assert INTRO_SCENES[2].items == (
        "등록·업로드", "실제 QA", "VOC Improved", "관측", "운영 안전", "AWS 서비스"
    )
```

- [ ] **Step 2: Run tests and confirm the intro module is missing**

Run: `pytest tests/test_build_system_intro.py -q`
Expected: collection error for `scripts.build_system_intro`.

- [ ] **Step 3: Implement the scene model and Pillow renderer**

Use `C:/Windows/Fonts/malgun.ttf`, a 1920x1080 canvas, navy `#081426`, ocean blue `#2563EB`, teal `#14B8A6`, white text, and muted slate connectors. Render one highlighted frame per process node/layer/card plus an overview frame. Every frame must include the platform title, the current design label, and a bottom `Docker 검증 → AWS 서비스` status line.

- [ ] **Step 4: Implement the 24-second FFmpeg sequence**

Write a concat manifest from the rendered frames. Allocate exactly 7 seconds to flow frames, 7 seconds to layer frames, and 10 seconds to card frames; add a continuously moving 12-pixel activity line at the bottom and encode `libx264`, `yuv420p`, 60fps, SAR 1:1.

- [ ] **Step 5: Run tests and build an intro probe**

Run: `pytest tests/test_build_system_intro.py -q`
Run: `python scripts/build_system_intro.py --output demo/video/system-intro-probe.mp4`
Expected: tests pass and metadata reports 24 seconds, 1920x1080, 60fps.

- [ ] **Step 6: Commit**

```powershell
git add scripts/build_system_intro.py tests/test_build_system_intro.py
git commit -m "feat: add animated three-design system intro"
```

### Task 4: Narration assembly, muxing, and verification

**Files:**
- Modify: `scripts/build_demo_narration.py`
- Create: `scripts/verify_narrated_video.py`
- Create: `tests/test_verify_narrated_video.py`

**Interfaces:**
- Produces: `demo/video/portfolio-demo-docker-narrated.mp4`.
- Produces: verifier exit code 0 only when video metadata, AAC audio, duration, and complete decode pass.

- [ ] **Step 1: Write failing metadata parser tests for an AAC audio stream**

```python
def test_parse_media_finds_324_second_video_and_aac_audio():
    metadata = parse_media(FFMPEG_SAMPLE)
    assert metadata.duration == pytest.approx(324.0)
    assert metadata.video == (1920, 1080, 60.0)
    assert metadata.audio_codec == "aac"
    assert metadata.audio_rate == 48000
```

- [ ] **Step 2: Run the verifier tests and confirm the module is missing**

Run: `pytest tests/test_verify_narrated_video.py -q`
Expected: collection error for `scripts.verify_narrated_video`.

- [ ] **Step 3: Implement concatenation and final muxing**

Create a concat manifest for the three intro slot WAVs followed by the 75 subtitle slot WAVs, concatenate them to a 324-second PCM WAV, concatenate the 24-second intro video before the original 300-second demo, then mux:

```text
ffmpeg -i combined-324s-video.mp4 -i narration-324s.wav \
  -map 0:v:0 -map 1:a:0 -c:v copy \
  -af loudnorm=I=-18:LRA=7:TP=-2 \
  -c:a aac -b:a 160k -ar 48000 -ac 2 -shortest narrated.new.mp4
```

Move `narrated.new.mp4` to the final narrated filename only after validation succeeds.

- [ ] **Step 4: Implement narrated media verification**

The verifier must assert 324 seconds within 0.05 seconds, 1920x1080, 60fps, AAC 48kHz audio, and a successful `ffmpeg -v error -f null` full decode.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_build_demo_narration.py tests/test_verify_narrated_video.py -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add scripts/build_demo_narration.py scripts/verify_narrated_video.py tests/test_verify_narrated_video.py
git commit -m "feat: assemble and verify narrated demo video"
```

### Task 5: Build and validate the five-minute-24-second narrated artifact

**Files:**
- Create artifact: `demo/video/portfolio-demo-docker-narrated.mp4`
- Preserve: `demo/video/portfolio-demo-docker.mp4`

**Interfaces:**
- Consumes the committed tools from Tasks 1-3.
- Produces the user-deliverable narrated MP4 and verification evidence.

- [ ] **Step 1: Generate all 75 cue WAVs and the narrated MP4**

Run: `python scripts/build_demo_narration.py --video demo/video/portfolio-demo-docker.mp4 --srt demo/video/portfolio-demo-docker.ko.srt --output demo/video/portfolio-demo-docker-narrated.mp4`
Expected: three intro speech WAVs, 75 subtitle speech WAVs, 78 fitted slots, one 324-second narration WAV, and a final narrated MP4.

- [ ] **Step 2: Run video and audio verification**

Run: `python scripts/verify_narrated_video.py --video demo/video/portfolio-demo-docker-narrated.mp4`
Run: `python scripts/verify_demo_video.py --ffmpeg <ffmpeg> --video demo/video/portfolio-demo-docker-narrated.mp4 --srt demo/video/portfolio-demo-docker.ko.srt`
Expected: both commands exit 0.

- [ ] **Step 3: Inspect representative narration points**

Extract or play 0-7 seconds, 7-14 seconds, 14-24 seconds, 24-32 seconds, 168-176 seconds, and 316-324 seconds. Confirm every intro design is readable, speech begins within its visual slot, the original video starts at 24 seconds, and the final AWS narration ends before the video.

- [ ] **Step 4: Run regression tests**

Run: `pytest tests/test_build_demo_narration.py tests/test_verify_narrated_video.py tests/test_demo_step_timeline.py tests/test_verify_demo_video.py -q`
Expected: all selected tests pass.

- [ ] **Step 5: Report artifact path and checksum**

Report the absolute narrated MP4 path, file size, SHA-256, voice, duration, resolution, frame rate, and verification counts. Do not add the large MP4 to Git.
