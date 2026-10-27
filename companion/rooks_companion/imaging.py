"""Just enough PNG handling (standard library only) to black out areas of a screenshot before it leaves this computer."""

from __future__ import annotations

import struct
import zlib


def decode(data: bytes) -> tuple[int, int, int, bytearray]:
    """(width, height, channels, pixels) for 8-bit, non-interlaced RGB or RGBA PNGs, which screenshots are."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w = 8, b"", 0
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in (2, 6) or interlace:
                raise ValueError("unsupported PNG layout")
            ch = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    i = 0
    for y in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - ch] if x >= ch else 0
            b = prev[x]
            c = prev[x - ch] if x >= ch else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, out


def encode(w: int, h: int, ch: int, px: bytes | bytearray) -> bytes:
    stride = w * ch
    raw = b"".join(b"\x00" + bytes(px[y * stride:(y + 1) * stride]) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2 if ch == 3 else 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def black_out(png: bytes, rects: list[tuple[int, int, int, int]]) -> bytes:
    """Fill rectangles (x, y, w, h in image pixels) with black. Rectangles outside the image are clipped."""
    if not rects:
        return png
    w, h, ch, px = decode(png)
    for x, y, rw, rh in rects:
        x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(w, int(x + rw)), min(h, int(y + rh))
        for yy in range(y0, y1):
            row = yy * w * ch
            for xx in range(x0, x1):
                o = row + xx * ch
                px[o:o + 3] = b"\x00\x00\x00"
    return encode(w, h, ch, px)


def size(png: bytes) -> tuple[int, int]:
    return struct.unpack(">II", png[16:24])
