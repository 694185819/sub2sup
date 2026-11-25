"""VobSub (.idx/.sub) reading and DVD SPU decoding.

The goal is a bit-exact conversion to PGS: the 2-bit DVD subpicture bitmap is
decoded to palette indices and re-encoded 1:1 as PGS object data, timing is
kept in 90 kHz ticks, colours are kept as RGBA (converted to PGS' YCbCr in the
writer).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

BLOCK = 2048
TICKS = 90000

# Classic 16 colour VobSub palette (also used by many DVD/BD rips).
DEFAULT_PALETTE: list[tuple[int, int, int]] = [
    (0x00, 0x00, 0x00), (0xF0, 0xF0, 0xF0), (0xCC, 0xCC, 0xCC), (0x99, 0x99, 0x99),
    (0x33, 0x33, 0xFA), (0x11, 0x11, 0xBB), (0xFA, 0x33, 0x33), (0xBB, 0x11, 0x11),
    (0x33, 0xFA, 0x33), (0x11, 0xBB, 0x11), (0xFA, 0xFA, 0x33), (0xBB, 0xBB, 0x11),
    (0xFA, 0x33, 0xFA), (0xBB, 0x11, 0xBB), (0x33, 0xFA, 0xFA), (0x11, 0xBB, 0xBB),
]


class VobSubError(Exception):
    pass


def be16(b: bytes, o: int = 0) -> int:
    return (b[o] << 8) | b[o + 1]


def be32(b: bytes, o: int = 0) -> int:
    return (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]


def ticks_to_time(t: int) -> str:
    t = max(0, int(t))
    ms = t * 1000 // TICKS
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_time(text: str) -> int:
    """Parse HH:MM:SS.mmm / HH:MM:SS:mmm / seconds to 90 kHz ticks."""
    text = text.strip()
    if not text:
        raise ValueError("empty time")
    if ':' in text:
        parts = text.split(':')
        if len(parts) == 4:
            h, m, s, ms = (int(p) for p in parts)
        elif len(parts) == 3:
            h, m = int(parts[0]), int(parts[1])
            sec = parts[2]
            if '.' in sec:
                s, frac = sec.split('.')
                s = int(s)
                ms = int(frac.ljust(3, '0')[:3])
            else:
                s, ms = int(sec), 0
        else:
            raise ValueError("bad time " + text)
    else:
        if '.' in text:
            s, frac = text.split('.')
            h = m = 0
            s = int(s)
            ms = int(frac.ljust(3, '0')[:3])
        else:
            h = m = 0
            s = int(text)
            ms = 0
    return (((h * 60 + m) * 60 + s) * 1000 + ms) * 90


@dataclass
class Track:
    lang: str
    index: int
    timestamps: list[tuple[int, int]] = field(default_factory=list)  # (ticks, filepos)


@dataclass
class IdxData:
    path: Path
    size: tuple[int, int] | None
    palette: list[tuple[int, int, int]]
    tracks: list[Track]
    offset_ms: int = 0
    alpha_percent: int = 100
    forced: bool = False
    langidx: int = 0
    org: tuple[int, int] = (0, 0)
    scale: tuple[int, int] = (100, 100)
    custom_colors: bool = False
    warnings: list[str] = field(default_factory=list)


_TIMESTAMP_RE = re.compile(
    r"timestamp:\s*(\d+):(\d+):(\d+):(\d+)\s*,\s*filepos:\s*([0-9a-fA-F]+)")


def parse_idx(path: Path) -> IdxData:
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    size = None
    palette: list[tuple[int, int, int]] = []
    tracks: list[Track] = []
    cur: Track | None = None
    offset_ms = 0
    alpha_percent = 100
    forced = False
    langidx = 0
    org = (0, 0)
    scale = (100, 100)
    custom = False
    warnings: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        low = line.lower()
        if low.startswith('size:'):
            m = re.search(r'(\d+)\s*x\s*(\d+)', line)
            if m:
                size = (int(m.group(1)), int(m.group(2)))
        elif low.startswith('palette:'):
            body = line.split(':', 1)[1]
            hexes = re.findall(r'[0-9a-fA-F]{6}', body)
            palette = [tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) for h in hexes]
        elif low.startswith('id:'):
            m = re.search(r'id:\s*([A-Za-z-]+)\s*,\s*index:\s*(\d+)', line)
            if m:
                cur = Track(m.group(1), int(m.group(2)))
                tracks.append(cur)
        elif low.startswith('timestamp:'):
            m = _TIMESTAMP_RE.match(line)
            if not m:
                continue
            h, mi, s, ms = (int(m.group(i)) for i in range(1, 5))
            ticks = (((h * 60 + mi) * 60 + s) * 1000 + ms) * 90
            if cur is None:
                cur = Track('und', 0)
                tracks.append(cur)
            cur.timestamps.append((ticks, int(m.group(5), 16)))
        elif low.startswith('time offset:'):
            try:
                offset_ms = int(float(line.split(':', 1)[1]))
            except ValueError:
                warnings.append('无法解析 time offset')
        elif low.startswith('alpha:'):
            m = re.search(r'(\d+)', line.split(':', 1)[1])
            if m:
                alpha_percent = max(0, min(100, int(m.group(1))))
        elif low.startswith('forced subs:'):
            forced = 'OFF' not in line.upper()
        elif low.startswith('langidx:'):
            try:
                langidx = int(line.split(':', 1)[1])
            except ValueError:
                pass
        elif low.startswith('org:'):
            m = re.findall(r'-?\d+', line.split(':', 1)[1])
            if len(m) >= 2:
                org = (int(m[0]), int(m[1]))
        elif low.startswith('scale:'):
            m = re.findall(r'(\d+)%', line.split(':', 1)[1])
            if len(m) >= 2:
                scale = (int(m[0]), int(m[1]))
        elif low.startswith('custom colors:'):
            custom = 'ON' in line.upper()

    if not palette:
        palette = list(DEFAULT_PALETTE)
    if not tracks:
        tracks = [Track('und', 0)]
    if custom:
        warnings.append('idx 使用了 custom colors，本工具按标准 palette 处理')
    if scale != (100, 100):
        warnings.append(f'idx scale 为 {scale[0]}%x{scale[1]}%，未做几何缩放（可能位置不准）')
    if org != (0, 0):
        warnings.append(f'idx org 为 {org}，未做位移（可能位置不准）')
    return IdxData(path, size, palette, tracks, offset_ms, alpha_percent, forced,
                   langidx, org, scale, custom, warnings)


@dataclass
class SpuData:
    size: int
    ctrl_offset: int
    area: tuple[int, int, int, int] | None   # x1, y1, x2, y2 (inclusive)
    offsets: tuple[int, int] | None          # RLE offsets for field 0 / 1
    colormap: list[int] | None               # palette index for pixel value 0..3
    alpha: list[int] | None                  # 4-bit alpha for pixel value 0..3
    stop_ticks: int | None                   # relative stop time in 90 kHz ticks
    sequences: int
    animated: bool
    warnings: list[str]

    @property
    def width(self) -> int:
        return self.area[2] - self.area[0] + 1 if self.area else 0

    @property
    def height(self) -> int:
        return self.area[3] - self.area[1] + 1 if self.area else 0


def _read_nibble(raw: bytes, bitpos: int) -> tuple[int, int]:
    byte = raw[bitpos >> 3]
    return (byte >> (4 if (bitpos & 7) == 0 else 0)) & 0xF, bitpos + 4


def decode_bitmap(raw: bytes, spu: SpuData) -> list[bytearray]:
    """Decode the two interlaced RLE fields into raster-ordered palette indices."""
    x1, y1, x2, y2 = spu.area  # type: ignore[misc]
    w = x2 - x1 + 1
    h = y2 - y1 + 1
    rows: list[bytearray | None] = [bytearray(w) for _ in range(h)]
    off_even, off_odd = spu.offsets  # type: ignore[misc]

    for first_row, start in ((0, off_even), (1, off_odd)):
        nrows = (h + 1) // 2 if first_row == 0 else h // 2
        bitpos = start * 8
        for k in range(nrows):
            y = first_row + 2 * k
            x = 0
            row = rows[y]
            assert row is not None
            while x < w:
                if bitpos + 4 > len(raw) * 8:
                    raise VobSubError(f'SPU 位图数据提前结束（第 {y} 行）')
                v = 0
                t = 1
                while v < t and t <= 0x40:
                    n, bitpos = _read_nibble(raw, bitpos)
                    v = (v << 4) | n
                    t <<= 2
                color = v & 3
                ln = (w - x) if v < 4 else (v >> 2)
                if ln <= 0:
                    raise VobSubError(f'SPU 位图游程非法（第 {y} 行，长度 {ln}）')
                ln = min(ln, w - x)
                row[x:x + ln] = bytes([color]) * ln
                x += ln
            bitpos = (bitpos + 7) & ~7
    return [r for r in rows if r is not None]


def parse_spu(raw: bytes) -> SpuData:
    if len(raw) < 6:
        raise VobSubError('SPU 数据过短')
    size = be16(raw, 0)
    ctrl = be16(raw, 2)
    if ctrl < 4 or ctrl >= len(raw):
        raise VobSubError(f'SPU 控制序列偏移非法：{ctrl}/{len(raw)}')
    warnings: list[str] = []
    area = None
    offsets = None
    colormap = None
    alpha = None
    stop_dates: list[int] = []
    seen_offsets: set[tuple[int, int]] = set()
    seen_cmaps: set[tuple[int, ...]] = set()
    seqs = 0
    pos = ctrl
    guard = 0
    while 0 < pos <= len(raw) - 4 and guard < 32:
        guard += 1
        seqs += 1
        date = be16(raw, pos)
        nxt = be16(raw, pos + 2)
        p = pos + 4
        while p < len(raw):
            cmd = raw[p]
            p += 1
            if cmd == 0x00:
                pass  # menu subpicture flag
            elif cmd == 0x01:
                pass  # start display at this sequence's date
            elif cmd == 0x02:
                stop_dates.append(date)
            elif cmd == 0x03:
                if p + 2 > len(raw):
                    raise VobSubError('SPU palette 命令截断')
                b0, b1 = raw[p], raw[p + 1]
                p += 2
                cm = [b1 & 0xF, b1 >> 4, b0 & 0xF, b0 >> 4]
                seen_cmaps.add(tuple(cm))
                colormap = cm
            elif cmd == 0x04:
                if p + 2 > len(raw):
                    raise VobSubError('SPU alpha 命令截断')
                b0, b1 = raw[p], raw[p + 1]
                p += 2
                alpha = [b1 & 0xF, b1 >> 4, b0 & 0xF, b0 >> 4]
            elif cmd == 0x05:
                if p + 6 > len(raw):
                    raise VobSubError('SPU 显示区域命令截断')
                x1 = (raw[p] << 4) | (raw[p + 1] >> 4)
                x2 = ((raw[p + 1] & 0xF) << 8) | raw[p + 2]
                y1 = (raw[p + 3] << 4) | (raw[p + 4] >> 4)
                y2 = ((raw[p + 4] & 0xF) << 8) | raw[p + 5]
                p += 6
                area = (x1, y1, x2, y2)
            elif cmd == 0x06:
                if p + 4 > len(raw):
                    raise VobSubError('SPU 位图偏移命令截断')
                o1, o2 = be16(raw, p), be16(raw, p + 2)
                p += 4
                offsets = (o1, o2)
                seen_offsets.add(offsets)
            elif cmd == 0xFF:
                break
            else:
                warnings.append(f'未知 SPU 命令 0x{cmd:02x}，已停止解析该段')
                p = len(raw)
                break
        if nxt == 0 or nxt <= pos:
            break
        pos = nxt
    if area is None:
        raise VobSubError('SPU 缺少显示区域信息')
    if offsets is None:
        raise VobSubError('SPU 缺少位图偏移信息')
    stop_ticks = max(stop_dates) * 1024 if stop_dates else None
    animated = len(seen_offsets) > 1 or len(seen_cmaps) > 1
    return SpuData(size, ctrl, area, offsets, colormap, alpha, stop_ticks, seqs,
                   animated, warnings)


@dataclass
class Entry:
    index: int
    start: int          # ticks
    filepos: int
    substream: int      # expected 0x20-based substream id


@dataclass
class Subtitle:
    index: int
    start: int
    end: int
    x: int
    y: int
    width: int
    height: int
    rows: list[bytearray]
    colors: list[tuple[int, int, int, int]]  # RGBA for pixel values 0..3
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> int:
        return self.end - self.start


class VobSub:
    """Reads a VobSub pair, a lone .sub, and decodes SPUs."""

    def __init__(self, path: str | Path):
        path = Path(path)
        self.warnings: list[str] = []
        self._f = None
        if path.suffix.lower() == '.idx':
            idx = parse_idx(path)
            sub = self._find_sub(path)
            self.idx = idx
            self.idx_path = path
            self.sub_path = sub
            self.palette = idx.palette
            self.size = idx.size
            self.offset_ticks = idx.offset_ms * 90
            self.alpha_percent = idx.alpha_percent
            self.tracks = idx.tracks
            self.warnings.extend(idx.warnings)
            self.lone = False
            self.entries: list[list[Entry]] = []
            for t in self.tracks:
                self.entries.append([
                    Entry(i, ts + self.offset_ticks, fp, 0x20 + (t.index & 0x1F))
                    for i, (ts, fp) in enumerate(t.timestamps)])
        elif path.suffix.lower() == '.sub':
            idx_path = self._find_idx(path)
            if idx_path is not None:
                self.__init__(idx_path)
                return
            self.idx = None
            self.idx_path = None
            self.sub_path = path
            self.palette = list(DEFAULT_PALETTE)
            self.size = None
            self.offset_ticks = 0
            self.alpha_percent = 100
            self.tracks = [Track('und', 0)]
            self.lone = True
            self.warnings.append('单 .sub 文件：缺少 .idx，无法得知调色板与画布尺寸，'
                                 '已使用默认调色板，请在导出时确认画布尺寸。')
            self.entries = [self._scan_lone_sub()]
        else:
            raise VobSubError('请选择 .idx 或 .sub 文件')

    @staticmethod
    def _find_sub(idx_path: Path) -> Path:
        for ext in ('.sub', '.SUB', '.Sub'):
            p = idx_path.with_suffix(ext)
            if p.exists():
                return p
        # fallback: same stem, any case extension
        for p in idx_path.parent.iterdir():
            if p.stem.lower() == idx_path.stem.lower() and p.suffix.lower() == '.sub':
                return p
        raise VobSubError(f'找不到同名的 .sub 文件：{idx_path.name}')

    @staticmethod
    def _find_idx(sub_path: Path) -> Path | None:
        for p in sub_path.parent.iterdir():
            if p.stem.lower() == sub_path.stem.lower() and p.suffix.lower() == '.idx':
                return p
        return None

    # ---------- low level reading ----------

    def _blocks(self, filepos: int):
        """Yield (payload, pts) for the PES payloads of the 2048 byte packs."""
        while True:
            self._f.seek(filepos)
            block = self._f.read(BLOCK)
            if len(block) < 14:
                return
            if block[0:4] != b'\x00\x00\x01\xba':
                raise VobSubError(f'{filepos:#x} 处没有 MPEG pack 头（文件不是 VobSub 格式？）')
            p = 14
            if block[p:p + 4] != b'\x00\x00\x01\xbd':
                raise VobSubError(f'{filepos + p:#x} 处没有私有流 PES 头')
            pes_len = be16(block, p + 4)
            q = p + 6
            flags1, flags2, hlen = block[q], block[q + 1], block[q + 2]
            q += 3
            pts = None
            if flags2 & 0x80 and q + 5 <= len(block):
                pts = (((block[q] >> 1) & 0x07) << 30) | (block[q + 1] << 22) | \
                      ((block[q + 2] >> 1) << 15) | (block[q + 3] << 7) | (block[q + 4] >> 1)
            q += hlen
            end = min(BLOCK, p + 6 + pes_len)
            payload = block[q:end]
            if payload and 0x20 <= payload[0] <= 0x3F:
                payload = payload[1:]
            yield payload, pts
            filepos += BLOCK

    def read_spu(self, filepos: int, expected_substream: int | None = None) -> tuple[bytes, int | None]:
        own = False
        if self._f is None:
            self._f = open(self.sub_path, 'rb')
            own = True
        try:
            it = self._blocks(filepos)
            first, pts = next(it)
            substream = None
            if expected_substream is not None:
                # substream id byte was already stripped: recover it by re-reading
                pass
            if len(first) < 4:
                raise VobSubError('SPU 数据为空')
            size = be16(first, 0)
            if size < 4 or size > 1_000_000:
                raise VobSubError(f'SPU 尺寸异常：{size}')
            out = bytearray(first)
            while len(out) < size:
                payload, _ = next(it)
                if not payload:
                    raise VobSubError('SPU 数据在文件尾部截断')
                out += payload
            return bytes(out[:size]), pts
        finally:
            if own and self._f is not None:
                self._f.close()
                self._f = None

    def _scan_lone_sub(self) -> list[Entry]:
        """Walk a lone .sub, deriving start times from PES PTS headers."""
        entries: list[Entry] = []
        fsize = self.sub_path.stat().st_size
        pos = 0
        idx = 0
        idxfile = open(self.sub_path, 'rb')
        try:
            while pos + BLOCK <= fsize:
                idxfile.seek(pos)
                block = idxfile.read(BLOCK)
                if block[0:4] != b'\x00\x00\x01\xba':
                    # tolerate trailing padding
                    break
                p = 14
                if block[p:p + 4] != b'\x00\x00\x01\xbd':
                    break
                pes_len = be16(block, p + 4)
                q = p + 6
                flags1, flags2, hlen = block[q], block[q + 1], block[q + 2]
                q += 3
                pts = None
                if flags2 & 0x80:
                    pts = (((block[q] >> 1) & 0x07) << 30) | (block[q + 1] << 22) | \
                          ((block[q + 2] >> 1) << 15) | (block[q + 3] << 7) | (block[q + 4] >> 1)
                q += hlen
                payload = block[q:min(BLOCK, p + 6 + pes_len)]
                if not payload or not (0x20 <= payload[0] <= 0x3F):
                    pos += BLOCK
                    continue
                payload = payload[1:]
                substream = 0x20
                size = be16(payload, 0)
                have = len(payload)
                nblocks = 1
                while have < size:
                    idxfile.seek(pos + nblocks * BLOCK)
                    more = idxfile.read(BLOCK)
                    if len(more) < 14 or more[0:4] != b'\x00\x00\x01\xba':
                        break
                    pp = 14
                    if more[pp:pp + 4] != b'\x00\x00\x01\xbd':
                        break
                    plen = be16(more, pp + 4)
                    qq = pp + 6
                    fl1, fl2, hl = more[qq], more[qq + 1], more[qq + 2]
                    qq += 3 + hl
                    pl = more[qq:min(BLOCK, pp + 6 + plen)]
                    if pl and 0x20 <= pl[0] <= 0x3F:
                        pl = pl[1:]
                    payload += pl
                    have += len(pl)
                    nblocks += 1
                if pts is None:
                    pos += nblocks * BLOCK
                    continue
                entries.append(Entry(idx, pts, pos, substream))
                idx += 1
                pos += nblocks * BLOCK
        finally:
            idxfile.close()
        if not entries:
            raise VobSubError('未能在 .sub 中找到 VobSub 字幕数据')
        return entries

    # ---------- decoding ----------

    def decode(self, entry: Entry, end_fallback: int | None = None) -> Subtitle:
        raw, pts = self.read_spu(entry.filepos)
        spu = parse_spu(raw)
        if spu.width <= 0 or spu.height <= 0:
            raise VobSubError('字幕画面尺寸为 0')
        rows = decode_bitmap(raw, spu)
        start = entry.start
        if start < 0:
            start = 0
        end = start + spu.stop_ticks if spu.stop_ticks else None
        warnings = list(spu.warnings)
        if end is None or end <= start:
            if end_fallback is not None and end_fallback > start:
                end = end_fallback
                warnings.append('该段缺少结束时间，已使用下一段开始时间')
            else:
                end = start + 3 * TICKS
                warnings.append('该段缺少结束时间，已按 3 秒处理')
        if spu.animated:
            warnings.append('该段在原字幕内有多帧变化，转换时只保留第一帧')
        colors = self._colors_for(spu)
        x1, y1, _, _ = spu.area  # type: ignore[misc]
        return Subtitle(entry.index, start, end, x1, y1, spu.width, spu.height,
                        rows, colors, warnings)

    def _colors_for(self, spu: SpuData) -> list[tuple[int, int, int, int]]:
        cmap = spu.colormap or [3, 2, 1, 0]
        alpha = spu.alpha or [15, 15, 15, 0]
        pal = self.palette or DEFAULT_PALETTE
        out = []
        for i in range(4):
            idx = cmap[i] % len(pal)
            r, g, b = pal[idx]
            a = alpha[i] * 17
            if self.alpha_percent != 100:
                a = a * self.alpha_percent // 100
            out.append((r, g, b, a))
        return out

    def track_names(self) -> list[str]:
        return [f'{t.lang} (index {t.index})' for t in self.tracks]

    def iter_entries(self, track: int = 0) -> list[Entry]:
        return self.entries[track]

    def decode_all(self, track: int = 0, progress=None, cancel=None):
        """Yield (Subtitle, warnings) sequentially. Starts a persistent handle."""
        entries = self.entries[track]
        self._f = open(self.sub_path, 'rb')
        try:
            for i, e in enumerate(entries):
                nxt = entries[i + 1].start if i + 1 < len(entries) else None
                if cancel is not None and cancel():
                    return
                sub = self.decode(e, nxt)
                if progress is not None:
                    progress(i + 1, len(entries))
                yield sub
        finally:
            if self._f is not None:
                self._f.close()
                self._f = None

    def close(self):
        if self._f is not None:
            self._f.close()
            self._f = None
