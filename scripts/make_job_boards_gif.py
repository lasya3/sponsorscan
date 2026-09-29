#!/usr/bin/env python3
"""Render assets/job-boards.gif, the README strip that rolls through the
supported job boards like a picker wheel.

Re-run it after adding a fetcher:

    python scripts/make_job_boards_gif.py

Needs Pillow and the Arial fonts. Set FONT_DIR if they are not in the Windows
fonts folder.
"""

import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BOARDS = ["Greenhouse", "Lever", "Ashby", "Workday"]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else
           Path(__file__).resolve().parent.parent / "assets" / "job-boards.gif")

S = 2                      # draw at twice the size, then downscale for clean edges
W, H = 820, 150
LEFT = 72                  # matches the left margin of assets/banner.svg
FONT_DIR = os.environ.get("FONT_DIR", "C:/Windows/Fonts/")
lead_font = ImageFont.truetype(os.path.join(FONT_DIR, "arial.ttf"), 44 * S)
wheel_font = ImageFont.truetype(os.path.join(FONT_DIR, "arialbd.ttf"), 44 * S)

LEAD = "Live postings from"
SPACING = 58 * S           # distance between wheel rows
HOLD_MS = 1300
STEPS = 12                 # frames per transition
STEP_MS = 30


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def frame(position):
    """position is a float index into BOARDS; a whole number is centered."""
    img = Image.new("L", (W * S, H * S), 255)
    d = ImageDraw.Draw(img)
    cy = H * S // 2

    d.text((LEFT * S, cy), LEAD, font=lead_font, fill=90, anchor="lm")
    x = LEFT * S + d.textlength(LEAD + " ", font=lead_font)

    # Hairlines bracketing the selected row, drawn under the labels.
    half = 31 * S
    for yy in (cy - half, cy + half):
        d.line([(x - 8 * S, yy), (x + 290 * S, yy)], fill=232, width=S)

    for k in range(-2, 3):
        i = math.floor(position) + k
        offset = i - position                     # rows from the center
        if abs(offset) > 1.6:
            continue
        # Fade and flatten with distance, like a physical picker wheel.
        shade = int(255 * min(1.0, abs(offset) * 0.62))
        squash = max(0.35, math.cos(offset * 0.9))
        label = BOARDS[i % len(BOARDS)]
        box = d.textbbox((0, 0), label, font=wheel_font, anchor="lt")
        tw, th = box[2] - box[0], box[3] - box[1] + 6 * S
        tile = Image.new("L", (tw + 4 * S, th), 255)
        ImageDraw.Draw(tile).text((2 * S, 0), label, font=wheel_font,
                                  fill=shade, anchor="lt")
        tile = tile.resize((tile.width, max(1, int(th * squash))), Image.LANCZOS)
        y = cy + offset * SPACING - tile.height // 2
        img.paste(tile, (int(x), int(y)), Image.eval(tile, lambda v: 255 - v))

    return img.resize((W, H), Image.LANCZOS)


def main():
    frames, durations = [], []
    for i in range(len(BOARDS)):
        frames.append(frame(i))
        durations.append(HOLD_MS)
        for s in range(1, STEPS):
            frames.append(frame(i + ease(s / STEPS)))
            durations.append(STEP_MS)

    # One shared 16-level gray palette, so unchanged pixels match across frames
    # and the encoder only stores what moved.
    levels = [round(i * 255 / 15) for i in range(16)]
    pal = Image.new("P", (1, 1))
    pal.putpalette([v for lvl in levels for v in (lvl, lvl, lvl)] + [0] * 3 * 240)
    frames = [f.convert("RGB").quantize(palette=pal, dither=Image.Dither.NONE)
              for f in frames]
    frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=True, disposal=1)
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
