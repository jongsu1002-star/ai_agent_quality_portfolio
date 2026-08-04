"""Build an offline Korean narration track for the portfolio demo video."""

from __future__ import annotations

import re
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
