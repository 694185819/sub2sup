"""Command line interface (used for testing / power users).

Run from source:
    py -m sub2sup --cli "movie.idx" --list
    py -m sub2sup --cli "movie.idx" --out movie.sup --drop 0,1088
    py -m sub2sup --cli "movie.idx" --out movie-uhd.sup --profile uhd
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import formats, pgs, render, vsrender, vobsub


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
    ap = argparse.ArgumentParser(prog='sub2sup',
                                 description='VobSub(idx/sub) → PGS(sup) 无损转换 / UHD HDR 输出')
    ap.add_argument('input')
    ap.add_argument('--out', '-o')
    ap.add_argument('--list', action='store_true', help='打印字幕段列表')
    ap.add_argument('--drop', default='', help='要删除的段序号，如 0,5,10-12')
    ap.add_argument('--keep', default='', help='仅保留这些段序号')
    ap.add_argument('--track', type=int, default=None)
    ap.add_argument('--profile', choices=['bluray', 'uhd', '4k'], default='bluray',
                    help='bluray=1080p SDR；uhd=1920x1080 PGS + HDR 亮度 + UHD 帧率；'
                         '4k=3840x2160 PGS（试验）')
    ap.add_argument('--hdr-target', type=int, default=None,
                    help='HDR 字幕峰值亮度 Y（16-235）。uhd/4k 默认 140；235 表示保持原白')
    ap.add_argument('--canvas', default=None, help='指定画布，如 1920x1080 / 3840x2160')
    ap.add_argument('--fps', type=float, default=23.976)
    ap.add_argument('--offset-ms', type=int, default=0)
    ap.add_argument('--dump', default=None, help='把每段预览 PNG 输出到目录')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--vsrender', action='store_true',
                    help='用 easyavs2bdnxml + VSFilterMod 渲染 ASS 到 SUP（需要外部组件）')
    ap.add_argument('--avs2bdnxml', default=None, help='avs2bdnxml.exe 路径')
    ap.add_argument('--vsfilter', default=None, help='VSFilterMod.dll 路径')
    args = ap.parse_args(argv)

    if args.vsrender:
        p = Path(args.input)
        if p.suffix.lower() not in ('.ass', '.ssa'):
            raise SystemExit('--vsrender 只支持 ASS/SSA 输入')
        comp = vsrender.detect_components()
        avs = args.avs2bdnxml or comp.get('avs2bdnxml')
        vf = args.vsfilter or comp.get('vsfilter')
        if not avs or not vf:
            raise SystemExit('未找到 avs2bdnxml.exe / VSFilterMod.dll，请用 --avs2bdnxml/--vsfilter 指定')
        out = Path(args.out) if args.out else p.with_suffix('.vs.sup')
        last = [-1]
        def progress(done: int, total: int) -> None:
            pct = int(done * 100 / max(1, total))
            if pct != last[0]:
                last[0] = pct
                print(f'\r高级渲染 {done}/{total} ({pct}%)', end='', flush=True)
        print(f'高级渲染: {p} -> {out}')
        result = vsrender.render_ass(str(p), str(out), avs, vf, fps=args.fps,
                                     progress=progress)
        print(f'\n完成: {result["entries"]} 段, 画布 {result["size"]}, {out}')
        return 0

    v = formats.open_subtitle(args.input)
    track = args.track
    if track is None:
        track = min(max(0, getattr(v, 'idx', None).langidx if getattr(v, 'idx', None) else 0),
                    len(v.tracks) - 1)
    entries = v.iter_entries(track)
    total = len(entries)
    print(f'文件: {v.idx_path or v.sub_path}')
    print(f'轨道: {v.track_names()[track]}  共 {total} 段  源画布: {v.size}')
    if getattr(v, 'warnings', None):
        for warning in v.warnings[:3]:
            print(f'提示: {warning}')
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

    src_canvas = v.size or (1920, 1080)
    if args.canvas:
        w, h = args.canvas.lower().split('x')
        canvas = (int(w), int(h))
    elif args.profile == 'uhd':
        canvas = (1920, 1080)
    elif args.profile == '4k':
        canvas = (3840, 2160)
    else:
        canvas = src_canvas

    hdr_target = None
    if args.profile != 'bluray':
        hdr_target = pgs.HDR_DEFAULT if args.hdr_target is None else args.hdr_target
        if not 16 <= hdr_target <= 235:
            raise SystemExit('--hdr-target 必须在 16..235')
        if hdr_target >= 235:
            hdr_target = None
        if args.fps not in pgs.UHD_FPS:
            raise SystemExit(f'UHD 模式不支持 {args.fps} fps；可用: {pgs.UHD_FPS}')
    elif args.hdr_target is not None:
        hdr_target = None if args.hdr_target >= 235 else args.hdr_target

    sx = canvas[0] / src_canvas[0]
    sy = canvas[1] / src_canvas[1]
    if getattr(v, 'is_text', False):
        v.set_render_size(canvas[0], canvas[1])
        sx = sy = 1.0
    print(f'输出规格: {args.profile}  画布: {canvas}  HDR 峰值Y: {hdr_target}  '
          f'帧率: {args.fps}')
    if (sx, sy) != (1.0, 1.0):
        print(f'缩放: {sx:.4f}x{sy:.4f}' + ('（非整数）' if sx != int(sx) or sy != int(sy) else ''))

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
        item = pgs.PgsItem(start, end, sub.x, sub.y, sub.width, sub.height,
                           sub.rows, sub.colors)
        if (sx, sy) != (1.0, 1.0):
            item = pgs.scale_item(item, sx, sy)
        items.append(item)
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
                          progress=lambda a, b: print(f'\r写入 {a}/{b}', end='', flush=True) if a % 50 == 0 else None,
                          hdr_target_y=hdr_target)
    print(f'\r写入完成: {kept} 段, 清除显示集 {stats["clears"]} 个')
    print(f'输出: {out}  ({Path(out).stat().st_size / 1024:.1f} KiB)  用时 {time.time() - t0:.1f}s')
    if args.verify:
        report = pgs.inspect_sup(out, expected_canvas=canvas,
                                 expected_fps=pgs.fps_code_for(args.fps),
                                 hdr_target_y=hdr_target)
        print(f'自检: {report["segments"]} 个段, {report["display_sets"]} 个显示集, '
              f'画布={report["dims"]}, 帧率={report["fps_codes"]}, '
              f'峰值Y={report["peak_y"]}, 最大ODS={report["max_ods"]}')
        if report['issues']:
            for issue in report['issues']:
                print('  !', issue)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
