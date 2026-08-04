"""Render the approved 24-second system overview intro."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1920
HEIGHT = 1080
NAVY = "#081426"
PANEL = "#10233F"
BLUE = "#2563EB"
TEAL = "#14B8A6"
WHITE = "#F8FAFC"
MUTED = "#A8BDD6"
FONT_PATH = Path(r"C:\Windows\Fonts\malgun.ttf")
BOLD_FONT_PATH = Path(r"C:\Windows\Fonts\malgunbd.ttf")


@dataclass(frozen=True)
class IntroScene:
    kind: str
    title: str
    duration: float
    items: tuple[str, ...]


INTRO_SCENES = (
    IntroScene(
        "flow",
        "AI Agent Quality Platform · 프로세스 흐름",
        7.0,
        ("합성 데이터", "QA 실행", "VOC Improved", "품질 분석", "모니터링", "운영 관리", "AWS 배포"),
    ),
    IntroScene(
        "layers",
        "서비스 아키텍처 · 계층별 연결",
        7.0,
        ("사용자 · 운영자", "FastAPI 품질 플랫폼", "QA · VOC · k6", "Prometheus · Grafana", "Docker", "AWS"),
    ),
    IntroScene(
        "cards",
        "5분 데모 · 핵심 기능 미리보기",
        10.0,
        ("등록·업로드", "실제 QA", "VOC Improved", "관측", "운영 안전", "AWS 서비스"),
    ),
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = BOLD_FONT_PATH if bold and BOLD_FONT_PATH.exists() else FONT_PATH
    return ImageFont.truetype(str(path), size=size)


def build_frame_schedule(
    scenes: tuple[IntroScene, ...],
) -> list[tuple[float, float, IntroScene, int]]:
    schedule: list[tuple[float, float, IntroScene, int]] = []
    cursor = 0.0
    for scene in scenes:
        step = scene.duration / len(scene.items)
        for index in range(len(scene.items)):
            end = cursor + step
            schedule.append((cursor, end, scene, index))
            cursor = end
    schedule[-1] = (schedule[-1][0], sum(scene.duration for scene in scenes), schedule[-1][2], schedule[-1][3])
    return schedule


def _rounded_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    *,
    active: bool,
    font_size: int = 30,
) -> None:
    fill = BLUE if active else PANEL
    outline = TEAL if active else "#254262"
    draw.rounded_rectangle(box, radius=22, fill=fill, outline=outline, width=4)
    font = _font(font_size, bold=active)
    bounds = draw.textbbox((0, 0), text, font=font)
    x = (box[0] + box[2] - (bounds[2] - bounds[0])) / 2
    y = (box[1] + box[3] - (bounds[3] - bounds[1])) / 2 - 5
    draw.text((x, y), text, font=font, fill=WHITE if active else MUTED)


def _draw_header(draw: ImageDraw.ImageDraw, scene: IntroScene, active_index: int) -> None:
    draw.text((96, 70), "PORTFOLIO DEMO", font=_font(24, bold=True), fill=TEAL)
    draw.text((96, 116), scene.title, font=_font(54, bold=True), fill=WHITE)
    draw.text(
        (96, 195),
        f"{active_index + 1:02d} / {len(scene.items):02d}  ·  {scene.items[active_index]}",
        font=_font(28),
        fill=MUTED,
    )


def _draw_flow(draw: ImageDraw.ImageDraw, scene: IntroScene, active_index: int) -> None:
    left, gap, top, width, height = 75, 24, 430, 230, 170
    for index, item in enumerate(scene.items):
        x = left + index * (width + gap)
        if index:
            draw.line((x - gap + 4, top + height // 2, x - 5, top + height // 2), fill=TEAL, width=5)
            draw.polygon(
                [(x - 5, top + height // 2), (x - 19, top + height // 2 - 10), (x - 19, top + height // 2 + 10)],
                fill=TEAL,
            )
        _rounded_panel(draw, (x, top, x + width, top + height), item, active=index == active_index, font_size=25)
    draw.text((96, 760), "데이터 → 검증 → 분석 → 관측 → 배포", font=_font(36, bold=True), fill=WHITE)
    draw.text((96, 825), "하나의 품질 루프에서 생성·평가·운영 지표를 연결합니다.", font=_font(28), fill=MUTED)


def _draw_layers(draw: ImageDraw.ImageDraw, scene: IntroScene, active_index: int) -> None:
    top, width, height, gap = 300, 1120, 92, 18
    for index, item in enumerate(scene.items):
        x = (WIDTH - width) // 2
        y = top + index * (height + gap)
        inset = index * 42
        _rounded_panel(draw, (x + inset, y, x + width - inset, y + height), item, active=index == active_index, font_size=28)
    draw.text((1500, 365), "UI", font=_font(28, bold=True), fill=TEAL)
    draw.text((1500, 475), "API", font=_font(28, bold=True), fill=TEAL)
    draw.text((1500, 585), "WORKLOAD", font=_font(28, bold=True), fill=TEAL)
    draw.text((1500, 695), "OBSERVE", font=_font(28, bold=True), fill=TEAL)
    draw.text((1500, 805), "RUNTIME", font=_font(28, bold=True), fill=TEAL)
    draw.text((1500, 915), "CLOUD", font=_font(28, bold=True), fill=TEAL)


def _draw_cards(draw: ImageDraw.ImageDraw, scene: IntroScene, active_index: int) -> None:
    card_w, card_h = 500, 235
    for index, item in enumerate(scene.items):
        row, col = divmod(index, 3)
        x = 150 + col * 605
        y = 320 + row * 300
        _rounded_panel(draw, (x, y, x + card_w, y + card_h), item, active=index == active_index, font_size=34)
        marker = "실행" if index in (1, 2) else "확인"
        draw.text((x + 28, y + 176), marker, font=_font(22, bold=True), fill=WHITE if index == active_index else MUTED)
    draw.text((150, 930), "합성 데이터 기반 실제 QA · LLM Judge는 SKIPPED로 명확히 표시", font=_font(28, bold=True), fill=TEAL)


def render_scene_frame(scene: IntroScene, active_index: int, output: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 14), fill=TEAL)
    _draw_header(draw, scene, active_index)
    if scene.kind == "flow":
        _draw_flow(draw, scene, active_index)
    elif scene.kind == "layers":
        _draw_layers(draw, scene, active_index)
    elif scene.kind == "cards":
        _draw_cards(draw, scene, active_index)
    else:
        raise ValueError(f"unsupported scene kind: {scene.kind}")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)


def build_intro(ffmpeg: Path, output: Path, work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    schedule = build_frame_schedule(INTRO_SCENES)
    manifest_lines: list[str] = []
    for number, (start, end, scene, active_index) in enumerate(schedule):
        frame = work_dir / f"intro-{number:02d}.png"
        render_scene_frame(scene, active_index, frame)
        escaped = frame.resolve().as_posix().replace("'", "'\\''")
        manifest_lines.extend([f"file '{escaped}'", f"duration {end - start:.9f}"])
    manifest_lines.append(f"file '{frame.resolve().as_posix()}'")
    manifest = work_dir / "intro-frames.txt"
    manifest.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(manifest.resolve()),
        "-f", "lavfi", "-i", "color=c=0x14B8A6:s=220x10:r=60",
        "-filter_complex",
        "[0:v]fps=60,format=yuv420p[base];[base][1:v]overlay=x='mod(t*260,2140)-220':y=1045:shortest=1,setsar=1[out]",
        "-map", "[out]", "-t", "24", "-an", "-r", "60",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        str(output.resolve()),
    ]
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    build_intro(args.ffmpeg, args.output, args.work_dir)


if __name__ == "__main__":
    main()
