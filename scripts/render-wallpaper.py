#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render the Amba Horizon wallpaper as a PNG using only the stdlib."""
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "profile/rootfs/usr/share/wallpapers/Amba/contents/images/3840x2160.png"
W, H = 3840, 2160


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def inside(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    result = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            result = not result
        j = i
    return result


def main() -> None:
    # A stylised A, scaled from a 256-unit design into the right-hand horizon.
    cx, cy, scale = 2860.0, 1080.0, 4.2
    outer = [(48, 196), (112, 56), (144, 56), (208, 196), (170, 196), (157, 165), (99, 165), (86, 196)]
    hole = [(112, 133), (144, 133), (128, 94)]
    outer = [(cx + (x - 128) * scale, cy + (y - 128) * scale) for x, y in outer]
    hole = [(cx + (x - 128) * scale, cy + (y - 128) * scale) for x, y in hole]
    rows = []
    for y in range(H):
        row = bytearray([0])
        horizon = max(0.0, 1.0 - abs(y - H * 0.64) / (H * 0.55))
        for x in range(W):
            glow = max(0.0, 1.0 - ((x - W * 0.74) ** 2 + (y - H * 0.58) ** 2) ** 0.5 / (W * 0.48))
            r = int(7 + 8 * glow)
            g = int(15 + 42 * glow + 12 * horizon)
            b = int(29 + 48 * glow + 20 * horizon)
            if inside(x + 0.5, y + 0.5, outer) and not inside(x + 0.5, y + 0.5, hole):
                t = (y - (cy - 310)) / 620
                r, g, b = int(20 + 25 * t), int(150 + 62 * (1 - t)), int(145 + 46 * (1 - t))
            row.extend((r, g, b))
        rows.append(bytes(row))
    raw = b"".join(rows)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(png)
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
