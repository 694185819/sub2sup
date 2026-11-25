"""PGS (.sup) writing helpers: palette conversion and RLE encoding.

The PGS RLE format (as implemented by FFmpeg's pgssub decoder) is:

    colour byte != 0x00        -> one pixel of that palette index
    0x00, flags, [len_hi], [colour]
        flags & 0x3f            low 6 bits of run length
        flags & 0x40            8 more run length bits follow
        flags & 0x80            explicit colour byte follows
        run length 0            -> new line

We map the four DVD pixel values (0..3) to PGS palette indices 1..4 so that no
pixel of value 0 is ever emitted as a bare RLE byte (0x00 is the escape).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

SEG_PDS = 0x14
SEG_ODS = 0x15
SEG_PCS = 0x16
SEG_WDS = 0x17
SEG_END = 0x80

FPS_CODES: dict[float, int] = {
    23.976: 0x10, 24.0: 0x20, 25.0: 0x30, 29.97: 0x40,
    30.0: 0x50, 50.0: 0x60, 59.94: 0x70, 60.0: 0x80,
}
FPS_LABELS: list[tuple[str, float]] = [
    ('23.976 (Blu-ray 电影常见)', 23.976),
    ('24', 24.0),
    ('25 (PAL)', 25.0),
    ('29.97 (NTSC)', 29.97),
    ('30', 30.0),
    ('50', 50.0),
    ('59.94', 59.94),
    ('60', 60.0),
]

SCALEBITS = 10
ONE_HALF = 1 << (SCALEBITS - 1)


def _fix(x: float) -> int:
    # C: (int)(x * 1024 + 0.5)  -> truncation toward zero
    return int(x * (1 << SCALEBITS) + 0.5)


def _decode_yuv(y: int, cr: int, cb: int, bt709: bool) -> tuple[int, int, int]:
    """Exact port of FFmpeg's YUV_TO_RGB1_CCIR[_BT709] + YUV_TO_RGB2_CCIR."""
    cb_ = cb - 128
    cr_ = cr - 128
    if bt709:
        r_add = ONE_HALF + _fix(1.5747 * 255.0 / 224.0) * cr_
        g_add = ONE_HALF - _fix(0.1873 * 255.0 / 224.0) * cb_ - _fix(0.4682 * 255.0 / 224.0) * cr_
        b_add = ONE_HALF + _fix(1.8556 * 255.0 / 224.0) * cb_
    else:
        r_add = _fix(1.40200 * 255.0 / 224.0) * cr_ + ONE_HALF
        g_add = -_fix(0.34414 * 255.0 / 224.0) * cb_ - _fix(0.71414 * 255.0 / 224.0) * cr_ + ONE_HALF
        b_add = _fix(1.77200 * 255.0 / 224.0) * cb_ + ONE_HALF
    yy = (y - 16) * _fix(255.0 / 219.0)
    r = (yy + r_add) >> SCALEBITS
    g = (yy + g_add) >> SCALEBITS
    b = (yy + b_add) >> SCALEBITS
    return (min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b)))


_YUV_CACHE: dict[tuple[tuple[int, int, int], bool], tuple[int, int, int]] = {}


def rgb_to_ycbcr(rgb: tuple[int, int, int], bt709: bool) -> tuple[int, int, int]:
    """Pick Y/Cr/Cb that decode back to exactly ``rgb`` under FFmpeg's maths."""
    key = (rgb, bt709)
    cached = _YUV_CACHE.get(key)
    if cached is not None:
        return cached
    r, g, b = rgb
    if bt709:
        y = 16.0 + (0.2126 * r + 0.7152 * g + 0.0722 * b) * 219.0 / 255.0
        cb = 128.0 + (-0.1146 * r - 0.3854 * g + 0.5000 * b) * 224.0 / 255.0
        cr = 128.0 + (0.5000 * r - 0.4542 * g - 0.0458 * b) * 224.0 / 255.0
    else:
        y = 16.0 + (0.2990 * r + 0.5870 * g + 0.1140 * b) * 219.0 / 255.0
        cb = 128.0 + (-0.168736 * r - 0.331264 * g + 0.5000 * b) * 224.0 / 255.0
        cr = 128.0 + (0.5000 * r - 0.418688 * g - 0.081312 * b) * 224.0 / 255.0
    y0 = max(16, min(235, round(y)))
    cb0 = max(16, min(240, round(cb)))
    cr0 = max(16, min(240, round(cr)))

    best = None
    best_err = 1 << 30
    for dy in range(-2, 3):
        yy = y0 + dy
        if not 16 <= yy <= 235:
            continue
        for dcb in range(-2, 3):
            ccb = cb0 + dcb
            if not 16 <= ccb <= 240:
                continue
            for dcr in range(-2, 3):
                ccr = cr0 + dcr
                if not 16 <= ccr <= 240:
                    continue
                dr, dg, db = _decode_yuv(yy, ccr, ccb, bt709)
                err = (dr - r) ** 2 + (dg - g) ** 2 + (db - b) ** 2
                if err < best_err:
                    best_err = err
                    best = (yy, ccr, ccb)
                    if err == 0:
                        _YUV_CACHE[key] = best  # type: ignore[assignment]
                        return best  # type: ignore[return-value]
    assert best is not None
    _YUV_CACHE[key] = best
    return best


@dataclass
class PgsItem:
    start: int
    end: int
    x: int
    y: int
    w: int
    h: int
    rows: list[bytearray]      # palette index 0..3
    colors: list[tuple[int, int, int, int]]  # RGBA for 0..3


def _u16(v: int) -> bytes:
    return struct.pack('>H', v)


def _u24(v: int) -> bytes:
    return bytes([(v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])


def encode_rle(rows: list[bytearray], w: int, h: int) -> bytes:
    out = bytearray()
    for y in range(h):
        row = rows[y]
        x = 0
        while x < w:
            c = row[x]
            j = x + 1
            while j < w and row[j] == c:
                j += 1
            run = j - x
            code = c + 1  # PGS palette indices 1..4
            if run == 1:
                out.append(code)
            elif run <= 3:
                out += bytes((code,)) * run
            elif run <= 63:
                out += b'\x00' + bytes((0x80 | run, code))
            else:
                out += b'\x00' + bytes((0xC0 | (run >> 8), run & 0xFF, code))
            x = j
        out += b'\x00\x00'  # end of line
    return bytes(out)


def decode_rle(data: bytes, w: int, h: int) -> list[bytearray] | None:
    """Reference decoder used for self checks (mirrors FFmpeg's rules)."""
    rows: list[bytearray] = []
    cur = bytearray()
    p = 0
    line = 0
    while p < len(data) and line < h:
        color = data[p]
        p += 1
        run = 1
        if color == 0:
            if p >= len(data):
                return None
            flags = data[p]
            p += 1
            run = flags & 0x3F
            if flags & 0x40:
                if p >= len(data):
                    return None
                run = (run << 8) + data[p]
                p += 1
            color = data[p] if flags & 0x80 else 0
            if flags & 0x80:
                p += 1
        if run > 0:
            cur += bytes((color,)) * run
            if len(cur) > w:
                return None
        else:
            if len(cur) != w:
                return None
            rows.append(cur)
            cur = bytearray()
            line += 1
    if len(rows) != h:
        return None
    return rows


def build_palette(colors: list[tuple[int, int, int, int]], video_height: int) -> list[tuple[int, int, int, int, int]]:
    """Return PGS palette entries (index, Y, Cr, Cb, alpha)."""
    bt709 = video_height > 576
    entries = [(0, 16, 128, 128, 0)]
    for i, (r, g, b, a) in enumerate(colors):
        y, cr, cb = rgb_to_ycbcr((r, g, b), bt709)
        entries.append((i + 1, y, cr, cb, min(255, max(0, a))))
    return entries


def _pcs(video_w: int, video_h: int, fps_code: int, comp: int, state: int,
         objects: list[tuple[int, int, int, int, int]]) -> bytes:
    payload = bytearray()
    payload += _u16(video_w) + _u16(video_h) + bytes((fps_code,))
    payload += _u16(comp) + bytes((state, 0x00, 0x00, len(objects)))
    for obj_id, win_id, flags, x, y in objects:
        payload += _u16(obj_id) + bytes((win_id, flags)) + _u16(x) + _u16(y)
    return bytes((SEG_PCS,)) + _u16(len(payload)) + bytes(payload)


def _wds(windows: list[tuple[int, int, int, int, int]]) -> bytes:
    payload = bytearray()
    payload.append(len(windows))
    for win_id, x, y, w, h in windows:
        payload += bytes((win_id,)) + _u16(x) + _u16(y) + _u16(w) + _u16(h)
    return bytes((SEG_WDS,)) + _u16(len(payload)) + bytes(payload)


def _pds(entries: list[tuple[int, int, int, int, int]]) -> bytes:
    payload = bytearray()
    payload += bytes((0, 0))  # palette id, version
    for idx, y, cr, cb, a in entries:
        payload += bytes((idx, y, cr, cb, a))
    return bytes((SEG_PDS,)) + _u16(len(payload)) + bytes(payload)


def _ods(obj_id: int, rows: list[bytearray], w: int, h: int) -> bytes:
    rle = encode_rle(rows, w, h)
    data_len = 4 + len(rle)
    out = bytearray()
    first = True
    offset = 0
    chunk_max = 60_000
    total = len(rle)
    while True:
        chunk = rle[offset:offset + chunk_max]
        if first:
            flags = 0xC0 if offset + len(chunk) >= total else 0x80
            payload = _u16(obj_id) + bytes((0, flags)) + _u24(data_len) + _u16(w) + _u16(h) + chunk
            first = False
        else:
            # continuation: the 0x80 "sequence start" bit must stay clear
            flags = 0x40 if offset + len(chunk) >= total else 0x00
            payload = _u16(obj_id) + bytes((0, flags)) + chunk
        out += bytes((SEG_ODS,)) + _u16(len(payload)) + payload
        offset += len(chunk)
        if offset >= total:
            break
    return bytes(out)


def _end() -> bytes:
    return bytes((SEG_END, 0x00, 0x00))


def write_sup(items: list[PgsItem], out_path: str, video_w: int, video_h: int,
              fps: float = 23.976, progress=None) -> dict:
    fps_code = FPS_CODES.get(round(fps, 2))
    if fps_code is None:
        fps_code = FPS_CODES[23.976]
    comp = 0
    written = 0
    clear_count = 0
    with open(out_path, 'wb') as f:
        def emit(seg: bytes, pts: int) -> None:
            f.write(b'PG' + struct.pack('>II', max(0, pts) & 0xFFFFFFFF,
                                        max(0, pts) & 0xFFFFFFFF))
            f.write(seg)

        for k, item in enumerate(items):
            start = max(0, item.start)
            end = max(start, item.end) if item.end > start else start + 1
            entries = build_palette(item.colors, video_h)
            ds = _pcs(video_w, video_h, fps_code, comp, 0x80, [(0, 0, 0, item.x, item.y)])
            ds += _wds([(0, item.x, item.y, item.w, item.h)])
            ds += _pds(entries)
            ds += _ods(0, item.rows, item.w, item.h)
            ds += _end()
            for seg in _split_segments(ds):
                emit(seg, start)
            comp = (comp + 1) & 0xFFFF
            next_start = items[k + 1].start if k + 1 < len(items) else None
            if next_start is None or end < next_start:
                clear = _pcs(video_w, video_h, fps_code, comp, 0x00, [])
                clear += _wds([(0, item.x, item.y, item.w, item.h)])
                clear += _end()
                for seg in _split_segments(clear):
                    emit(seg, end)
                comp = (comp + 1) & 0xFFFF
                clear_count += 1
            written += 1
            if progress is not None:
                progress(written, len(items))
    return {'items': written, 'clears': clear_count}


def _split_segments(blob: bytes) -> list[bytes]:
    """Split a concatenation of PGS segments into individual segment records."""
    out = []
    pos = 0
    while pos + 3 <= len(blob):
        typ = blob[pos]
        ln = int.from_bytes(blob[pos + 1:pos + 3], 'big')
        total = 3 + ln
        if pos + total > len(blob):
            raise ValueError('internal: bad segment concatenation')
        out.append(blob[pos:pos + total])
        pos += total
    if pos != len(blob):
        raise ValueError('internal: trailing bytes in display set')
    return out


def scan_sup(path: str) -> list[dict]:
    """Parse a .sup back into segments (for verification / statistics)."""
    data = open(path, 'rb').read()
    pos = 0
    out: list[dict] = []
    while pos + 13 <= len(data):
        if data[pos:pos + 2] != b'PG':
            raise ValueError(f'{pos:#x}: not PG')
        pts = int.from_bytes(data[pos + 2:pos + 6], 'big')
        dts = int.from_bytes(data[pos + 6:pos + 10], 'big')
        typ = data[pos + 10]
        ln = int.from_bytes(data[pos + 11:pos + 13], 'big')
        if pos + 13 + ln > len(data):
            raise ValueError(f'{pos:#x}: truncated segment')
        out.append({'pos': pos, 'pts': pts, 'dts': dts, 'type': typ, 'len': ln,
                    'payload': data[pos + 13:pos + 13 + ln]})
        pos += 13 + ln
    if pos != len(data):
        raise ValueError(f'sup has {len(data) - pos} trailing bytes')
    return out
