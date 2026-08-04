from pathlib import Path

from PIL import Image

from scripts.build_system_intro import (
    INTRO_SCENES,
    IntroScene,
    build_frame_schedule,
    render_scene_frame,
)


def test_approved_intro_has_three_scenes_and_exact_24_second_duration():
    assert [scene.kind for scene in INTRO_SCENES] == ["flow", "layers", "cards"]
    assert [scene.duration for scene in INTRO_SCENES] == [7.0, 7.0, 10.0]
    assert sum(scene.duration for scene in INTRO_SCENES) == 24.0
    assert "AWS 서비스" in INTRO_SCENES[2].items


def test_frame_schedule_changes_highlight_without_static_gaps_over_three_seconds():
    schedule = build_frame_schedule(INTRO_SCENES)

    assert schedule[0][0] == 0.0
    assert schedule[-1][1] == 24.0
    assert all(end - start <= 2.0 for start, end, _, _ in schedule)


def test_render_scene_frame_is_full_hd_and_uses_approved_navy_background(tmp_path: Path):
    scene = IntroScene("flow", "프로세스 흐름", 7.0, ("합성 데이터", "QA 실행"))
    output = tmp_path / "frame.png"

    render_scene_frame(scene, 0, output)

    with Image.open(output) as image:
        assert image.size == (1920, 1080)
        assert image.mode == "RGB"
        red, green, blue = image.getpixel((10, 30))
        assert blue > red
        assert blue > green
