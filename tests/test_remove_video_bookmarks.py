from pathlib import Path

from scripts.remove_video_bookmarks import bookmark_filter, ffmpeg_command


def test_bookmark_filter_only_masks_the_browser_bar_after_the_intro():
    assert bookmark_filter() == (
        "drawbox=x=0:y=88:w=1920:h=40:"
        "color=0xF4F6F8:t=fill:enable='gte(t,24)*lt(t,308)'"
    )


def test_ffmpeg_command_preserves_aac_audio_and_reencodes_only_video(tmp_path: Path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"

    command = ffmpeg_command(Path("ffmpeg.exe"), source, output)

    assert command[:5] == ["ffmpeg.exe", "-y", "-hide_banner", "-loglevel", "error"]
    assert command[command.index("-vf") + 1] == bookmark_filter()
    assert command[command.index("-c:a") + 1] == "copy"
    assert command[-1] == str(output.resolve())
