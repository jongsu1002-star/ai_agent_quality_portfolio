"""Build an offline Korean narration track for the portfolio demo video."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path


_TIMING_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})$"
)
_ASS_TAG_RE = re.compile(r"\{\\[^}]+\}")


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str


INTRO_CUES = (
    Cue(
        1001,
        0.0,
        7.0,
        "합성 데이터 입력부터 QA와 VOC 분석, 운영 모니터링을 거쳐 AWS 서비스 배포까지 연결됩니다.",
    ),
    Cue(
        1002,
        7.0,
        14.0,
        "사용자와 운영자는 FastAPI 품질 플랫폼에서 QA, VOC, 케이식스를 실행하고 프로메테우스와 그라파나로 관측합니다.",
    ),
    Cue(
        1003,
        14.0,
        24.0,
        "등록과 업로드, 실제 QA, VOC Improved, 관측과 운영 안전, AWS 서비스까지 주요 기능을 순서대로 시연합니다.",
    ),
)


def _seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def parse_srt(path: Path) -> list[Cue]:
    blocks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig").strip())
    cues: list[Cue] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError(f"invalid SRT block: {block!r}")
        timing = _TIMING_RE.match(lines[1].strip())
        if timing is None:
            raise ValueError(f"invalid SRT timing: {lines[1]!r}")
        spoken_text = " ".join(_ASS_TAG_RE.sub("", line).strip() for line in lines[2:]).strip()
        cues.append(
            Cue(
                index=int(lines[0].strip()),
                start=_seconds(timing.group(1)),
                end=_seconds(timing.group(2)),
                text=spoken_text,
            )
        )
    return cues


def validate_cues(cues: list[Cue]) -> None:
    if len(cues) != 75:
        raise ValueError(f"expected 75 cues, got {len(cues)}")
    cursor = 0.0
    for expected_index, cue in enumerate(cues, start=1):
        if cue.index != expected_index:
            raise ValueError("cue indexes must be sequential")
        if abs(cue.start - cursor) > 0.001:
            raise ValueError("cue timeline has a gap or overlap")
        if abs((cue.end - cue.start) - 4.0) > 0.001:
            raise ValueError("every cue must occupy exactly four seconds")
        if not cue.text:
            raise ValueError("cue text must not be empty")
        cursor = cue.end
    if abs(cursor - 300.0) > 0.001:
        raise ValueError("cue timeline must end at 300 seconds")


def tempo_for(duration: float, slot_duration: float) -> float:
    safe_duration = max(0.1, float(slot_duration) - 0.3)
    return max(1.0, float(duration) / safe_duration)


def slot_filter(duration: float, slot_duration: float) -> str:
    filters = ["aresample=48000"]
    tempo = tempo_for(duration, slot_duration)
    if tempo > 1.0:
        filters.append(f"atempo={tempo:.6f}")
    filters.extend(
        [
            "afade=t=in:st=0:d=0.04",
            f"afade=t=out:st={max(0.0, slot_duration - 0.08):g}:d=0.08",
            "apad",
            f"atrim=duration={slot_duration:g}",
        ]
    )
    return ",".join(filters)


def build_manifest(cues: list[Cue], raw_dir: Path) -> list[dict[str, object]]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    return [
        {
            "index": cue.index,
            "text": cue.text,
            "wav_path": str((raw_dir / f"cue-{cue.index:03d}.wav").resolve()),
        }
        for cue in cues
    ]


def write_manifest(items: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def wave_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def sapi_command(
    helper: Path,
    manifest: Path,
    voice: str = "Microsoft Heami Desktop",
) -> list[str]:
    return [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(helper.resolve()),
        "-ManifestPath",
        str(manifest.resolve()),
        "-VoiceName",
        voice,
    ]


def ffmpeg_slot_args(
    ffmpeg: Path,
    raw: Path,
    output: Path,
    *,
    duration: float,
    slot_duration: float,
) -> list[str]:
    return [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(raw.resolve()),
        "-af",
        slot_filter(duration, slot_duration),
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "1",
        str(output.resolve()),
    ]


def _concat_manifest(paths: list[Path], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for path in paths:
        escaped = path.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_narration_track(
    *,
    ffmpeg: Path,
    srt: Path,
    helper: Path,
    work_dir: Path,
) -> Path:
    demo_cues = parse_srt(srt)
    validate_cues(demo_cues)
    cues = [*INTRO_CUES, *demo_cues]
    raw_dir = work_dir / "raw"
    slot_dir = work_dir / "slots"
    slot_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = work_dir / "speech-manifest.json"
    write_manifest(build_manifest(cues, raw_dir), manifest_path)
    subprocess.run(sapi_command(helper, manifest_path), check=True)

    slots: list[Path] = []
    for cue in cues:
        raw = raw_dir / f"cue-{cue.index:03d}.wav"
        slot = slot_dir / f"slot-{cue.index:04d}.wav"
        subprocess.run(
            ffmpeg_slot_args(
                ffmpeg,
                raw,
                slot,
                duration=wave_duration(raw),
                slot_duration=cue.end - cue.start,
            ),
            check=True,
        )
        slots.append(slot)

    concat_path = work_dir / "audio-slots.txt"
    _concat_manifest(slots, concat_path)
    narration = work_dir / "narration-324s.wav"
    subprocess.run(
        [
            str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_path.resolve()),
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
            str(narration.resolve()),
        ],
        check=True,
    )
    return narration


def assemble_video(
    *,
    ffmpeg: Path,
    intro: Path,
    original: Path,
    narration: Path,
    output: Path,
    work_dir: Path,
) -> None:
    video_manifest = work_dir / "video-parts.txt"
    _concat_manifest([intro, original], video_manifest)
    combined = work_dir / "combined-324s-video.mp4"
    subprocess.run(
        [
            str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(video_manifest.resolve()),
            "-map", "0:v:0", "-c:v", "copy", str(combined.resolve()),
        ],
        check=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".new.mp4")
    subprocess.run(
        [
            str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(combined.resolve()), "-i", str(narration.resolve()),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-af", "loudnorm=I=-18:LRA=7:TP=-2",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-t", "324", "-movflags", "+faststart", str(temporary.resolve()),
        ],
        check=True,
    )
    temporary.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("--intro", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument(
        "--helper",
        type=Path,
        default=Path(__file__).with_name("synthesize_korean_tts.ps1"),
    )
    args = parser.parse_args()
    narration = build_narration_track(
        ffmpeg=args.ffmpeg,
        srt=args.srt,
        helper=args.helper,
        work_dir=args.work_dir,
    )
    assemble_video(
        ffmpeg=args.ffmpeg,
        intro=args.intro,
        original=args.original,
        narration=narration,
        output=args.output,
        work_dir=args.work_dir,
    )


if __name__ == "__main__":
    main()
