from scripts.verify_demo_video import parse_freezes, parse_srt, validate_cues


def test_parse_freezes_pairs_start_and_duration():
    log = "freeze_start: 12.4\nfreeze_end: 15.6 | freeze_duration: 3.2\n"

    assert parse_freezes(log) == [(12.4, 15.6, 3.2)]


def test_parse_freezes_accepts_ffmpeg_native_duration_before_end_order():
    log = (
        "lavfi.freezedetect.freeze_start: 12.4\n"
        "lavfi.freezedetect.freeze_duration: 3.2\n"
        "lavfi.freezedetect.freeze_end: 15.6\n"
    )

    assert parse_freezes(log) == [(12.4, 15.6, 3.2)]


def test_validate_cues_rejects_gap_of_three_seconds():
    cues = [(0.0, 2.0), (5.0, 8.0), (8.0, 300.0)]

    errors = validate_cues(cues, expected_end=300.0, max_gap=3.0)

    assert any("3.000초 자막 공백" in error for error in errors)


def test_parse_srt_accepts_utf8_korean_and_full_coverage():
    text = (
        "1\n00:00:00,000 --> 00:02:30,000\n기능 설명\n\n"
        "2\n00:02:30,000 --> 00:05:00,000\n처리 완료\n"
    )

    cues = parse_srt(text)

    assert validate_cues(cues, expected_end=300.0, max_gap=3.0) == []
