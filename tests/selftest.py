#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self test: convert a VobSub file and verify the generated .sup against the source.

Usage:
    py -3 tests\\selftest.py "movie.idx" [--limit N] [--out FILE] [--track N]

Checks performed:
  * every display set exists at the source start timestamp
  * decoded PGS bitmap rows equal the source 2-bit bitmap (palette index mapping)
  * object placement (x / y / w / h) equals the source
  * palette colours decode back to the source RGBA within 1/255
  * display sets are monotonic in time
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from sub2sup import pgs, vobsub  # noqa: E402


class DisplaySet:
    def __init__(self, pts: int):
        self.pts = pts
        self.pcs = b''
        self.pds = b''
        self.ods: list[bytes] = []


def group_display_sets(segments):
    out = []
    cur = None
    for seg in segments:
        t = seg['type']
        if t == pgs.SEG_PCS:
            cur = DisplaySet(seg['pts'])
            cur.pcs = seg['payload']
            out.append(cur)
        elif cur is not None:
            if t == pgs.SEG_ODS:
                cur.ods.append(seg['payload'])
            elif t == pgs.SEG_PDS:
                cur.pds = seg['payload']
    return out


def decode_ods(chain):
    first = chain[0]
    if not (first[3] & 0x80):
        raise ValueError('ODS 起始标志错误')
    w = int.from_bytes(first[7:9], 'big')
    h = int.from_bytes(first[9:11], 'big')
    rle = bytearray(first[11:])
    for extra in chain[1:]:
        rle += extra[4:]
    rows = pgs.decode_rle(bytes(rle), w, h)
    if rows is None:
        raise ValueError('RLE 校验失败')
    return w, h, rows


def verify(src: Path, limit: int = 0, track: int = 0) -> tuple[bool, str]:
    v = vobsub.VobSub(str(src))
    if track >= len(v.tracks):
        return False, f'轨道 {track} 不存在（共 {len(v.tracks)} 轨）'
    entries = v.iter_entries(track)
    subs = []
    for i, sub in enumerate(v.decode_all(track)):
        if limit and i >= limit:
            break
        subs.append(sub)
    if not subs:
        return False, '没有可校验的字幕段'
    vh = v.size[1] if v.size else 1080
    vw = v.size[0] if v.size else 1920
    with tempfile.TemporaryDirectory(prefix='sub2sup_') as td:
        out = Path(td) / 'test.sup'
        items = [pgs.PgsItem(s.start, s.end, s.x, s.y, s.width, s.height, s.rows, s.colors)
                 for s in subs]
        pgs.write_sup(items, str(out), vw, vh, 23.976)
        segs = pgs.scan_sup(str(out))
    ds = group_display_sets(segs)
    row_err = geo_err = colour_err = time_err = 0
    max_delta = 0
    di = 0
    for sub in subs:
        while di < len(ds) and ds[di].pts < sub.start:
            di += 1
        if di >= len(ds):
            time_err += 1
            break
        d = ds[di]
        di += 1
        if d.pts != sub.start:
            time_err += 1
        if not d.ods:
            continue
        w, h, rows = decode_ods(d.ods)
        if (w, h) != (sub.width, sub.height):
            geo_err += 1
            continue
        for y in range(h):
            expect = bytes((val + 1) & 7 for val in sub.rows[y])
            if bytes(rows[y]) != expect:
                row_err += 1
                break
        p = d.pcs
        if p and p[10] == 1:
            x = int.from_bytes(p[15:17], 'big')
            y = int.from_bytes(p[17:19], 'big')
            if (x, y) != (sub.x, sub.y):
                geo_err += 1
        pal = {}
        for k in range(2, len(d.pds), 5):
            idx = d.pds[k]
            yy, cr, cb, a = d.pds[k + 1:k + 5]
            r, g, b = pgs._decode_yuv(yy, cr, cb, vh > 576)
            pal[idx] = (r, g, b, a)
        for k in range(4):
            got = pal.get(k + 1)
            if got is None:
                colour_err += 1
                continue
            delta = max(abs(got[i] - sub.colors[k][i]) for i in range(4))
            max_delta = max(max_delta, delta)
            if delta > 1:
                colour_err += 1
    pts = [d.pts for d in ds]
    non_mono = sum(1 for i in range(1, len(pts)) if pts[i] < pts[i - 1])
    ok = (row_err == 0 and geo_err == 0 and colour_err == 0 and time_err == 0 and non_mono == 0)
    msg = (f'{"PASS" if ok else "FAIL"}: {len(subs)} 段, 显示集 {len(ds)}, '
           f'位图错误 {row_err}, 位置错误 {geo_err}, 颜色错误 {colour_err}'
           f'(最大误差 {max_delta}), 时间错误 {time_err}, 乱序 {non_mono}')
    return ok, msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--track', type=int, default=0)
    args = ap.parse_args()
    ok, msg = verify(Path(args.input), args.limit, args.track)
    print(msg)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
