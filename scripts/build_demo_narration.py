"""Build an offline Korean narration track for the portfolio demo video."""

from __future__ import annotations

import json
import re
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
