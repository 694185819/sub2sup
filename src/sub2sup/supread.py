"""Read Blu-ray PGS (.sup) files into VobSub-like segment objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import pgs, vobsub

SEG_END = 0x80


@dataclass
class PObject:
    obj_id: int
    window_id: int
    flags: int
    x: int
    y: int


@dataclass
class DisplaySet:
    pts: int
    pcs: bytes
    objects: list[PObject] = field(default_factory=list)
    pds: bytes | None = None
    wds: bytes | None = None
    ods: dict[int, tuple[int, int, bytearray, bool]] = field(default_factory=dict)
    video_w: int = 0
    video_h: int = 0
    fps_code: int = 0
    palette_id: int = 0
    state: int = 0


def _be16(b: bytes, o: int = 0) -> int:
    return (b[o] << 8) | b[o + 1]


def _parse_pcs(payload: bytes) -> tuple[int, int, int, int, int, list[PObject]]:
    if len(payload) < 11:
        raise ValueError('PCS too short')
    w = _be16(payload, 0)
    h = _be16(payload, 2)
    fps_code = payload[4]
    state = payload[8]
    palette_id = payload[9]
    n = payload[10]
    objs: list[PObject] = []
    q = 11
    for _ in range(n):
        if q + 8 > len(payload):
            break
        oid = _be16(payload, q)
        win = payload[q + 2]
        flags = payload[q + 3]
        x = _be16(payload, q + 4)
        y = _be16(payload, q + 6)
        objs.append(PObject(oid, win, flags, x, y))
        q += 8
    return w, h, fps_code, state, palette_id, objs


def _parse_pds(payload: bytes) -> dict[int, tuple[int, int, int, int]]:
    if len(payload) < 2:
        return {}
    out: dict[int, tuple[int, int, int, int]] = {}
    for k in range(2, len(payload) - 4, 5):
        idx = payload[k]
        out[idx] = (payload[k + 1], payload[k + 2], payload[k + 3], payload[k + 4])
    return out


def _read_sets(path: str) -> list[DisplaySet]:
    segs = pgs.scan_sup(path)
    sets: list[DisplaySet] = []
    cur: DisplaySet | None = None
    for s in segs:
        t = s['type']
        p = s['payload']
        if t == pgs.SEG_PCS:
            w, h, fps, state, pal_id, objs = _parse_pcs(p)
            cur = DisplaySet(pts=s['pts'], pcs=p, objects=objs,
                             video_w=w, video_h=h, fps_code=fps,
                             palette_id=pal_id, state=state)
            sets.append(cur)
        elif cur is None:
            continue
        elif t == pgs.SEG_PDS:
            cur.pds = p
        elif t == pgs.SEG_WDS:
            cur.wds = p
        elif t == pgs.SEG_ODS:
            if len(p) < 4:
                continue
            oid = _be16(p, 0)
            flags = p[3]
            if flags & 0x80:
                if len(p) < 11:
                    continue
                w = _be16(p, 7)
                h = _be16(p, 9)
                data = bytearray(p[11:])
                cur.ods[oid] = (w, h, data, bool(flags & 0x40))
            elif oid in cur.ods:
                w, h, data, last = cur.ods[oid]
                data += p[4:]
                cur.ods[oid] = (w, h, data, bool(flags & 0x40) or last)
    return sets


def _palette_rgba(palette: dict[int, tuple[int, int, int, int]],
                  video_h: int) -> dict[int, tuple[int, int, int, int]]:
    bt709 = video_h > 576
    out = {}
    for idx, (y, cr, cb, a) in palette.items():
        r, g, b = pgs._decode_yuv(y, cr, cb, bt709)
        out[idx] = (r, g, b, a)
    return out


class SupSubtitle:
    """VobSub-like backend for PGS .sup input."""

    is_sup = True

    def __init__(self, path: str | Path):
        self.sub_path = Path(path)
        self.idx_path = None
        self.idx = None
        self.tracks = [object()]
        self._track_names = ['PGS 字幕 / PGS']
        self.warnings: list[str] = []
        sets = _read_sets(str(path))
        if not sets:
            raise ValueError('SUP 中没有 PGS 显示集')
        sizes = {(s.video_w, s.video_h) for s in sets if s.video_w and s.video_h}
        self.size = next(iter(sizes)) if sizes else (1920, 1080)
        code_to_fps = {v: k for k, v in pgs.FPS_CODES.items()}
        self.fps = next((code_to_fps[s.fps_code] for s in sets if s.fps_code in code_to_fps),
                        23.976)
        if len(sizes) > 1:
            self.warnings.append(f'PGS 画布不一致: {sorted(sizes)}')
        self._sets = sets
        self._palette: dict[int, tuple[int, int, int, int]] = {}
        self._cache: dict[int, vobsub.Subtitle] = {}
        self.entries: list[vobsub.Entry] = []
        self._entry_sets: list[DisplaySet] = []
        self._decode_sets()

    def track_names(self) -> list[str]:
        return list(self._track_names)

    def iter_entries(self, track: int = 0) -> list[vobsub.Entry]:
        return self.entries

    def decode_all(self, track: int = 0, progress=None, cancel=None):
        for i, entry in enumerate(self.entries):
            if cancel is not None and cancel():
                return
            if progress is not None:
                progress(i + 1, len(self.entries))
            yield self.decode(entry)

    def decode(self, entry: vobsub.Entry, end_fallback: int | None = None) -> vobsub.Subtitle:
        cached = self._cache.get(entry.index)
        if cached is not None:
            return cached
        try:
            ds = self._entry_sets[entry.index]
        except IndexError:
            ds = None
        sub = self._decode_display_set(ds, entry.index) if ds is not None else None
        if sub is None:
            sub = vobsub.Subtitle(entry.index, entry.start, entry.start + 1,
                                  0, 0, 1, 1, [bytearray(1)],
                                  [(0, 0, 0, 0)], self._warnings_copy())
        else:
            sub.start = entry.start
            sub.end = entry.end
        if len(self._cache) > 8:
            self._cache.clear()
        self._cache[entry.index] = sub
        return sub

    def _decode_sets(self) -> None:
        open_ds: DisplaySet | None = None
        ends: list[int] = []
        for ds in self._sets:
            if ds.pds is not None:
                pal = _parse_pds(ds.pds)
                if pal:
                    self._palette = pal
            if not ds.objects:
                if open_ds is not None:
                    ends.append(max(open_ds.pts + 1, ds.pts))
                    self.entries.append(vobsub.Entry(len(self.entries), open_ds.pts, 0, 0))
                    self._entry_sets.append(open_ds)
                    open_ds = None
                continue
            if open_ds is not None:
                ends.append(max(open_ds.pts + 1, ds.pts))
                self.entries.append(vobsub.Entry(len(self.entries), open_ds.pts, 0, 0))
                self._entry_sets.append(open_ds)
                open_ds = None
            open_ds = ds
        if open_ds is not None:
            ends.append(open_ds.pts + 90000)
            self.entries.append(vobsub.Entry(len(self.entries), open_ds.pts, 0, 0))
            self._entry_sets.append(open_ds)
            self.warnings.append('最后一个 PGS 段没有结束清屏，按 1 秒处理')
        for i, e in enumerate(self.entries):
            e.end = max(e.start + 1, ends[i] if i < len(ends) else e.start + 90000)
            e.index = i

    def _decode_display_set(self, ds: DisplaySet, index: int) -> vobsub.Subtitle | None:
        objs = []
        for obj in ds.objects:
            data = ds.ods.get(obj.obj_id)
            if data is None:
                continue
            w, h, rle, _last = data
            if w <= 0 or h <= 0:
                continue
            rows = pgs.decode_rle(bytes(rle), w, h)
            if rows is None:
                self.warnings.append(f'对象 {obj.obj_id} RLE 解码失败')
                continue
            objs.append((obj, w, h, rows))
        if not objs:
            return None
        if len(objs) == 1:
            obj, w, h, rows = objs[0]
            colors = self._colors_for_rows(rows)
            if not colors:
                return None
            return vobsub.Subtitle(index, ds.pts, ds.pts, obj.x, obj.y, w, h,
                                   rows, colors, self._warnings_copy())
        return self._compose_objects(objs, ds, index)

    def _warnings_copy(self) -> list[str]:
        return list(self.warnings)

    def _colors_for_rows(self, rows: list[bytearray]) -> list[tuple[int, int, int, int]]:
        max_idx = 0
        for row in rows:
            if row:
                max_idx = max(max_idx, max(row))
        if max_idx > 254:
            return []
        pal = _palette_rgba(self._palette, self.size[1])
        return [pal.get(i, (0, 0, 0, 0)) for i in range(max_idx + 1)]

    def _compose_objects(self, objs, ds: DisplaySet, index: int):
        try:
            from PIL import Image
            from . import textsub
        except Exception:
            self.warnings.append('多对象 PGS 需要 Pillow 才能合成')
            return None
        pal = _palette_rgba(self._palette, self.size[1])
        pieces = []
        for obj, w, h, rows in objs:
            im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            px = im.load()
            for y, row in enumerate(rows):
                for x, v in enumerate(row):
                    c = pal.get(v)
                    if c and c[3]:
                        px[x, y] = c
            pieces.append((im, obj.x, obj.y))
        left = max(0, min(x for _im, x, _y in pieces))
        top = max(0, min(y for _im, _x, y in pieces))
        right = min(self.size[0], max(x + im.width for im, x, _y in pieces))
        bottom = min(self.size[1], max(y + im.height for im, _x, y in pieces))
        if right <= left or bottom <= top:
            return None
        canvas = Image.new('RGBA', (right - left, bottom - top), (0, 0, 0, 0))
        for im, x, y in pieces:
            canvas.alpha_composite(im, (x - left, y - top))
        rows, colors = textsub._quantize_image(canvas)
        if not rows or not colors:
            return None
        return vobsub.Subtitle(index, ds.pts, ds.pts, left, top,
                               right - left, bottom - top, rows, colors,
                               self._warnings_copy())
