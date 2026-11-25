"""Command line interface (used for testing / power users).

Run from source:
    py -m sub2sup --cli "movie.idx" --list
    py -m sub2sup --cli "movie.idx" --out movie.sup --drop 0,1088
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import pgs, render, vobsub


def parse_index_list(text: str, total: int) -> set[int]:
    out: set[int] = set()
    if not text:
        return out
    for part in text.replace(' ', '').split(','):
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            start, end = int(a), int(b)
            if end < start:
                start, end = end, start
            out.update(range(start, end + 1))
        else:
            out.add(int(part))
    bad = [i for i in out if i < 0 or i >= total]
    if bad:
        raise SystemExit(f'序号超出范围: {bad[:5]}')
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='sub2sup', description='VobSub(idx/sub) → PGS(sup) 无损转换')
    ap.add_argument('input')
    ap.add_argument('--out', '-o')
    ap.add_argument('--list', action='store_true', help='打印字幕段列表')
    ap.add_argument('--drop', default='', help='要删除的段序号，如 0,5,10-12')
    ap.add_argument('--keep', default='', help='仅保留这些段序号')
    ap.add_argument('--track', type=int, default=None)
    ap.add_argument('--canvas', default=None, help='单 sub 时指定画布，如 1920x1080')
    ap.add_argument('--fps', type=float, default=23.976)
    ap.add_argument('--offset-ms', type=int, default=0)
    ap.add_argument('--dump', default=None, help='把每段预览 PNG 输出到目录')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args(argv)

    v = vobsub.VobSub(args.input)
    track = args.track
    if track is None:
        track = min(max(0, v.idx.langidx if v.idx else 0), len(v.tracks) - 1)
    entries = v.iter_entries(track)
    total = len(entries)
    print(f'文件: {v.idx_path or v.sub_path}')
    print(f'轨道: {v.track_names()[track]}  共 {total} 段  画布: {v.size}')
    if args.list:
        for i, e in enumerate(entries):
            print(f'#{i:05d}  {vobsub.ticks_to_time(e.start)}  filepos={e.filepos:#x}')
        return 0

    drop = parse_index_list(args.drop, total)
    keep = set(range(total))
    if args.keep:
        keep = parse_index_list(args.keep, total)
        drop = set(range(total)) - keep
    if args.limit:
        keep = {i for i in keep if i < args.limit}

    canvas = v.size
    if args.canvas:
        w, h = args.canvas.lower().split('x')
        canvas = (int(w), int(h))
    if canvas is None:
        canvas = (1920, 1080)
        print('警告: 未指定画布，使用 1920x1080')

    out = args.out or str(Path(args.input).with_suffix('.sup'))

    t0 = time.time()
    items: list[pgs.PgsItem] = []
    kept = 0
    last_pct = -1
    for i, sub in enumerate(v.decode_all(track)):
        if args.limit and i >= args.limit:
            break
        if i in drop:
            continue
        start = sub.start + args.offset_ms * 90
        end = sub.end + args.offset_ms * 90
        if start < 0:
            start = 0
        items.append(pgs.PgsItem(start, end, sub.x, sub.y, sub.width, sub.height,
                                 sub.rows, sub.colors))
        kept += 1
        pct = int(kept * 100 / max(1, total - len(drop)))
        if pct != last_pct:
            last_pct = pct
            print(f'\r解码 {kept}/{total - len(drop)} 段 ({pct}%)', end='', flush=True)
        if args.dump:
            d = Path(args.dump)
            d.mkdir(parents=True, exist_ok=True)
            tw, th, buf = render.thumb(sub.rows, sub.colors, 960, 240)
            (d / f'{i:05d}.png').write_bytes(render.png_bytes(tw, th, buf))
    print()

    stats = pgs.write_sup(items, out, canvas[0], canvas[1], args.fps,
                          progress=lambda a, b: print(f'\r写入 {a}/{b}', end='', flush=True) if a % 50 == 0 else None)
    print(f'\r写入完成: {kept} 段, 清除显示集 {stats["clears"]} 个')
    print(f'输出: {out}  ({Path(out).stat().st_size / 1024:.1f} KiB)  用时 {time.time() - t0:.1f}s')
    if args.verify:
        segs = pgs.scan_sup(out)
        pcs = sum(1 for s in segs if s['type'] == pgs.SEG_PCS)
        print(f'自检: {len(segs)} 个段, {pcs} 个 PCS')
        assert pcs == kept + stats['clears'], (pcs, kept, stats['clears'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
