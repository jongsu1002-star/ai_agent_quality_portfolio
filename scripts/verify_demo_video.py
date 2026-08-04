"""Validate the final portfolio recording and its Korean subtitle track."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path


FREEZE_EVENT_RE = re.compile(r"freeze_(start|end|duration):\s*([0-9.]+)")
SRT_TIMING_RE = re.compile(
    r"(?m)^(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})$"
)
DURATION_RE = re.compile(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)")
VIDEO_RE = re.compile(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b.*?\b([0-9.]+) fps\b")


def parse_freezes(stderr: str) -> list[tuple[float, float, float]]:
    freezes: list[tuple[float, float, float]] = []
    current: dict[str, float] | None = None
    for event, raw_value in FREEZE_EVENT_RE.findall(stderr):
        if event == "start":
            current = {"start": float(raw_value)}
            continue
        if current is None:
            continue
        current[event] = float(raw_value)
        if {"start", "end", "duration"}.issubset(current):
            freezes.append((current["start"], current["end"], current["duration"]))
            current = None
    return freezes


def _timestamp_seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def parse_srt(text: str) -> list[tuple[float, float]]:
    return [
        (_timestamp_seconds(start), _timestamp_seconds(end))
        for start, end in SRT_TIMING_RE.findall(text)
    ]


def validate_cues(
    cues: list[tuple[float, float]], expected_end: float, max_gap: float
) -> list[str]:
    errors: list[str] = []
    if not cues:
        return ["자막 큐가 없습니다."]
    cursor = 0.0
    for start, end in cues:
        gap = start - cursor
        if gap >= max_gap:
            errors.append(f"{gap:.3f}초 자막 공백이 {cursor:.3f}초에서 시작합니다.")
        if end <= start:
            errors.append(f"자막 종료가 시작보다 늦지 않습니다: {start:.3f}-{end:.3f}")
        cursor = max(cursor, end)
    if abs(cursor - expected_end) > 0.05:
        errors.append(
            f"마지막 자막 종료 {cursor:.3f}초가 목표 {expected_end:.3f}초와 다릅니다."
        )
    return errors


def _run_ffmpeg(ffmpeg: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ffmpeg), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _probe_metadata(ffmpeg: Path, video: Path) -> tuple[float, int, int, float, str]:
    result = _run_ffmpeg(ffmpeg, ["-hide_banner", "-i", str(video)])
    output = f"{result.stdout}\n{result.stderr}"
    duration_match = DURATION_RE.search(output)
    video_match = VIDEO_RE.search(output)
    if not duration_match or not video_match:
        raise ValueError("FFmpeg 출력에서 재생시간 또는 영상 스트림 정보를 찾지 못했습니다.")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    width, height, fps = video_match.groups()
    return duration, int(width), int(height), float(fps), output


def verify(
    ffmpeg: Path,
    video: Path,
    srt: Path,
    *,
    expected_duration: float = 300.0,
    max_freeze: float = 3.0,
) -> list[str]:
    errors: list[str] = []
    if not ffmpeg.is_file():
        return [f"FFmpeg 실행 파일을 찾을 수 없습니다: {ffmpeg}"]
    if not video.is_file():
        return [f"영상 파일을 찾을 수 없습니다: {video}"]
    if not srt.is_file():
        return [f"자막 파일을 찾을 수 없습니다: {srt}"]

    try:
        duration, width, height, fps, _ = _probe_metadata(ffmpeg, video)
    except ValueError as exc:
        return [str(exc)]
    if abs(duration - expected_duration) > 0.05:
        errors.append(
            f"재생시간 {duration:.3f}초가 목표 {expected_duration:.3f}초와 다릅니다."
        )
    if (width, height) != (1920, 1080):
        errors.append(f"해상도가 1920x1080이 아닙니다: {width}x{height}")
    if abs(fps - 60.0) > 0.01:
        errors.append(f"프레임레이트가 60fps가 아닙니다: {fps:g}fps")

    decode = _run_ffmpeg(
        ffmpeg, ["-v", "error", "-i", str(video), "-f", "null", os.devnull]
    )
    if decode.returncode != 0:
        detail = (decode.stderr or decode.stdout).strip()
        errors.append(f"전체 디코딩 실패(exit={decode.returncode}): {detail}")

    freeze = _run_ffmpeg(
        ffmpeg,
        [
            "-hide_banner",
            "-i",
            str(video),
            "-vf",
            f"freezedetect=n=-50dB:d={max_freeze}",
            "-f",
            "null",
            os.devnull,
        ],
    )
    if freeze.returncode != 0:
        errors.append(f"정지 화면 분석 실패(exit={freeze.returncode}).")
    freezes = [item for item in parse_freezes(freeze.stderr) if item[2] >= max_freeze]
    for start, end, duration_value in freezes:
        errors.append(
            f"{duration_value:.3f}초 정지 화면: {start:.3f}-{end:.3f}초"
        )

    cues = parse_srt(srt.read_text(encoding="utf-8-sig"))
    errors.extend(validate_cues(cues, expected_end=expected_duration, max_gap=max_freeze))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--srt", type=Path, required=True)
    args = parser.parse_args()

    errors = verify(args.ffmpeg, args.video, args.srt)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: duration=300.000, resolution=1920x1080, fps=60")
    print("PASS: decode, freeze<3.000s, subtitle coverage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
