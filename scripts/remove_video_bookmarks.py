"""Mask the captured Chrome bookmarks bar without changing narration timing."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def bookmark_filter() -> str:
    return (
        "drawbox=x=0:y=88:w=1920:h=40:"
        "color=0xF4F6F8:t=fill:enable='gte(t,24)'"
    )


def ffmpeg_command(ffmpeg: Path, source: Path, output: Path) -> list[str]:
    return [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source.resolve()),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-vf",
        bookmark_filter(),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "60",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output.resolve()),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"source video not found: {args.source}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".new.mp4")
    subprocess.run(ffmpeg_command(args.ffmpeg, args.source, temporary), check=True)
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
