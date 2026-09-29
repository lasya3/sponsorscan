#!/usr/bin/env python3
"""Render assets/job-boards.gif, the bar under the README banner: a live dot and
a picker wheel rolling through the supported job boards' logos, each with the
number of boards shipped in companies.yaml.

Re-run it after adding a fetcher or changing companies.yaml:

    python scripts/make_job_boards_gif.py

Needs Pillow, PyYAML, cairosvg and the Arial and Consolas fonts. Set FONT_DIR if
the fonts are not in the Windows fonts folder.

The logos in assets/logos belong to their owners and appear only to show which
job boards SponsorScan reads.
"""

import io
import math
import os
import sys
from pathlib import Path

import cairosvg
import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LOGOS = ROOT / "assets" / "logos"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "assets" / "job-boards.gif"

# companies.yaml key, logo file, display height in px (wordmarks differ in how
# much of their height is letters), a vertical nudge in px that lines the
# letters up with the lead text (Greenhouse's "g" hangs below its baseline),
# and the colors the logo uses, for the palette.
BOARDS = [("greenhouse", "greenhouse.svg", 29, 4, [(76, 179, 152)]),
          ("lever", "lever.png", 19, 0, [(5, 119, 103)]),
          ("ashby", "ashby.svg", 24, 0, [(255, 255, 255)]),
          ("workday", "workday.svg", 36, -2, [(255, 255, 255), (252, 91, 5)])]

S = 2                      # draw at twice the size, then downscale for clean edges
W, H = 1200, 76            # same width as assets/banner.svg
BG = (13, 13, 15)
INK = (236, 236, 236)
DIM = (120, 120, 126)
RULE = (38, 38, 42)
LIVE = (61, 211, 155)

FONT_DIR = os.environ.get("FONT_DIR", "C:/Windows/Fonts/")


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size * S)


F_LABEL = font("consolab.ttf", 15)
F_LEAD = font("arial.ttf", 27)
F_COUNT = font("consola.ttf", 15)
F_TAG = font("consola.ttf", 15)

SPACING = 36 * S           # distance between wheel rows
HOLD_FRAMES, HOLD_MS = 8, 170   # the dot keeps pulsing while a board is held
STEPS, STEP_MS = 12, 30


def load_logo(filename, height):
    """The logo as RGBA at `height` (supersampled). A black wordmark drawn with
    currentColor, like Ashby's, is rendered white for the dark bar."""
    path = LOGOS / filename
    if path.suffix == ".svg":
        svg = path.read_text(encoding="utf-8").replace("currentColor", "#FFFFFF")
        png = cairosvg.svg2png(bytestring=svg.encode(), output_height=height * S)
        img = Image.open(io.BytesIO(png)).convert("RGBA")
    else:
        img = Image.open(path).convert("RGBA")
        img = img.resize((round(img.width * height * S / img.height), height * S),
                         Image.LANCZOS)
    return img.crop(img.getbbox())


def board_counts():
    with open(ROOT / "companies.yaml", encoding="utf-8") as fh:
        companies = (yaml.safe_load(fh) or {}).get("companies") or {}
    return {key: len(companies.get(key) or []) for key, *_ in BOARDS}


COUNTS = board_counts()
LOGO_IMAGES = {key: load_logo(filename, height) for key, filename, height, *_ in BOARDS}
NUDGE = {key: dy * S for key, _, _, dy, _ in BOARDS}
TAGLINE = f"{sum(COUNTS.values())} boards  \u00b7  employers with real H-1B filings"


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def mix(color, amount):
    """Blend color toward the background; amount 0 is full color, 1 is BG."""
    return tuple(round(c + (b - c) * amount) for c, b in zip(color, BG))


def wheel_row(key, fade):
    """One wheel row, the logo then its board count, faded toward BG."""
    logo = LOGO_IMAGES[key]
    count = f"{COUNTS[key]} board{'s' if COUNTS[key] != 1 else ''}"
    row_h = 44 * S
    row = Image.new("RGBA", (logo.width + 200 * S, row_h), BG + (255,))
    faded = logo.copy()
    faded.putalpha(faded.getchannel("A").point(lambda a: round(a * (1 - fade))))
    row.alpha_composite(faded, (0, (row_h - logo.height) // 2 + NUDGE[key]))
    ImageDraw.Draw(row).text((logo.width + 14 * S, row_h // 2 + 2 * S), count,
                             font=F_COUNT, fill=mix(DIM, 0.3 + 0.7 * fade), anchor="lm")
    return row.convert("RGB")


def frame(position, pulse):
    """position is a float index into BOARDS; pulse runs 0..1 for the dot."""
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)
    cy = H * S // 2

    # Live dot with a ring that grows and fades.
    dot_x = 44 * S
    ring = (6 + 9 * pulse) * S
    d.ellipse([dot_x - ring, cy - ring, dot_x + ring, cy + ring],
              outline=mix(LIVE, 0.35 + 0.65 * pulse), width=2 * S)
    d.ellipse([dot_x - 5 * S, cy - 5 * S, dot_x + 5 * S, cy + 5 * S], fill=LIVE)
    d.text((dot_x + 16 * S, cy), "LIVE", font=F_LABEL, fill=INK, anchor="lm")

    x = dot_x + 16 * S + d.textlength("LIVE", font=F_LABEL) + 22 * S
    d.line([(x, cy - 14 * S), (x, cy + 14 * S)], fill=RULE, width=S)
    x += 22 * S
    d.text((x, cy), "postings from", font=F_LEAD, fill=DIM, anchor="lm")
    x += d.textlength("postings from ", font=F_LEAD) + 4 * S

    tag_w = d.textlength(TAGLINE, font=F_TAG)
    d.text((W * S - 40 * S - tag_w, cy), TAGLINE, font=F_TAG, fill=DIM, anchor="lm")

    # The wheel: neighbours above and below, fading and flattening with
    # distance, clipped by the bar like a physical picker window.
    for k in range(-2, 3):
        i = math.floor(position) + k
        offset = i - position
        if abs(offset) > 1.2:
            continue
        key = BOARDS[i % len(BOARDS)][0]
        row = wheel_row(key, min(1.0, abs(offset) * 1.05))
        squash = max(0.3, math.cos(offset * 1.1))
        row = row.resize((row.width, max(1, int(row.height * squash))), Image.LANCZOS)
        img.paste(row, (int(x), int(cy + offset * SPACING - row.height / 2)))

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

    # One palette for every frame: ramps from the background to white, the live
    # green and each logo color, which is every tone the bar uses. Shared, so
    # unchanged pixels match across frames and the encoder only stores what moved.
    ramps = list(dict.fromkeys([(255, 255, 255), LIVE] +
                               [c for *_, colors in BOARDS for c in colors]))
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
