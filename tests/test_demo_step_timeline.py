import pytest

from scripts.demo_step_timeline import (
    STEP_RANGES,
    ffmpeg_final_filter,
    ffmpeg_step_counter_filter,
    step_at,
)


def test_step_at_changes_on_each_recorded_feature_boundary():
    expected = [
        (0.0, 1),
        (24.0, 2),
        (36.0, 3),
        (48.0, 4),
        (60.0, 5),
        (84.0, 6),
        (104.0, 7),
        (124.0, 8),
        (148.0, 9),
        (168.0, 10),
        (188.0, 11),
        (204.0, 12),
        (224.0, 13),
        (248.0, 14),
        (284.0, 15),
        (300.0, 15),
    ]

    assert [step_at(seconds) for seconds, _ in expected] == [step for _, step in expected]


def test_step_ranges_cover_the_full_five_minutes_without_gaps():
    assert len(STEP_RANGES) == 15
    assert STEP_RANGES[0] == (0.0, 24.0)
    assert STEP_RANGES[-1] == (284.0, 300.0)
    assert all(left[1] == right[0] for left, right in zip(STEP_RANGES, STEP_RANGES[1:]))


def test_ffmpeg_filter_draws_every_counter_value_over_the_fixed_counter():
    filter_text = ffmpeg_step_counter_filter()

    assert "drawbox=x=1368:y=918:w=68:h=70" in filter_text
    for step in range(1, 16):
        assert f"text='{step} / 15'" in filter_text


def test_step_at_rejects_times_outside_the_recording():
    with pytest.raises(ValueError):
        step_at(-0.001)
    with pytest.raises(ValueError):
        step_at(300.001)


def test_final_filter_removes_the_windows_taskbar_before_restoring_activity_band():
    filter_text = ffmpeg_final_filter()

    crop = "crop=1920:1038:0:0,scale=1920:1080,setsar=1"
    activity = "drawbox=x=0:y=ih-18:w=iw:h=18"
    assert crop in filter_text
    assert activity in filter_text
    assert filter_text.index("text='15 / 15'") < filter_text.index(crop)
    assert filter_text.index(crop) < filter_text.index(activity)
