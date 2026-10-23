#!/usr/bin/env python3
"""Draw the Dock icon: the Kraken mark over a short list, on a dark tile.

No client mark. A client logo was here once and it read as a dark shape on a
dark tile, which is to say it read as nothing, and tying the icon to one client
makes the app look wrong the day the work is for somebody else. What is left says what
the app is: Kraken, and a list with the top line ticked.

Standard library only, so it survives a fresh laptop. Everything is drawn at
double size and averaged down, which is enough anti-aliasing for an icon. The
Kraken mark is read from a Kraken Core checkout rather than copied in, so the
repo carries no brand assets of its own. It is optional: `DANDORI_KRAKEN_LOGO`
names the PNG, or `KRAKEN_CORE` the checkout, and otherwise the usual place,
`~/Projects/kraken-core`, is tried. With no logo anywhere the icon is drawn
without the mark, the list alone, rather than failing the install.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib
from pathlib import Path

SIZE = 1024
SS = 2
W = SIZE * SS

LOGO_IN_CHECKOUT = Path("docs/_static/logo.png")


def kraken_logo() -> Path | None:
    """Where the Kraken mark is on this machine, or None when it is nowhere."""
    named = os.environ.get("DANDORI_KRAKEN_LOGO")
    candidates = [Path(named).expanduser()] if named else []
    if os.environ.get("KRAKEN_CORE"):
        candidates.append(Path(os.environ["KRAKEN_CORE"]).expanduser() / LOGO_IN_CHECKOUT)
    candidates.append(Path.home() / "Projects" / "kraken-core" / LOGO_IN_CHECKOUT)
    return next((c for c in candidates if c.is_file()), None)

INK = (9, 14, 26)
INK_TOP = (28, 42, 70)
CHALK = (244, 246, 250)
TICK = (232, 84, 216)


def load_png(path: Path) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    """Minimal 8-bit PNG reader, RGB or RGBA, enough for the two logo files."""
    blob = path.read_bytes()
    w, h, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", blob[16:29])
    if depth != 8 or interlace or colour not in (2, 6):
        raise ValueError(f"{path} is not an 8-bit non-interlaced RGB(A) png")
    channels = 4 if colour == 6 else 3

    idat = bytearray()
    i = 8
    while i < len(blob):
        length = struct.unpack(">I", blob[i : i + 4])[0]
        tag = blob[i + 4 : i + 8]
        if tag == b"IDAT":
            idat += blob[i + 8 : i + 8 + length]
        i += 12 + length

    raw = zlib.decompress(bytes(idat))
    stride = w * channels
    out: list[list[tuple[int, int, int, int]]] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(h):
        filt = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if filt == 1:
                line[x] = (line[x] + a) & 0xFF
            elif filt == 2:
                line[x] = (line[x] + b) & 0xFF
            elif filt == 3:
                line[x] = (line[x] + (a + b) // 2) & 0xFF
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 0xFF
        row = []
        for x in range(0, stride, channels):
            px = tuple(line[x : x + channels])
            row.append(px if channels == 4 else (*px, 255))
        out.append(row)
        prev = line
    return w, h, out


def crop_to_content(img, w: int, h: int):
    xs = [x for y in range(h) for x in range(w) if img[y][x][3] > 8]
    ys = [y for y in range(h) for x in range(w) if img[y][x][3] > 8]
    if not xs:
        return w, h, img
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return x1 - x0 + 1, y1 - y0 + 1, [row[x0 : x1 + 1] for row in img[y0 : y1 + 1]]


def sample(src, sw: int, sh: int, u: float, v: float) -> tuple[int, int, int, int]:
    """Bilinear sample, so the logo does not go blocky when scaled up."""
    x, y = u * (sw - 1), v * (sh - 1)
    x0, y0 = int(x), int(y)
    x1, y1 = min(x0 + 1, sw - 1), min(y0 + 1, sh - 1)
    fx, fy = x - x0, y - y0
    out = []
    for c in range(4):
        top = src[y0][x0][c] * (1 - fx) + src[y0][x1][c] * fx
        bot = src[y1][x0][c] * (1 - fx) + src[y1][x1][c] * fx
        out.append(int(top * (1 - fy) + bot * fy))
    return tuple(out)


def rounded(x: float, y: float, w: float, h: float, r: float):
    def inside(px: float, py: float) -> bool:
        if not (x <= px <= x + w and y <= py <= y + h):
            return False
        cx = min(max(px, x + r), x + w - r)
        cy = min(max(py, y + r), y + h - r)
        return (px - cx) ** 2 + (py - cy) ** 2 <= r * r

    return inside


def tick_mark(x: float, y: float, size: float, t: float):
    """A check: a short stroke down-right, then a long one up-right."""
    def leg(x0: float, y0: float, x1: float, y1: float):
        dx, dy = x1 - x0, y1 - y0
        length = (dx * dx + dy * dy) ** 0.5

        def inside(px: float, py: float) -> bool:
            u = ((px - x0) * dx + (py - y0) * dy) / (length * length)
            u = min(max(u, 0.0), 1.0)
            ox, oy = px - (x0 + u * dx), py - (y0 + u * dy)
            return ox * ox + oy * oy <= (t / 2) ** 2

        return inside

    short = leg(x + size * 0.06, y + size * 0.55, x + size * 0.38, y + size * 0.86)
    long = leg(x + size * 0.38, y + size * 0.86, x + size * 0.94, y + size * 0.14)
    return lambda px, py: short(px, py) or long(px, py)


def draw() -> list[list[tuple[int, int, int, int]]]:
    body = rounded(0, 0, W, W, W * 0.225)

    mark = None
    logo = kraken_logo()
    if logo is not None:
        try:
            kw, kh, kraken = load_png(logo)
            kw, kh, kraken = crop_to_content(kraken, kw, kh)
            kwidth = W * 0.50
            mark = (kraken, kw, kh, (W - kwidth) / 2, W * 0.135, kwidth, kwidth * kh / kw)
        except (OSError, ValueError, struct.error, zlib.error):
            mark = None

    # A short list under the mark, top line ticked. Three rows of decreasing
    # width read as a list at full size and as a solid block in the Dock, which
    # is the right behaviour both ways.
    bar_h = W * 0.050
    gap = W * 0.078
    top = W * 0.675
    left = W * 0.300
    tick = tick_mark(W * 0.180, top - bar_h * 0.25, bar_h * 1.5, bar_h * 0.34)
    bars = [
        (rounded(left, top + i * gap, w, bar_h, bar_h / 2), alpha)
        for i, (w, alpha) in enumerate(
            ((W * 0.380, 255), (W * 0.300, 140), (W * 0.220, 80))
        )
    ]

    px = []
    for py in range(W):
        row = []
        for pxx in range(W):
            if not body(pxx, py):
                row.append((0, 0, 0, 0))
                continue
            t = py / W
            colour = tuple(int(a + (b - a) * t) for a, b in zip(INK_TOP, INK))

            if mark is not None:
                img, sw, sh, x, y, w, h = mark
                if x <= pxx < x + w and y <= py < y + h:
                    r, g, b, a = sample(img, sw, sh, (pxx - x) / w, (py - y) / h)
                    if a:
                        colour = tuple(
                            int(c * (255 - a) / 255 + s * a / 255)
                            for c, s in zip(colour, (r, g, b))
                        )

            if tick(pxx, py):
                colour = TICK
            else:
                for shape, alpha in bars:
                    if shape(pxx, py):
                        colour = tuple(
                            int(c * (255 - alpha) / 255 + s * alpha / 255)
                            for c, s in zip(colour, CHALK)
                        )
                        break
            row.append((*colour, 255))
        px.append(row)
    return px


def downsample(px) -> bytes:
    out = bytearray()
    for y in range(SIZE):
        out.append(0)
        for x in range(SIZE):
            acc = [0, 0, 0, 0]
            for dy in range(SS):
                for dx in range(SS):
                    p = px[y * SS + dy][x * SS + dx]
                    for c in range(4):
                        acc[c] += p[c]
            out.extend(v // (SS * SS) for v in acc)
    return bytes(out)


def png(raw: bytes, path: Path) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "packaging/icon.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    png(downsample(draw()), target)
    print(f"wrote {target}")
