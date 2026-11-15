# License: AGPL-3.0-only (LICENSE).
"""GlucoCraft brand icon.

A hexagon (the six-membered ring of the glucose molecule) divided into six triangles, each in its range's color of the
time-in-ranges palette of the CGM Report (const.BAND_COLORS, the AGP colors; clockwise from the lower left: very high
orange, high yellow, in range dark green, tight range light green, low red, very low dark red), with a white 6-hour
glucose trace ending in a dot (no outline). Rendered at 2048 px and downsampled; transparent outside the hexagon.
Writes custom_components/glucocraft/brand/icon.png (256 px) and icon@2x.png (512 px), the files Home Assistant (2026.3
and later) and HACS read for a custom integration's icon.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 2048
COLORS = {  # const.BAND_COLORS
    "very_low": (0x9B, 0x1C, 0x1F, 255),
    "low": (0xE5, 0x35, 0x2B, 255),
    "tight_range": (0x52, 0xB9, 0x64, 255),
    "in_range": (0x1F, 0x8F, 0x45, 255),
    "high": (0xFC, 0xC2, 0x1B, 255),
    "very_high": (0xF0, 0x8A, 0x4B, 255),
}
ORDER = [
    "very_low",
    "very_high",
    "high",
    "in_range",
    "tight_range",
    "low",
]  # triangle k lies between hexagon corners k and k+1 (corners at 30 + 60k deg)


def hexagon(cx, cy, r):
    return [
        (cx + r * math.cos(math.radians(30 + 60 * k)), cy + r * math.sin(math.radians(30 + 60 * k))) for k in range(6)
    ]


def rounded_hexagon_mask(r, rad):
    """A hexagon with rounded corners, as an 8-bit mask."""
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    inner = hexagon(S / 2, S / 2, r - rad / math.cos(math.radians(30)))
    d.polygon(inner, fill=255)
    for i in range(6):
        a, b = inner[i], inner[(i + 1) % 6]
        nx, ny = a[1] - b[1], b[0] - a[0]
        length = math.hypot(nx, ny)
        nx, ny = nx / length * rad, ny / length * rad
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if (mx + nx - S / 2) ** 2 + (my + ny - S / 2) ** 2 < (mx - S / 2) ** 2 + (my - S / 2) ** 2:
            nx, ny = -nx, -ny
        d.polygon([a, b, (b[0] + nx, b[1] + ny), (a[0] + nx, a[1] + ny)], fill=255)
        d.ellipse([a[0] - rad, a[1] - rad, a[0] + rad, a[1] + rad], fill=255)
    return m


def glucose(t):
    """An illustrative 6-hour trace (t in hours): a clear rise and fall, a smaller second wave, ending a little higher."""
    return 104 + 46 * math.exp(-(((t - 2.0) / 0.85) ** 2)) + 16 * math.exp(-(((t - 4.6) / 0.6) ** 2)) + 5 * t / 6


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
wedges = Image.new("RGBA", (S, S), (0, 0, 0, 0))
wd = ImageDraw.Draw(wedges)
corners = hexagon(S / 2, S / 2, S * 0.6)  # oversized; the mask rounds the corners
for k in range(6):
    wd.polygon([(S / 2, S / 2), corners[k], corners[(k + 1) % 6]], fill=COLORS[ORDER[k]])
img.paste(wedges, (0, 0), rounded_hexagon_mask(S * 0.5, S * 0.06))

d = ImageDraw.Draw(img)
x0, x1, ytop, ybot = S * 0.14, S * 0.80, S * 0.27, S * 0.67
N = 4800  # a round brush stamped densely along the trace: smooth edges at any width
pts = [(x0 + (x1 - x0) * i / N, ybot - (glucose(6 * i / N) - 100) / 54 * (ybot - ytop)) for i in range(N + 1)]
for r, colour in ((S * 0.026, "white"),):
    for x, y in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill=colour)
x, y = pts[-1]  # the latest reading
d.ellipse([x - S * 0.054, y - S * 0.054, x + S * 0.054, y + S * 0.054], fill="white")

OUT = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft" / "brand"
OUT.mkdir(parents=True, exist_ok=True)
img.resize((512, 512), Image.LANCZOS).save(OUT / "icon@2x.png", optimize=True)
img.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png", optimize=True)
print("ok")
