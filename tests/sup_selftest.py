#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for PGS (.sup) input: parse, decode and re-export."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from sub2sup import formats, pgs  # noqa: E402


def main() -> int:
    sup = ROOT / 'demo' / 'demo.sup'
    sub = formats.open_subtitle(sup)
    assert getattr(sub, 'is_sup', False)
    assert sub.entries, 'no entries'
    assert sub.size == (1920, 1080), sub.size
    decoded = []
    for entry in sub.iter_entries():
        d = sub.decode(entry)
        assert d.rows and d.width > 0 and d.height > 0
        decoded.append(d)
    out = Path(tempfile.gettempdir()) / 'sub2sup-sup-roundtrip.sup'
    items = [pgs.PgsItem(d.start, d.end, d.x, d.y, d.width, d.height,
                         d.rows, d.colors) for d in decoded]
    pgs.write_sup(items, str(out), sub.size[0], sub.size[1], 23.976)
    rep = pgs.inspect_sup(str(out), expected_canvas=sub.size,
                          expected_fps=pgs.fps_code_for(23.976))
    assert not rep['issues'], rep['issues']
    assert rep['display_sets'] >= len(items)
    print(f'PASS: SUP input {len(decoded)} segments -> {out.name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
