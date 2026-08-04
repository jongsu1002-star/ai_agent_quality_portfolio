from pathlib import Path

import pytest

from scripts.build_demo_narration import Cue, parse_srt, slot_filter, tempo_for, validate_cues


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
