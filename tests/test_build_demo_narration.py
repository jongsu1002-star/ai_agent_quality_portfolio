from pathlib import Path
import wave

import pytest

from scripts.build_demo_narration import (
    INTRO_CUES,
    Cue,
    build_manifest,
    ffmpeg_slot_args,
    parse_srt,
    slot_filter,
    sapi_command,
    tempo_for,
    validate_cues,
    wave_duration,
    write_manifest,
)


def test_intro_narration_slots_match_the_three_approved_visual_durations():
    assert [(cue.start, cue.end) for cue in INTRO_CUES] == [
        (0.0, 7.0),
        (7.0, 14.0),
        (14.0, 24.0),
    ]
    assert "AWS 서비스 배포" in INTRO_CUES[0].text
    assert "프로메테우스와 그라파나" in INTRO_CUES[1].text
    assert "주요 기능을 순서대로 시연" in INTRO_CUES[2].text


def test_parse_srt_removes_ass_position_tags_and_keeps_korean_text(tmp_path: Path):
    path = tmp_path / "one.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:04,000\n{\\an8}첫 문장입니다.\n",
        encoding="utf-8",
    )

    assert parse_srt(path) == [Cue(1, 0.0, 4.0, "첫 문장입니다.")]


def test_validate_cues_accepts_75_contiguous_four_second_slots():
    cues = [
        Cue(index + 1, index * 4.0, (index + 1) * 4.0, f"문장 {index + 1}")
        for index in range(75)
    ]

    validate_cues(cues)


def test_validate_cues_rejects_a_timeline_gap():
    cues = [
        Cue(index + 1, index * 4.0, (index + 1) * 4.0, f"문장 {index + 1}")
        for index in range(75)
    ]
    cues[12] = Cue(13, 48.25, 52.0, "간격이 생긴 문장")

    with pytest.raises(ValueError, match="gap or overlap"):
        validate_cues(cues)


def test_tempo_only_accelerates_speech_that_exceeds_safe_slot():
    assert tempo_for(3.0, 4.0) == 1.0
    assert tempo_for(5.0, 4.0) == pytest.approx(5.0 / 3.7)


def test_slot_filter_pads_short_cue_and_fits_long_cue_without_cutting_words():
    short = slot_filter(2.0, 4.0)
    long = slot_filter(5.0, 4.0)

    assert "atempo=" not in short
    assert "apad" in short
    assert "atrim=duration=4" in short
    assert "atempo=1.351351" in long
    assert "apad" in long
    assert "atrim=duration=4" in long


def test_build_manifest_uses_absolute_numbered_wav_paths(tmp_path: Path):
    cues = [Cue(1, 0.0, 4.0, "첫 문장"), Cue(2, 4.0, 8.0, "둘째 문장")]

    manifest = build_manifest(cues, tmp_path / "raw")

    assert manifest == [
        {
            "index": 1,
            "text": "첫 문장",
            "wav_path": str((tmp_path / "raw" / "cue-001.wav").resolve()),
        },
        {
            "index": 2,
            "text": "둘째 문장",
            "wav_path": str((tmp_path / "raw" / "cue-002.wav").resolve()),
        },
    ]


def test_wave_duration_reads_real_pcm_frame_count(tmp_path: Path):
    path = tmp_path / "speech.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 20_000)

    assert wave_duration(path) == pytest.approx(1.25)


def test_sapi_command_runs_the_repo_helper_without_profile(tmp_path: Path):
    helper = tmp_path / "synthesize.ps1"
    manifest = tmp_path / "manifest.json"

    assert sapi_command(helper, manifest) == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(helper.resolve()),
        "-ManifestPath",
        str(manifest.resolve()),
        "-VoiceName",
        "Microsoft Heami Desktop",
    ]


def test_ffmpeg_slot_args_use_real_filter_and_pcm_output(tmp_path: Path):
    raw = tmp_path / "raw.wav"
    output = tmp_path / "slot.wav"
    args = ffmpeg_slot_args(Path("ffmpeg.exe"), raw, output, duration=5.0, slot_duration=4.0)

    assert args[:5] == ["ffmpeg.exe", "-y", "-hide_banner", "-loglevel", "error"]
    assert args[args.index("-af") + 1] == slot_filter(5.0, 4.0)
    assert args[-5:] == ["-ar", "48000", "-ac", "1", str(output.resolve())]


def test_write_manifest_preserves_korean_as_utf8_json(tmp_path: Path):
    target = tmp_path / "manifest.json"
    items = [{"index": 1, "text": "한국어 내레이션", "wav_path": "speech.wav"}]

    write_manifest(items, target)

    assert "한국어 내레이션" in target.read_text(encoding="utf-8")
    assert target.read_text(encoding="utf-8").startswith("[")
