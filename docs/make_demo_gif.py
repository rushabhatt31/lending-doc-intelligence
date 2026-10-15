"""Render the CLI demo as an animated terminal GIF for the README.

    python docs/make_demo_gif.py      # writes docs/demo.gif
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "demo.gif"
COMMAND = "python -m docintel.cli demo"

COLS, ROWS = 112, 34
FONT_SIZE, LINE_H, PAD = 13, 19, 18
BAR_H = 30
BG, BAR = (13, 17, 23), (30, 35, 44)
FG, DIM, BOLD = (212, 216, 222), (125, 133, 144), (245, 247, 250)
RED, GREEN, YELLOW, BLUE = (255, 107, 107), (95, 215, 135), (255, 215, 95), (121, 192, 255)
ANSI = {"91": RED, "92": GREEN, "93": YELLOW}
SEVERITY = {"[critical]": RED, "[high]": RED, "[medium]": YELLOW, "[low]": DIM}

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
font = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), FONT_SIZE)
font_b = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono-Bold.ttf"), FONT_SIZE)
CHAR_W = font.getlength("M")
W = int(PAD * 2 + CHAR_W * COLS)
H = BAR_H + PAD * 2 + LINE_H * ROWS


def parse(line: str) -> list[tuple[str, tuple, bool]]:
    """ANSI line -> [(text, color, bold)], truncated to COLS."""
    segs, color, bold, used = [], FG, False, 0
    for part in re.split(r"(\x1b\[[0-9;]*m)", line):
        m = re.match(r"\x1b\[([0-9;]*)m", part)
        if m:
            code = m.group(1)
            if code in ("0", ""):
                color, bold = FG, False
            elif code == "1":
                bold = True
            elif code == "2":
                color = DIM
            elif code in ANSI:
                color = ANSI[code]
            continue
        if not part or used >= COLS:
            continue
        room = COLS - used
        text = part if len(part) <= room else part[: room - 1] + "…"
        used += len(text)
        segs.append((text, color, bold))
    return segs


def accent(segs):
    """Colour check marks and severity tags so the story reads at a glance."""
    out = []
    for text, color, bold in segs:
        stripped = text.lstrip()
        if stripped.startswith("✓"):
            out += [(text[: text.index("✓")], color, bold), ("✓", GREEN, True), (text[text.index("✓") + 1:], color, bold)]
            continue
        tag = next((t for t in SEVERITY if stripped.startswith(t)), None)
        if tag:
            i = text.index(tag)
            out += [(text[:i], color, bold), (tag, SEVERITY[tag], True), (text[i + len(tag):], color, bold)]
            continue
        out.append((text, color, bold))
    return out


def frame(lines: list[list], cursor: bool = False) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, BAR_H], fill=BAR)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([14 + i * 20, 10, 26 + i * 20, 22], fill=c)
    title = "lending-doc-intelligence — demo"
    d.text(((W - font.getlength(title)) / 2, 8), title, font=font, fill=DIM)
    for row, segs in enumerate(lines[-ROWS:]):
        x, y = PAD, BAR_H + PAD + row * LINE_H
        for text, color, bold in segs:
            f = font_b if bold else font
            d.text((x, y), text, font=f, fill=color)
            x += CHAR_W * len(text)
        if cursor and row == len(lines[-ROWS:]) - 1:
            d.rectangle([x + 2, y + 2, x + CHAR_W, y + LINE_H - 3], fill=FG)
    return img


def main():
    env = {**os.environ, "DOCINTEL_DISABLE_LLM": "1"}
    raw = subprocess.run([sys.executable, "-m", "docintel.cli", "demo"], cwd=ROOT, env=env,
                         capture_output=True, text=True, check=True).stdout
    output = [accent(parse(l)) for l in raw.splitlines()]

    frames, durations = [], []
    prompt = [("~/lending-doc-intelligence", BLUE, True), (" $ ", DIM, False)]
    for i in range(0, len(COMMAND) + 1, 3):
        frames.append(frame([prompt + [(COMMAND[:i], FG, False)]], cursor=True))
        durations.append(70)
    durations[-1] = 500

    shown = [prompt + [(COMMAND, FG, False)]]
    step = 3
    for i in range(0, len(output), step):
        chunk = output[i:i + step]
        shown += chunk
        frames.append(frame(shown))
        plain = "".join(t for seg in chunk for t, _, _ in seg)
        durations.append(2200 if "Recommendation:" in plain else 2600 if "Audit chain" in plain else 110)
    frames.append(frame(shown + [prompt], cursor=True))
    durations.append(3500)

    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=48) for f in frames]
    pal[0].save(OUT, save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True, disposal=1)
    print(f"{OUT} — {len(frames)} frames, {OUT.stat().st_size / 1024:.0f} KB, {W}x{H}")


if __name__ == "__main__":
    main()
