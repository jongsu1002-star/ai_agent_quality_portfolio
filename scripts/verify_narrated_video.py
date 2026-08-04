"""Verify the final 5:24 narrated portfolio video."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


DURATION_RE = re.compile(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)")
VIDEO_RE = re.compile(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b.*?\b([0-9.]+) fps\b")
AUDIO_RE = re.compile(r"Audio:\s*([\w-]+).*?\b(\d+) Hz\b")


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    video: tuple[int, int, float]
    audio_codec: str
    audio_rate: int


def parse_media(text: str) -> MediaInfo:
    duration_match = DURATION_RE.search(text)
    video_match = VIDEO_RE.search(text)
    audio_match = AUDIO_RE.search(text)
    if duration_match is None:
        raise ValueError("duration stream metadata is missing")
    if video_match is None:
        raise ValueError("video stream metadata is missing")
    if audio_match is None:
        raise ValueError("audio stream metadata is missing")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    width, height, fps = video_match.groups()
    codec, rate = audio_match.groups()
    return MediaInfo(
        duration,
        (int(width), int(height), float(fps)),
        codec.lower(),
        int(rate),
    )


def verify_metadata(info: MediaInfo) -> list[str]:
    errors: list[str] = []
    if abs(info.duration - 324.0) > 0.05:
        errors.append(f"duration must be 324 seconds, got {info.duration:.3f}")
    if info.video[:2] != (1920, 1080):
        errors.append(f"resolution must be 1920x1080, got {info.video[0]}x{info.video[1]}")
    if abs(info.video[2] - 60.0) > 0.01:
        errors.append(f"frame rate must be 60fps, got {info.video[2]:g}")
    if info.audio_codec != "aac" or info.audio_rate != 48_000:
        errors.append(f"audio must be AAC 48kHz, got {info.audio_codec} {info.audio_rate}Hz")
    return errors


def verify(ffmpeg: Path, video: Path) -> list[str]:
    if not ffmpeg.is_file():
        return [f"FFmpeg not found: {ffmpeg}"]
    if not video.is_file():
        return [f"video not found: {video}"]
    probe = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(video)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        errors = verify_metadata(parse_media(f"{probe.stdout}\n{probe.stderr}"))
    except ValueError as exc:
        return [str(exc)]
    decode = subprocess.run(
        [str(ffmpeg), "-v", "error", "-i", str(video), "-f", "null", os.devnull],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if decode.returncode != 0:
        errors.append(f"full decode failed: {(decode.stderr or decode.stdout).strip()}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    args = parser.parse_args()
    errors = verify(args.ffmpeg, args.video)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: duration=324.000, resolution=1920x1080, fps=60")
    print("PASS: AAC 48kHz audio and full decode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
