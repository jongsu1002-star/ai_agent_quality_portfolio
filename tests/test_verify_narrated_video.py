from pathlib import Path

import pytest

from scripts.verify_narrated_video import MediaInfo, parse_media, verify_metadata


FFMPEG_SAMPLE = """
  Duration: 00:05:24.00, start: 0.000000, bitrate: 778 kb/s
  Stream #0:0: Video: h264 (High), yuv420p, 1920x1080, 60 fps, 60 tbr
  Stream #0:1: Audio: aac (LC), 48000 Hz, stereo, fltp, 160 kb/s
"""


def test_parse_media_finds_324_second_video_and_aac_audio():
    metadata = parse_media(FFMPEG_SAMPLE)

    assert metadata.duration == pytest.approx(324.0)
    assert metadata.video == (1920, 1080, 60.0)
    assert metadata.audio_codec == "aac"
    assert metadata.audio_rate == 48_000


def test_metadata_validation_requires_exact_portfolio_delivery_shape():
    good = MediaInfo(324.0, (1920, 1080, 60.0), "aac", 48_000)
    bad = MediaInfo(300.0, (1280, 720, 30.0), "mp3", 44_100)

    assert verify_metadata(good) == []
    assert len(verify_metadata(bad)) == 4


def test_parse_media_rejects_missing_audio():
    with pytest.raises(ValueError, match="audio"):
        parse_media("Duration: 00:05:24.00\nVideo: h264, 1920x1080, 60 fps")
