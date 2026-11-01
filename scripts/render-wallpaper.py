#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render the Amba Horizon wallpaper as a PNG using only the stdlib.

The renderer is pure Python, so it avoids per-pixel polygon tests: the "A"
silhouette is resolved once per scanline into spans and painted with a slice
assignment, while the radial glow reuses a colour lookup table. 4K rendering
drops from ~16 s to a few seconds and any resolution can be requested.
"""
from __future__ import annotations

import argparse
from math import ceil, sqrt
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_W, DEFAULT_H = 3840, 2160
DEFAULT_OUT = ROOT / "profile/rootfs/usr/share/wallpapers/Amba/contents/images/3840x2160.png"

# The "A" is designed in a 256x256 box and scaled by 4.2 at 3840 px wide.
OUTER = ((48, 196), (112, 56), (144, 56), (208, 196), (170, 196), (157, 165), (99, 165), (86, 196))
HOLE = ((112, 133), (144, 133), (128, 94))

# Resolution of the radial glow lookup table; 1/1024 of the radius is far below
# one 8-bit colour step, so the banding is invisible.
GLOW_STEPS = 1024


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _edges(poly: list[tuple[float, float]]) -> list[tuple[float, float, float, float]]:
    n = len(poly)
    return [(poly[i][0], poly[i][1], poly[(i + 1) % n][0], poly[(i + 1) % n][1]) for i in range(n)]


def render(width: int, height: int, level: int = 6) -> bytes:
    """Return the encoded PNG for a wallpaper of the requested size."""
    sx = width / DEFAULT_W
    cy = height * 1080 / DEFAULT_H
    scale = 4.2 * sx

    def place(poly):
        cx = width * 2860 / DEFAULT_W
        return [(cx + (x - 128) * scale, cy + (y - 128) * scale) for x, y in poly]

    edge_list = _edges(place(OUTER)) + _edges(place(HOLE))

    gx, gy = width * 0.74, height * 0.58
    radius, radius2 = width * 0.48, (width * 0.48) ** 2
    inv_step = GLOW_STEPS / radius
    dx2 = [(x + 0.5 - gx) ** 2 for x in range(width)]

    lut_r = [7 + int(8 * (1 - i / GLOW_STEPS)) for i in range(GLOW_STEPS + 1)]
    lut_g = [15 + int(42 * (1 - i / GLOW_STEPS)) for i in range(GLOW_STEPS + 1)]
    lut_b = [29 + int(48 * (1 - i / GLOW_STEPS)) for i in range(GLOW_STEPS + 1)]

    rows = []
    for y in range(height):
        yy = y + 0.5
        horizon = max(0.0, 1.0 - abs(yy - height * 0.64) / (height * 0.55))
        add_g, add_b = int(12 * horizon), int(20 * horizon)
        dy2 = (yy - gy) ** 2

        row = bytearray(3 * width)
        pos = 0
        for d2x in dx2:
            d2 = d2x + dy2
            i = GLOW_STEPS if d2 >= radius2 else int(sqrt(d2) * inv_step)
            row[pos] = lut_r[i]
            row[pos + 1] = lut_g[i] + add_g
            row[pos + 2] = lut_b[i] + add_b
            pos += 3

        # Even-odd crossing list for this scanline gives the filled spans of the
        # "A"; the glyph colour only varies with y, so one colour per row.
        xs = []
        for x1, y1, x2, y2 in edge_list:
            if (y1 <= yy) != (y2 <= yy):
                xs.append(x1 + (yy - y1) * (x2 - x1) / (y2 - y1))
        if xs:
            xs.sort()
            t = (yy - (cy - 310 * sx)) / (620 * sx)
            t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
            pixel = bytes((int(20 + 25 * t), int(150 + 62 * (1 - t)), int(145 + 46 * (1 - t))))
            for k in range(0, len(xs) - 1, 2):
                first = max(0, ceil(xs[k] - 0.5))
                last = min(width, ceil(xs[k + 1] - 0.5))
                if last > first:
                    row[3 * first:3 * last] = pixel * (last - first)
        rows.append(b"\x00" + bytes(row))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows), level))
    png += chunk(b"IEND", b"")
    return png


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the Amba Horizon wallpaper (stdlib only).")
    parser.add_argument("--width", type=int, default=DEFAULT_W, help="pixels wide (default: 3840)")
    parser.add_argument("--height", type=int, default=DEFAULT_H, help="pixels high (default: 2160)")
    parser.add_argument("--output", type=Path, help="PNG destination (required for non-4K sizes)")
    parser.add_argument("--level", type=int, default=6, choices=range(10), help="zlib level 0-9 (default: 6)")
    args = parser.parse_args()

    if args.width < 16 or args.height < 16:
        parser.error("--width dan --height minimal 16 piksel.")
    output = args.output
    if output is None:
        if (args.width, args.height) != (DEFAULT_W, DEFAULT_H):
            parser.error("--output wajib diisi bila ukuran bukan 3840x2160.")
        output = DEFAULT_OUT

    png = render(args.width, args.height, args.level)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(png)
    try:
        shown = output.resolve().relative_to(ROOT)
    except ValueError:
        shown = output
    print(f"Wrote {shown} ({args.width}x{args.height}, {len(png)} bytes)")


if __name__ == "__main__":
    main()
