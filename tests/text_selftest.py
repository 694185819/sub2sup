#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for text subtitle rendering (ASS / SRT / VTT / MicroDVD).

Run from the repository root:
    set PYTHONPATH=src
    py -3 tests\\text_selftest.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from sub2sup import pgs, textsub  # noqa: E402


def convert(path: Path, out: Path, canvas=(1920, 1080)) -> None:
    sub = textsub.TextSubtitle(path)
    sub.set_render_size(*canvas)
    items = []
    for entry in sub.iter_entries():
        d = sub.decode(entry)
        items.append(pgs.PgsItem(d.start, d.end, d.x, d.y, d.width, d.height,
                                 d.rows, d.colors))
    pgs.write_sup(items, str(out), canvas[0], canvas[1], 23.976)
    rep = pgs.inspect_sup(str(out), expected_canvas=canvas,
                          expected_fps=pgs.fps_code_for(23.976))
    assert not rep['issues'], rep['issues']
    assert rep['display_sets'] >= len(items)


def main() -> int:
    demo = ROOT / 'demo'
    convert(demo / 'demo.ass', Path(tempfile.gettempdir()) / 'sub2sup-demo.ass.sup')
    convert(demo / 'demo.srt', Path(tempfile.gettempdir()) / 'sub2sup-demo.srt.sup')
    convert(demo / 'demo.srt', Path(tempfile.gettempdir()) / 'sub2sup-demo.srt-4k.sup',
            (3840, 2160))

    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        long_srt = t / 'long.srt'
        long_srt.write_text(
            '1\n00:00:01,000 --> 00:00:04,000\n'
            + '这是一行很长的中文字幕用来测试自动换行是否会把超过画面宽度的文字拆成多行。'
            + 'This is a long English line used to verify automatic line wrapping.\n',
            encoding='utf-8')
        sub = textsub.TextSubtitle(long_srt)
        d = sub.decode(sub.entries[0])
        assert d.width <= 1800, d.width
        assert d.height > 200, d.height  # wrapped into several visual lines

        clip_ass = t / 'clip.ass'
        clip_ass.write_text(
            '[Script Info]\nPlayResX: 1920\nPlayResY: 1080\n\n'
            '[V4+ Styles]\n'
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
            'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
            'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
            'Alignment, MarginL, MarginR, MarginV, Encoding\n'
            'Style: Default,SimHei,80,&H00FFFFFF,&H000000FF,&H00000000,'
            '&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,20,20,20,1\n\n'
            '[Events]\n'
            'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, '
            'Effect, Text\n'
            'Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,'
            '{\\clip(600,800,1200,1000)\\pos(960,900)}CLIP TEST\n',
            encoding='utf-8')
        sub = textsub.TextSubtitle(clip_ass)
        d = sub.decode(sub.entries[0])
        assert 590 <= d.x < 1210, d.x
        assert d.x + d.width <= 1210, (d.x, d.width)
        assert 790 <= d.y < 1010, d.y
        assert d.y + d.height <= 1010, (d.y, d.height)

    print('PASS: text subtitle renderer')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
