# Korean Demo Narration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a synchronized Korean female narration track from the 75 subtitle cues and mux it into a separate five-minute portfolio MP4.

**Architecture:** A Python orchestrator parses and validates the SRT, invokes one PowerShell SAPI batch for offline speech synthesis, fits every WAV into its exact four-second slot with FFmpeg, concatenates the slots, and muxes AAC audio while stream-copying the video. A separate verifier checks the final video and audio stream without changing the silent source.

**Tech Stack:** Python 3.12, Windows PowerShell/System.Speech, Microsoft Heami Desktop, FFmpeg 7.1, pytest.

## Global Constraints

- Preserve `demo/video/portfolio-demo-docker.mp4` unchanged.
- Produce `demo/video/portfolio-demo-docker-narrated.mp4` as a separate artifact.
- Use only the installed `Microsoft Heami Desktop` `ko-KR` female voice; do not call external APIs.
- Preserve the 300-second, 1920x1080, 60fps video and its existing burned captions, step counter, and taskbar removal.
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
- Produces: `slot_filter(duration: float, slot_duration: float) -> str` and `build_slots(...) -> list[Path]`.

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

### Task 3: Narration assembly, muxing, and verification

**Files:**
- Modify: `scripts/build_demo_narration.py`
- Create: `scripts/verify_narrated_video.py`
- Create: `tests/test_verify_narrated_video.py`

**Interfaces:**
- Produces: `demo/video/portfolio-demo-docker-narrated.mp4`.
- Produces: verifier exit code 0 only when video metadata, AAC audio, duration, and complete decode pass.

- [ ] **Step 1: Write failing metadata parser tests for an AAC audio stream**

```python
def test_parse_media_finds_video_and_aac_audio():
    metadata = parse_media(FFMPEG_SAMPLE)
    assert metadata.duration == pytest.approx(300.0)
    assert metadata.video == (1920, 1080, 60.0)
    assert metadata.audio_codec == "aac"
    assert metadata.audio_rate == 48000
```

- [ ] **Step 2: Run the verifier tests and confirm the module is missing**

Run: `pytest tests/test_verify_narrated_video.py -q`
Expected: collection error for `scripts.verify_narrated_video`.

- [ ] **Step 3: Implement concatenation and final muxing**

Create a concat manifest for the 75 slot WAVs, concatenate to a 300-second PCM WAV, then run:

```text
ffmpeg -i portfolio-demo-docker.mp4 -i narration.wav \
  -map 0:v:0 -map 1:a:0 -c:v copy \
  -af loudnorm=I=-18:LRA=7:TP=-2 \
  -c:a aac -b:a 160k -ar 48000 -ac 2 -shortest narrated.new.mp4
```

Move `narrated.new.mp4` to the final narrated filename only after validation succeeds.

- [ ] **Step 4: Implement narrated media verification**

The verifier must assert 300 seconds within 0.05 seconds, 1920x1080, 60fps, AAC 48kHz audio, and a successful `ffmpeg -v error -f null` full decode.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_build_demo_narration.py tests/test_verify_narrated_video.py -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add scripts/build_demo_narration.py scripts/verify_narrated_video.py tests/test_verify_narrated_video.py
git commit -m "feat: assemble and verify narrated demo video"
```

### Task 4: Build and validate the five-minute narrated artifact

**Files:**
- Create artifact: `demo/video/portfolio-demo-docker-narrated.mp4`
- Preserve: `demo/video/portfolio-demo-docker.mp4`

**Interfaces:**
- Consumes the committed tools from Tasks 1-3.
- Produces the user-deliverable narrated MP4 and verification evidence.

- [ ] **Step 1: Generate all 75 cue WAVs and the narrated MP4**

Run: `python scripts/build_demo_narration.py --video demo/video/portfolio-demo-docker.mp4 --srt demo/video/portfolio-demo-docker.ko.srt --output demo/video/portfolio-demo-docker-narrated.mp4`
Expected: 75 speech WAVs, 75 fitted slots, one 300-second narration WAV, and a final narrated MP4.

- [ ] **Step 2: Run video and audio verification**

Run: `python scripts/verify_narrated_video.py --video demo/video/portfolio-demo-docker-narrated.mp4`
Run: `python scripts/verify_demo_video.py --ffmpeg <ffmpeg> --video demo/video/portfolio-demo-docker-narrated.mp4 --srt demo/video/portfolio-demo-docker.ko.srt`
Expected: both commands exit 0.

- [ ] **Step 3: Inspect representative narration points**

Extract or play 0-8 seconds, 144-152 seconds, and 292-300 seconds. Confirm speech begins within its caption slot, remains intelligible, and the final AWS narration ends before the video.

- [ ] **Step 4: Run regression tests**

Run: `pytest tests/test_build_demo_narration.py tests/test_verify_narrated_video.py tests/test_demo_step_timeline.py tests/test_verify_demo_video.py -q`
Expected: all selected tests pass.

- [ ] **Step 5: Report artifact path and checksum**

Report the absolute narrated MP4 path, file size, SHA-256, voice, duration, resolution, frame rate, and verification counts. Do not add the large MP4 to Git.
