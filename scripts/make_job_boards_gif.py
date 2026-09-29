#!/usr/bin/env python3
"""Render assets/job-boards.gif, the bar under the README banner: a live dot and
a picker wheel rolling through the supported job boards, each in its brand
color with the number of boards shipped in companies.yaml.

Re-run it after adding a fetcher or changing companies.yaml:

    python scripts/make_job_boards_gif.py

Needs Pillow, PyYAML and the Arial and Consolas fonts. Set FONT_DIR if they are
not in the Windows fonts folder.
"""

import math
import os
import sys
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "assets" / "job-boards.gif"

# Display name, companies.yaml key, and a brand color lifted for a dark ground.
BOARDS = [("Greenhouse", "greenhouse", (61, 211, 155)),
          ("Lever", "lever", (111, 168, 255)),
          ("Ashby", "ashby", (167, 139, 250)),
          ("Workday", "workday", (255, 166, 48))]

S = 2                      # draw at twice the size, then downscale for clean edges
W, H = 1200, 76            # same width as assets/banner.svg
BG = (13, 13, 15)
INK = (236, 236, 236)
DIM = (120, 120, 126)
RULE = (38, 38, 42)

FONT_DIR = os.environ.get("FONT_DIR", "C:/Windows/Fonts/")


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size * S)


F_LABEL = font("consolab.ttf", 15)
F_LEAD = font("arial.ttf", 27)
F_WHEEL = font("arialbd.ttf", 29)
F_COUNT = font("consola.ttf", 15)
F_TAG = font("consola.ttf", 15)

SPACING = 34 * S           # distance between wheel rows
HOLD_FRAMES, HOLD_MS = 8, 170   # the dot keeps pulsing while a board is held
STEPS, STEP_MS = 12, 30


def board_counts():
    with open(ROOT / "companies.yaml", encoding="utf-8") as fh:
        companies = (yaml.safe_load(fh) or {}).get("companies") or {}
    return {key: len(companies.get(key) or []) for _, key, _ in BOARDS}


COUNTS = board_counts()
TAGLINE = f"{sum(COUNTS.values())} boards  \u00b7  employers with real H-1B filings"


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def mix(color, amount):
    """Blend color toward the background; amount 0 is full color, 1 is BG."""
    return tuple(round(c + (b - c) * amount) for c, b in zip(color, BG))


def frame(position, pulse):
    """position is a float index into BOARDS; pulse runs 0..1 for the dot."""
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)
    cy = H * S // 2

    # Live dot with a ring that grows and fades.
    dot_x = 44 * S
    ring = (6 + 9 * pulse) * S
    d.ellipse([dot_x - ring, cy - ring, dot_x + ring, cy + ring],
              outline=mix((61, 211, 155), 0.35 + 0.65 * pulse), width=2 * S)
    d.ellipse([dot_x - 5 * S, cy - 5 * S, dot_x + 5 * S, cy + 5 * S], fill=(61, 211, 155))
    d.text((dot_x + 16 * S, cy), "LIVE", font=F_LABEL, fill=INK, anchor="lm")

    x = dot_x + 16 * S + d.textlength("LIVE", font=F_LABEL) + 22 * S
    d.line([(x, cy - 14 * S), (x, cy + 14 * S)], fill=RULE, width=S)
    x += 22 * S
    d.text((x, cy), "postings from", font=F_LEAD, fill=DIM, anchor="lm")
    x += d.textlength("postings from ", font=F_LEAD)

    tag_w = d.textlength(TAGLINE, font=F_TAG)
    d.text((W * S - 40 * S - tag_w, cy), TAGLINE, font=F_TAG, fill=DIM, anchor="lm")

    # The wheel: neighbours above and below, fading and flattening with
    # distance, clipped by the bar like a physical picker window.
    for k in range(-2, 3):
        i = math.floor(position) + k
        offset = i - position
        if abs(offset) > 1.2:
            continue
        name, key, color = BOARDS[i % len(BOARDS)]
        fade = min(1.0, abs(offset) * 1.05)
        squash = max(0.3, math.cos(offset * 1.1))
        count = f"{COUNTS[key]} board{'s' if COUNTS[key] != 1 else ''}"

        name_w = int(d.textlength(name, font=F_WHEEL))
        tile = Image.new("RGB", (name_w + 260 * S, 42 * S), BG)
        td = ImageDraw.Draw(tile)
        td.text((0, 21 * S), name, font=F_WHEEL, fill=mix(color, fade), anchor="lm")
        td.text((name_w + 12 * S, 23 * S), count, font=F_COUNT,
                fill=mix(DIM, 0.3 + 0.7 * fade), anchor="lm")
        tile = tile.resize((tile.width, max(1, int(tile.height * squash))), Image.LANCZOS)
        img.paste(tile, (int(x), int(cy + offset * SPACING - tile.height / 2)))

    return img.resize((W, H), Image.LANCZOS)


def main():
    frames, durations = [], []
    for i in range(len(BOARDS)):
        for h in range(HOLD_FRAMES):
            frames.append(frame(i, h / HOLD_FRAMES))
            durations.append(HOLD_MS)
        for s in range(1, STEPS):
            frames.append(frame(i + ease(s / STEPS), 0.0))
            durations.append(STEP_MS)

    # One palette for every frame: ramps from the background to white and to
    # each brand color, which is every tone the bar uses. Shared, so unchanged
    # pixels match across frames and the encoder only stores what moved.
    ramps = [(255, 255, 255)] + [color for _, _, color in BOARDS]
    steps = 256 // len(ramps)
    colors = [mix(c, 1 - k / (steps - 1)) for c in ramps for k in range(steps)]
    pal = Image.new("P", (1, 1))
    pal.putpalette([v for c in colors for v in c] + [0] * 3 * (256 - len(colors)))
    frames = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=True, disposal=1)
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
