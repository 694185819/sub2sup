"""Small RGBA helpers: PNG encoding and nearest-neighbour preview rendering."""
from __future__ import annotations

import struct
import zlib


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    if width <= 0 or height <= 0:
        raise ValueError('bad image size')
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) +
            chunk(b'IDAT', zlib.compress(bytes(raw), 6)) + chunk(b'IEND', b''))


def _premul(color: tuple[int, int, int, int]) -> tuple[int, int, int]:
    r, g, b, a = color
    if a >= 255:
        return (r, g, b)
    if a <= 0:
        return (0, 0, 0)
    return (r * a // 255, g * a // 255, b * a // 255)


def thumb(rows: list[bytearray], colors: list[tuple[int, int, int, int]],
          max_w: int = 240, max_h: int = 40) -> tuple[int, int, bytes]:
    """Crop to the object area and scale it down, over a black background."""
    h = len(rows)
    w = len(rows[0]) if h else 0
    scale = min(max_w / w, max_h / h, 1.0)
    tw = max(1, min(max_w, round(w * scale)))
    th = max(1, min(max_h, round(h * scale)))
    prem = [_premul(c) for c in colors]
    buf = bytearray(b'\x00\x00\x00\xff') * (tw * th)
    for y in range(th):
        sy = min(h - 1, int(y * h / th))
        row = rows[sy]
        base = (y * tw) * 4
        for x in range(tw):
            sx = min(w - 1, int(x * w / tw))
            r, g, b = prem[row[sx] & 3]
            o = base + x * 4
            buf[o] = r
            buf[o + 1] = g
            buf[o + 2] = b
    return tw, th, bytes(buf)


def preview(rows: list[bytearray], colors: list[tuple[int, int, int, int]],
            video_w: int, video_h: int, x: int, y: int,
            out_w: int = 960) -> tuple[int, int, bytes]:
    """Render the subtitle on a black video frame, scaled to ``out_w`` wide."""
    out_w = max(64, int(out_w))
    scale = out_w / video_w
    out_h = max(1, round(video_h * scale))
    buf = bytearray(b'\x00\x00\x00\xff') * (out_w * out_h)
    h = len(rows)
    w = len(rows[0]) if h else 0
    if w == 0 or h == 0:
        return out_w, out_h, bytes(buf)
    dx = max(0, round(x * scale))
    dy = max(0, round(y * scale))
    dw = max(1, round(w * scale))
    dh = max(1, round(h * scale))
    dw = min(dw, out_w - dx)
    dh = min(dh, out_h - dy)
    prem = [_premul(c) for c in colors]
    for yy in range(dh):
        sy = min(h - 1, int(yy * h / dh))
        row = rows[sy]
        base = ((dy + yy) * out_w + dx) * 4
        for xx in range(dw):
            sx = min(w - 1, int(xx * w / dw))
            r, g, b = prem[row[sx] & 3]
            o = base + xx * 4
            buf[o] = r
            buf[o + 1] = g
            buf[o + 2] = b
    return out_w, out_h, bytes(buf)
