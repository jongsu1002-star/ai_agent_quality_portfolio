"""Build the changing 15-step counter overlay for the five-minute demo video."""

from __future__ import annotations

from bisect import bisect_right


STEP_BOUNDARIES = (
    0.0,
    24.0,
    36.0,
    48.0,
    60.0,
    84.0,
    104.0,
    124.0,
    148.0,
    168.0,
    188.0,
    204.0,
    224.0,
    248.0,
    284.0,
    300.0,
)
STEP_RANGES = tuple(zip(STEP_BOUNDARIES, STEP_BOUNDARIES[1:]))


def step_at(seconds: float) -> int:
    """Return the one-based recorded feature step for a video timestamp."""

    value = float(seconds)
    if value < STEP_BOUNDARIES[0] or value > STEP_BOUNDARIES[-1]:
        raise ValueError("timestamp must be within the five-minute recording")
    if value == STEP_BOUNDARIES[-1]:
        return len(STEP_RANGES)
    return bisect_right(STEP_BOUNDARIES, value)


def ffmpeg_step_counter_filter() -> str:
    """Return filters that cover the captured 1/15 label and render each real step."""

    filters = ["drawbox=x=1368:y=918:w=68:h=70:color=0x0f172a@1:t=fill"]
    for step, (start, end) in enumerate(STEP_RANGES, start=1):
        enable = f"gte(t\\,{start:g})*lt(t\\,{end:g})"
        filters.append(
            "drawtext="
            "font='Malgun Gothic':"
            f"text='{step} / 15':"
            "fontcolor=white:fontsize=17:"
            "x=1374:y=937:"
            f"enable='{enable}'"
        )
    return ",".join(filters)


def ffmpeg_final_filter() -> str:
    """Render the changing counter, remove the taskbar, and restore activity."""

    return ",".join(
        [
            ffmpeg_step_counter_filter(),
            "crop=1920:1038:0:0",
            "scale=1920:1080",
            "setsar=1",
            (
                "drawbox=x=0:y=ih-18:w=iw:h=18:color=0x2563eb@0.95:t=fill:"
                "enable='lt(mod(t\\,2)\\,1)'"
            ),
            (
                "drawbox=x=0:y=ih-18:w=iw:h=18:color=0xf97316@0.95:t=fill:"
                "enable='gte(mod(t\\,2)\\,1)'"
            ),
        ]
    )


if __name__ == "__main__":
    print(ffmpeg_final_filter())
