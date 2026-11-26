"""Render text subtitles (ASS/SSA, SRT, WebVTT, MicroDVD) to PGS bitmaps.

Text subtitles are re-rendered with Pillow, so the result is not lossless:
font, layout and styling are approximated.  ASS support covers the common
styles/tags; drawings, animation and transforms are ignored with warnings.
"""
from __future__ import annotations

import html as _html
import math
import os
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

    PIL_OK = True
    PIL_ERROR = ''
except Exception as _exc:  # noqa: BLE001
    Image = None
    ImageChops = None
    ImageDraw = None
    ImageFilter = None
    ImageFont = None
    PIL_OK = False
    PIL_ERROR = str(_exc)

from . import vobsub

RGBA = tuple[int, int, int, int]
WHITE: RGBA = (255, 255, 255, 255)
BLACK: RGBA = (0, 0, 0, 255)
CLEAR: RGBA = (0, 0, 0, 0)


class TextSubError(Exception):
    pass


# --------------------------------------------------------------------------- utils
def _read_text(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        try:
            return data.decode('utf-16'), 'utf-16'
        except UnicodeDecodeError:
            pass
    for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'big5', 'shift_jis', 'cp1252'):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1', 'replace'), 'latin-1'


def _ticks_from_seconds(seconds: float) -> int:
    return max(0, int(round(seconds * 90000)))


def parse_ass_time(s: str) -> int:
    s = s.strip()
    parts = s.split(':')
    if len(parts) == 3:
        h, m, rest = parts
    elif len(parts) == 2:
        h, m, rest = '0', parts[0], parts[1]
    else:
        raise ValueError(f'bad ASS time: {s}')
    return _ticks_from_seconds(int(h) * 3600 + int(m) * 60 + float(rest))


def parse_srt_time(s: str) -> int:
    s = s.strip().replace(',', '.')
    parts = s.split(':')
    if len(parts) == 3:
        h, m, rest = parts
    elif len(parts) == 2:
        h, m, rest = '0', parts[0], parts[1]
    else:
        raise ValueError(f'bad SRT time: {s}')
    return _ticks_from_seconds(int(h) * 3600 + int(m) * 60 + float(rest))


def parse_color(value: str, default: RGBA = WHITE) -> RGBA:
    s = (value or '').strip().strip('"\'')
    if not s:
        return default
    if s.startswith('#'):
        h = s[1:]
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        if len(h) >= 6:
            try:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
            except ValueError:
                return default
    if s.lower().startswith('&h'):
        h = s[2:].rstrip('&')
        try:
            if len(h) >= 8:
                aa = int(h[0:2], 16)
                bgr = int(h[2:8], 16)
                a = 255 - aa
            elif len(h) >= 6:
                bgr = int(h[0:6], 16)
                a = 255
            else:
                return default
            r = bgr & 0xFF
            g = (bgr >> 8) & 0xFF
            b = (bgr >> 16) & 0xFF
            return (r, g, b, max(0, min(255, a)))
        except ValueError:
            return default
    names = {
        'white': WHITE, 'black': BLACK, 'red': (255, 0, 0, 255),
        'green': (0, 128, 0, 255), 'blue': (0, 0, 255, 255),
        'yellow': (255, 255, 0, 255), 'cyan': (0, 255, 255, 255),
        'magenta': (255, 0, 255, 255), 'gray': (128, 128, 128, 255),
        'grey': (128, 128, 128, 255),
    }
    return names.get(s.lower(), default)


def _alpha_of(value: str) -> int | None:
    s = (value or '').strip()
    if s.startswith('&H') or s.startswith('&h'):
        s = s[2:].rstrip('&')
    elif s.startswith('H') or s.startswith('h'):
        s = s[1:].rstrip('&')
    try:
        if len(s) > 2:
            s = s[:2]
        return max(0, min(255, 255 - int(s, 16)))
    except ValueError:
        return None


def _parse_xy(arg: str) -> tuple[float, float] | None:
    m = re.search(r'\(?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', arg or '')
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


# --------------------------------------------------------------------------- styles
@dataclass
class Style:
    name: str = 'Default'
    fontname: str = 'Microsoft YaHei'
    fontsize: float = 54.0
    primary: RGBA = WHITE
    secondary: RGBA = (255, 255, 0, 255)
    outline_color: RGBA = BLACK
    back_color: RGBA = (0, 0, 0, 160)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False
    scale_x: float = 100.0
    scale_y: float = 100.0
    spacing: float = 0.0
    border_style: int = 1
    outline: float = 2.0
    shadow: float = 1.5
    alignment: int = 2
    margin_l: int = 20
    margin_r: int = 20
    margin_v: int = 20

    def copy(self) -> 'Style':
        return replace(self)


@dataclass
class Event:
    index: int
    start: int
    end: int
    style: str
    text: str
    layer: int = 0
    margin_l: int = 0
    margin_r: int = 0
    margin_v: int = 0
    effect: str = ''


@dataclass
class Run:
    text: str
    fontname: str
    fontsize: float
    bold: bool
    italic: bool
    underline: bool
    strikeout: bool
    primary: RGBA
    outline_color: RGBA
    shadow_color: RGBA
    outline: float
    shadow: float
    spacing: float = 0.0


@dataclass
class ParsedText:
    lines: list[list[Run]]
    align: int | None = None
    pos: tuple[float, float] | None = None
    top: bool = False
    warnings: list[str] = field(default_factory=list)
    clip: tuple[float, float, float, float] | None = None
    clip_inverse: bool = False
    blur: float = 0.0


def _style_to_run(style: Style) -> Run:
    return Run('', style.fontname, style.fontsize, style.bold, style.italic,
               style.underline, style.strikeout, style.primary,
               style.outline_color, style.back_color, style.outline,
               style.shadow, style.spacing)


# --------------------------------------------------------------------------- fonts
_FONT_INDEX: list[tuple[str, str, bool, bool, Path]] | None = None
_FONT_CACHE: dict[tuple[str, int, bool, bool], tuple[object, bool, bool]] = {}
_FONT_WARN: set[str] = set()

_FONT_ALIASES = {
    '微软雅黑': 'microsoft yahei', '雅黑': 'microsoft yahei',
    '黑体': 'simhei', '宋体': 'simsun', '新宋体': 'nsimsun',
    '楷体': 'kaiti', '仿宋': 'fangsong', '思源黑体': 'source han sans',
    '思源宋体': 'source han serif', '等线': 'dengxian',
}


def _norm_font(s: str) -> str:
    s = s.replace('@', '').replace('（', '').replace('）', '').lower()
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', s)


def _font_index() -> list[tuple[str, str, bool, bool, Path]]:
    global _FONT_INDEX
    if _FONT_INDEX is not None:
        return _FONT_INDEX
    entries: list[tuple[str, str, bool, bool, Path]] = []
    win = Path(os.environ.get('WINDIR', r'C:\Windows'))
    dirs = [win / 'Fonts', Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'Windows' / 'Fonts']
    seen: set[Path] = set()
    try:
        import winreg
        for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts'),
                          (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')):
            try:
                key = winreg.OpenKey(root, sub)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    name, value, _typ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                p = Path(value)
                if not p.is_absolute():
                    p = win / 'Fonts' / value
                if not p.exists() or p.suffix.lower() not in ('.ttf', '.ttc', '.otf'):
                    continue
                if p in seen:
                    continue
                seen.add(p)
                low = name.lower()
                bold = any(x in low for x in ('bold', 'black', 'heavy', 'semibold', 'demi')) or \
                    bool(re.search(r'(bd|b)\.(ttf|ttc)$', p.name.lower()))
                italic = 'italic' in low or 'oblique' in low or \
                    bool(re.search(r'(i|bi)\.(ttf|ttc)$', p.name.lower()))
                entries.append((name, p.stem, bold, italic, p))
    except Exception:
        pass
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.glob('*'):
            if p.suffix.lower() not in ('.ttf', '.ttc', '.otf') or p in seen:
                continue
            seen.add(p)
            low = p.stem.lower()
            entries.append((p.stem, p.stem,
                            bool(re.search(r'(bold|bd|black|semibold)', low)),
                            bool(re.search(r'(italic|oblique|i$|bi$)', low)), p))
    _FONT_INDEX = entries
    return entries


def _match_font(family: str, bold: bool, italic: bool) -> tuple[Path | None, bool, bool]:
    fam = _norm_font(family or '')
    if not fam:
        fam = 'microsoftyahei'
    fam = _FONT_ALIASES.get(family.strip(), fam)
    entries = _font_index()
    matches = [e for e in entries
               if fam and (fam in _norm_font(e[0]) or fam in _norm_font(e[1]))]
    if not matches:
        return None, bold, italic
    best = None
    best_score = -10**9
    for _name, _stem, ebold, eitalic, path in matches:
        score = 0
        if ebold == bold:
            score += 8
        elif ebold:
            score -= 4
        if eitalic == italic:
            score += 6
        elif eitalic:
            score -= 3
        if best is None or score > best_score:
            best = (path, ebold, eitalic)
            best_score = score
    if best is None:
        return None, bold, italic
    path, ebold, eitalic = best
    return path, (bold and not ebold), bool(italic and not eitalic)


def _fallback_font_path() -> Path | None:
    win = Path(os.environ.get('WINDIR', r'C:\Windows'))
    for name in ('msyh.ttc', 'msyh.ttf', 'simhei.ttf', 'simsun.ttc', 'arial.ttf',
                 'segoeui.ttf', 'DejaVuSans.ttf'):
        p = win / 'Fonts' / name
        if p.exists():
            return p
    return None


def get_font(family: str, size: int, bold: bool, italic: bool,
             warnings: list[str]) -> tuple[object, bool, bool]:
    size = max(1, min(2000, int(size)))
    key = (family or '', size, bool(bold), bool(italic))
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    path, fake_bold, fake_italic = _match_font(family, bold, italic)
    if path is None:
        path = _fallback_font_path()
        if family and family not in _FONT_WARN:
            _FONT_WARN.add(family)
            warnings.append(f'字体 “{family}” 未找到，使用回退字体')
    font = None
    if path is not None:
        try:
            font = ImageFont.truetype(str(path), size)
        except Exception:
            font = None
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except Exception:
            font = ImageFont.load_default()
        fake_bold = bold
    _FONT_CACHE[key] = (font, fake_bold, fake_italic)
    return font, fake_bold, fake_italic


# --------------------------------------------------------------------------- ASS / SSA
def _split_fields(line: str, count: int) -> list[str]:
    return line.split(',', count - 1) if count > 0 else [line]


def _parse_style(fields: dict[str, str]) -> Style:
    def fnum(name: str, default: float) -> float:
        try:
            return float(fields.get(name, default) or default)
        except (TypeError, ValueError):
            return default

    def inum(name: str, default: int) -> int:
        try:
            return int(float(fields.get(name, default) or default))
        except (TypeError, ValueError):
            return default

    def flag(name: str) -> bool:
        try:
            return float(fields.get(name, 0) or 0) != 0
        except (TypeError, ValueError):
            return False

    align = inum('Alignment', 2)
    if align <= 0:
        align = 2
    return Style(
        name=fields.get('Name', 'Default') or 'Default',
        fontname=fields.get('Fontname', 'Microsoft YaHei') or 'Microsoft YaHei',
        fontsize=max(1.0, fnum('Fontsize', 54)),
        primary=parse_color(fields.get('PrimaryColour', '')),
        secondary=parse_color(fields.get('SecondaryColour', ''), (255, 255, 0, 255)),
        outline_color=parse_color(fields.get('OutlineColour', ''), BLACK),
        back_color=parse_color(fields.get('BackColour', ''), (0, 0, 0, 160)),
        bold=flag('Bold'), italic=flag('Italic'),
        underline=flag('Underline'), strikeout=flag('StrikeOut'),
        scale_x=fnum('ScaleX', 100), scale_y=fnum('ScaleY', 100),
        spacing=fnum('Spacing', 0),
        border_style=max(1, min(3, inum('BorderStyle', 1))),
        outline=max(0, fnum('Outline', 2)),
        shadow=max(0, fnum('Shadow', 1.5)),
        alignment=max(1, min(9, align)),
        margin_l=max(0, inum('MarginL', 20)),
        margin_r=max(0, inum('MarginR', 20)),
        margin_v=max(0, inum('MarginV', 20)),
    )


def parse_ass(text: str, path: Path | None = None) -> tuple[dict[str, Style], list[Event],
                                                              tuple[int, int], list[str]]:
    styles: dict[str, Style] = {'Default': Style()}
    events: list[Event] = []
    play_x, play_y = 1920, 1080
    warnings: list[str] = []
    section = ''
    style_fmt: list[str] = []
    event_fmt: list[str] = []
    declared_res = False
    for raw in text.splitlines():
        line = raw.strip().lstrip('\ufeff')
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line.strip('[]').strip().lower()
            continue
        if section.startswith('script info'):
            if ':' in line:
                key, val = line.split(':', 1)
                key = key.strip().lower()
                if key == 'playresx':
                    try:
                        play_x = max(1, int(float(val.strip())))
                        declared_res = True
                    except ValueError:
                        pass
                elif key == 'playresy':
                    try:
                        play_y = max(1, int(float(val.strip())))
                        declared_res = True
                    except ValueError:
                        pass
            continue
        if section.startswith('v4+ styles') or section.startswith('v4 styles'):
            if line.lower().startswith('format:'):
                style_fmt = [x.strip() for x in line.split(':', 1)[1].split(',')]
            elif line.lower().startswith('style:') and style_fmt:
                vals = _split_fields(line.split(':', 1)[1].strip(), len(style_fmt))
                fields = dict(zip(style_fmt, vals))
                st = _parse_style(fields)
                styles[st.name] = st
            continue
        if section.startswith('events'):
            if line.lower().startswith('format:'):
                event_fmt = [x.strip() for x in line.split(':', 1)[1].split(',')]
            elif line.lower().startswith('dialogue:') and event_fmt:
                vals = _split_fields(line.split(':', 1)[1].strip(), len(event_fmt))
                fields = dict(zip(event_fmt, vals))
                try:
                    start = parse_ass_time(fields.get('Start', '0:00:00.00'))
                    end = parse_ass_time(fields.get('End', '0:00:00.00'))
                except ValueError:
                    continue
                if end <= start:
                    continue
                try:
                    layer = int(float(fields.get('Layer', 0) or 0))
                except ValueError:
                    layer = 0
                def mi(name: str) -> int:
                    try:
                        return max(0, int(float(fields.get(name, 0) or 0)))
                    except ValueError:
                        return 0
                events.append(Event(
                    index=len(events), start=start, end=end,
                    style=fields.get('Style', 'Default') or 'Default',
                    text=fields.get('Text', '') or '',
                    layer=layer, margin_l=mi('MarginL'), margin_r=mi('MarginR'),
                    margin_v=mi('MarginV'), effect=fields.get('Effect', '') or '',
                ))
            continue
    if not declared_res:
        warnings.append('ASS 未声明 PlayRes，按 1920×1080 排版')
    if not events:
        raise TextSubError('ASS/SSA 中没有可用的 Dialogue 事件')
    return styles, events, (play_x, play_y), warnings


# --------------------------------------------------------------------------- SRT / VTT
def _html_to_ass(text: str, base: Style) -> str:
    s = _html.unescape(text).replace('\xa0', ' ')
    s = re.sub(r'<br\s*/?>', lambda m: '\\N', s, flags=re.I)
    simple = {
        'b': ('{\\b1}', '{\\b0}'), 'strong': ('{\\b1}', '{\\b0}'),
        'i': ('{\\i1}', '{\\i0}'), 'em': ('{\\i1}', '{\\i0}'),
        'u': ('{\\u1}', '{\\u0}'), 's': ('{\\s1}', '{\\s0}'),
    }

    def simple_tag(m: re.Match) -> str:
        closing = bool(m.group(1))
        name = (m.group(2) or '').lower()
        pair = simple.get(name)
        if not pair:
            return ''
        return pair[1] if closing else pair[0]

    s = re.sub(r'<\s*(/?)\s*(b|strong|i|em|u|s)\s*>', simple_tag, s, flags=re.I)

    def font_tag(m: re.Match) -> str:
        attrs = m.group(1)
        out = ''
        cm = re.search(r'color\s*=\s*["\']?(#[0-9a-fA-F]{6}|[a-zA-Z]+)', attrs)
        if cm:
            col = parse_color(cm.group(1), base.primary)
            out += f'{{\\c&H{col[2]:02X}{col[1]:02X}{col[0]:02X}&}}'
        fm = re.search(r'face\s*=\s*["\']([^"\']+)', attrs)
        if fm:
            out += '{\\fn' + fm.group(1) + '}'
        return out

    s = re.sub(r'<font\b([^>]*)>', font_tag, s, flags=re.I)
    base_col = f'&H{base.primary[2]:02X}{base.primary[1]:02X}{base.primary[0]:02X}&'
    s = re.sub(r'</font\s*>',
               lambda m: '{\\c' + base_col + '\\fn' + base.fontname + '}',
               s, flags=re.I)
    # strip remaining HTML-ish tags, keeping their inner text
    s = re.sub(r'</?(?:c|v|ruby|rt|span|div|p|br)\b[^>]*>', '', s, flags=re.I)
    return s


def _clean_event_text(text: str, base: Style) -> str:
    return _html_to_ass(text, base).replace('\r\n', '\n').replace('\r', '\n')


def parse_srt(text: str) -> tuple[dict[str, Style], list[Event], tuple[int, int], list[str]]:
    base = Style(name='Default', fontname='Microsoft YaHei', fontsize=54,
                 primary=WHITE, outline_color=BLACK, back_color=(0, 0, 0, 160),
                 outline=2.2, shadow=1.5, alignment=2, margin_l=60, margin_r=60, margin_v=45)
    events: list[Event] = []
    warnings: list[str] = []
    blocks = re.split(r'\n\s*\n', text.replace('\r\n', '\n').replace('\r', '\n').strip())
    for block in blocks:
        lines = [ln for ln in block.split('\n') if ln.strip() != '']
        if not lines:
            continue
        t_idx = next((i for i, ln in enumerate(lines) if '-->' in ln), -1)
        if t_idx < 0:
            continue
        tline = lines[t_idx]
        m = re.match(r'\s*(\S+)\s*-->\s*(\S+)', tline)
        if not m:
            continue
        try:
            start = parse_srt_time(m.group(1))
            end = parse_srt_time(m.group(2))
        except ValueError:
            continue
        body = '\n'.join(lines[t_idx + 1:])
        if not body.strip() or end <= start:
            continue
        body = _clean_event_text(body, base)
        events.append(Event(index=len(events), start=start, end=end,
                            style='Default', text=body))
    if not events:
        raise TextSubError('SRT 中没有可用的字幕事件')
    return {'Default': base}, events, (1920, 1080), warnings


def parse_vtt(text: str) -> tuple[dict[str, Style], list[Event], tuple[int, int], list[str]]:
    base = Style(name='Default', fontname='Microsoft YaHei', fontsize=54,
                 primary=WHITE, outline_color=BLACK, back_color=(0, 0, 0, 160),
                 outline=2.2, shadow=1.5, alignment=2, margin_l=60, margin_r=60, margin_v=45)
    events: list[Event] = []
    warnings: list[str] = []
    body = text.replace('\r\n', '\n').replace('\r', '\n')
    if body.startswith('\ufeff'):
        body = body[1:]
    # remove header up to first blank line
    parts = body.split('\n\n')
    if parts and parts[0].lstrip().startswith('WEBVTT'):
        body = '\n\n'.join(parts[1:])
    for block in re.split(r'\n\s*\n', body.strip()):
        lines = [ln for ln in block.split('\n') if ln.strip() != '']
        if not lines or lines[0].strip().startswith(('NOTE', 'STYLE', 'REGION')):
            continue
        t_idx = next((i for i, ln in enumerate(lines) if '-->' in ln), -1)
        if t_idx < 0:
            continue
        tline = lines[t_idx]
        m = re.match(r'\s*(\S+)\s*-->\s*(\S+)(.*)$', tline)
        if not m:
            continue
        try:
            start = parse_srt_time(m.group(1))
            end = parse_srt_time(m.group(2))
        except ValueError:
            continue
        cue_text = '\n'.join(lines[t_idx + 1:])
        if not cue_text.strip() or end <= start:
            continue
        settings = m.group(3) or ''
        h_align = 2
        v_align = 1
        am = re.search(r'align\s*:\s*(\w+)', settings)
        if am:
            a = am.group(1).lower()
            if a in ('left', 'start'):
                h_align = 1
            elif a in ('right', 'end'):
                h_align = 3
        lm = re.search(r'line\s*:\s*(-?\d+(?:\.\d+)?)(%?)', settings)
        if lm:
            try:
                val = float(lm.group(1))
                if lm.group(2) == '%':
                    if val <= 30:
                        v_align = 3
                elif 0 <= val <= 5:
                    v_align = 3
            except ValueError:
                pass
        an_map = {(1, 1): 1, (2, 1): 2, (3, 1): 3,
                  (1, 2): 4, (2, 2): 5, (3, 2): 6,
                  (1, 3): 7, (2, 3): 8, (3, 3): 9}
        prefix = f'{{\\an{an_map[(h_align, v_align)]}}}'
        cue_text = _clean_event_text(prefix + cue_text, base)
        events.append(Event(index=len(events), start=start, end=end,
                            style='Default', text=cue_text))
    if not events:
        raise TextSubError('WebVTT 中没有可用的字幕事件')
    return {'Default': base}, events, (1920, 1080), warnings


def parse_microdvd(text: str) -> tuple[dict[str, Style], list[Event], tuple[int, int], list[str]]:
    base = Style(name='Default', fontname='Microsoft YaHei', fontsize=54,
                 primary=WHITE, outline_color=BLACK, back_color=(0, 0, 0, 160),
                 outline=2.2, shadow=1.5, alignment=2, margin_l=60, margin_r=60, margin_v=45)
    events: list[Event] = []
    warnings: list[str] = []
    fps = 23.976
    fps_declared = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^\{(\d+)\}\{(\d+)\}(.*)$', line)
        if not m:
            continue
        a, b, body = int(m.group(1)), int(m.group(2)), m.group(3)
        if a <= 1 and b <= 1 and re.fullmatch(r'\d+(?:\.\d+)?', body.strip()):
            try:
                fps = float(body.strip())
                fps_declared = True
            except ValueError:
                pass
            continue
        if body.strip() == '':
            continue
        start = _ticks_from_seconds(a / fps)
        end = _ticks_from_seconds(b / fps)
        if end <= start:
            continue
        body = body.replace('|', r'\N')
        # MicroDVD style tags {Y:...}
        def ytag(mm: re.Match) -> str:
            val = mm.group(1).lower()
            if val == 'i':
                return r'{\i1}'
            if val == 'b':
                return r'{\b1}'
            if val == 'u':
                return r'{\u1}'
            return ''
        body = re.sub(r'\{Y:([a-zA-Z])\}', ytag, body)
        body = re.sub(r'\{[^}]*\}', '', body)
        events.append(Event(index=len(events), start=start, end=end,
                            style='Default', text=body))
    if not events:
        raise TextSubError('MicroDVD .sub 中没有可用的字幕事件')
    if not fps_declared:
        warnings.append('MicroDVD 未声明帧率，按 23.976 fps 计算')
    return {'Default': base}, events, (1920, 1080), warnings


# --------------------------------------------------------------------------- rich text
_STYLE_TAGS = {'fn', 'fs', 'b', 'i', 'u', 's', 'c', '1c', '2c', '3c', '4c',
               'alpha', '1a', '3a', '4a', 'bord', 'xbord', 'ybord', 'shad',
               'xshad', 'yshad', 'r', 'fsp'}


def _iter_tags(tagstr: str):
    i = 0
    n = len(tagstr)
    while i < n:
        if tagstr[i] != '\\':
            i += 1
            continue
        j = i + 1
        if j < n and tagstr[j].isdigit():
            # colour/alpha tags such as \1c, \3a
            while j < n and tagstr[j].isalnum():
                j += 1
        else:
            # normal tags: letters only, so \an8 / \b1 / \fs64 split into
            # name + argument correctly
            while j < n and tagstr[j].isalpha():
                j += 1
        name = tagstr[i + 1:j].lower()
        if not name:
            i += 1
            continue
        if j < n and tagstr[j] == '(':
            k = j + 1
            depth = 1
            while k < n and depth:
                if tagstr[k] == '(':
                    depth += 1
                elif tagstr[k] == ')':
                    depth -= 1
                k += 1
            arg = tagstr[j + 1:k - 1] if depth == 0 else tagstr[j + 1:]
            i = k
        else:
            k = tagstr.find('\\', j)
            if k < 0:
                arg = tagstr[j:]
                i = n
            else:
                arg = tagstr[j:k]
                i = k
        yield name, arg


def parse_event_text(text: str, style: Style, styles: dict[str, Style]) -> ParsedText | None:
    warnings: list[str] = []
    base_run = _style_to_run(style)
    state = replace(base_run)
    lines: list[list[Run]] = [[]]
    buf: list[str] = []
    layout_align: int | None = None
    layout_pos: tuple[float, float] | None = None
    layout_clip: tuple[float, float, float, float] | None = None
    layout_clip_inverse = False
    layout_blur = 0.0
    drawing = 0
    ignored: set[str] = set()

    def flush() -> None:
        if buf:
            run = replace(state, text=''.join(buf))
            lines[-1].append(run)
            buf.clear()

    def mark(name: str) -> None:
        if name not in ignored:
            ignored.add(name)

    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '{':
            j = text.find('}', i + 1)
            if j < 0:
                buf.append(text[i:])
                break
            tagstr = text[i + 1:j]
            tags = list(_iter_tags(tagstr))
            if any(name in _STYLE_TAGS for name, _ in tags):
                flush()
            for name, arg in tags:
                a = arg.strip()
                if name in ('pos', 'move'):
                    xy = _parse_xy(arg)
                    if xy is not None:
                        layout_pos = xy
                    if name == 'move':
                        mark('move')
                elif name == 'an':
                    try:
                        layout_align = max(1, min(9, int(float(a))))
                    except ValueError:
                        pass
                elif name == 'a':
                    try:
                        old = int(float(a))
                        layout_align = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9,
                                        9: 4, 10: 5, 11: 6}.get(old, layout_align)
                    except ValueError:
                        pass
                elif name == 'fn':
                    if a:
                        state.fontname = a
                elif name == 'fs':
                    try:
                        if a.startswith('+') or a.startswith('-'):
                            state.fontsize = max(1, state.fontsize + float(a))
                        elif a:
                            state.fontsize = max(1, float(a))
                    except ValueError:
                        pass
                elif name == 'b':
                    try:
                        state.bold = float(a) != 0 if a else True
                    except ValueError:
                        state.bold = True
                elif name == 'i':
                    state.italic = (a != '0')
                elif name == 'u':
                    state.underline = (a != '0')
                elif name == 's':
                    state.strikeout = (a != '0')
                elif name in ('c', '1c', '2c'):
                    if a:
                        state.primary = parse_color(a, state.primary)
                elif name == '3c':
                    state.outline_color = parse_color(a, state.outline_color)
                elif name == '4c':
                    state.shadow_color = parse_color(a, state.shadow_color)
                elif name == 'alpha':
                    al = _alpha_of(a)
                    if al is not None:
                        state.primary = (*state.primary[:3], al)
                        state.outline_color = (*state.outline_color[:3], al)
                        state.shadow_color = (*state.shadow_color[:3], al)
                elif name in ('1a', '3a', '4a'):
                    al = _alpha_of(a)
                    if al is not None:
                        if name == '1a':
                            state.primary = (*state.primary[:3], al)
                        elif name == '3a':
                            state.outline_color = (*state.outline_color[:3], al)
                        else:
                            state.shadow_color = (*state.shadow_color[:3], al)
                elif name in ('bord', 'xbord', 'ybord'):
                    try:
                        state.outline = max(0, float(a or 0))
                    except ValueError:
                        pass
                elif name == 'shad' or name == 'xshad' or name == 'yshad':
                    try:
                        state.shadow = max(0, float(a or 0))
                    except ValueError:
                        pass
                elif name == 'fsp':
                    try:
                        state.spacing = float(a or 0)
                    except ValueError:
                        pass
                elif name == 'clip' or name == 'iclip':
                    nums = re.findall(r'-?\d+(?:\.\d+)?', a)
                    if len(nums) == 4 and re.fullmatch(r'[\s\d.,\-]+', a):
                        layout_clip = tuple(float(x) for x in nums)  # type: ignore[assignment]
                        layout_clip_inverse = (name == 'iclip')
                    else:
                        mark('clip')
                elif name == 'blur' or name == 'be':
                    try:
                        layout_blur = max(layout_blur, float(a or 0))
                    except ValueError:
                        pass
                elif name == 'r':
                    target = styles.get(a) if a else style
                    state = _style_to_run(target or style)
                elif name == 'p':
                    try:
                        drawing = int(float(a or 0))
                    except ValueError:
                        drawing = 0
                    if drawing:
                        flush()
                        mark('drawing')
                elif name in ('fad', 'fade', 't', 'clip', 'iclip', 'fr', 'fscx',
                              'fscy', 'fax', 'fay', 'fe', 'k', 'kf',
                              'ko', 'K', 'org', 'q', 'xbord', 'ybord'):
                    mark(name)
            i = j + 1
            continue
        if drawing:
            i += 1
            continue
        if ch == '\\' and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in ('N', 'n'):
                flush()
                lines.append([])
                i += 2
                continue
            if nxt == 'h':
                buf.append(' ')
                i += 2
                continue
            if nxt == '\\':
                buf.append('\\')
                i += 2
                continue
        if ch == '\n':
            flush()
            lines.append([])
            i += 1
            continue
        buf.append(ch)
        i += 1
    flush()
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return None
    if ignored:
        names = ', '.join(sorted(ignored))
        warnings.append(f'已忽略部分 ASS 特效: {names}')
    return ParsedText(lines, layout_align, layout_pos, False, warnings,
                      layout_clip, layout_clip_inverse, layout_blur)


# --------------------------------------------------------------------------- layout / render
@dataclass
class LineLayout:
    runs: list[Run]
    fonts: list[object]
    fake_bold: list[bool]
    widths: list[float]
    width: float
    height: float
    ascent: float


class Renderer:
    def __init__(self, styles: dict[str, Style], play_res: tuple[int, int]):
        self.styles = styles
        self.play_x, self.play_y = play_res
        self.warnings: list[str] = []

    def _font_size(self, run: Run, sy: float) -> int:
        return max(1, int(round(run.fontsize * sy)))

    def _run_width(self, run: Run, font, sx: float) -> float:
        try:
            if run.spacing:
                return (sum(float(font.getlength(ch)) for ch in run.text) +
                        max(0, len(run.text) - 1) * run.spacing * sx)
            return float(font.getlength(run.text))
        except Exception:
            return len(run.text) * run.fontsize * 0.5

    def _wrap_lines(self, lines: list[list[Run]], max_width: float,
                    sx: float, sy: float) -> list[list[Run]]:
        """Wrap lines to the available width (CJK characters count singly)."""
        if max_width <= 0:
            return lines
        token_re = re.compile(r'\s+|[A-Za-z0-9]+(?:[._\'’-][A-Za-z0-9]+)*|.', re.S)
        out: list[list[Run]] = []

        def same_style(a: Run, b: Run) -> bool:
            return (a.fontname, a.fontsize, a.bold, a.italic, a.underline,
                    a.strikeout, a.primary, a.outline_color, a.shadow_color,
                    a.outline, a.shadow, a.spacing) == (
                    b.fontname, b.fontsize, b.bold, b.italic, b.underline,
                    b.strikeout, b.primary, b.outline_color, b.shadow_color,
                    b.outline, b.shadow, b.spacing)

        for line in lines:
            current: list[Run] = []
            current_w = 0.0

            def flush() -> None:
                nonlocal current, current_w
                if current:
                    out.append(current)
                current = []
                current_w = 0.0

            def append_token(run: Run, text: str, font) -> bool:
                nonlocal current_w
                if not text:
                    return True
                if text.isspace() and not current:
                    return True
                if current and same_style(current[-1], run):
                    last = current[-1]
                    old_w = self._run_width(last, font, sx)
                    merged = replace(last, text=last.text + text)
                    new_w = self._run_width(merged, font, sx)
                    if current and current_w + (new_w - old_w) > max_width:
                        return False
                    current[-1] = merged
                    current_w += new_w - old_w
                    return True
                tok = replace(run, text=text)
                w = self._run_width(tok, font, sx)
                if current and current_w + w > max_width:
                    return False
                current.append(tok)
                current_w += w
                return True

            for run in line:
                font, _fb, _fi = get_font(run.fontname, self._font_size(run, sy),
                                          run.bold, run.italic, self.warnings)
                for token in token_re.findall(run.text):
                    if token.isspace() and not current:
                        continue
                    if append_token(run, token, font):
                        continue
                    flush()
                    if token.isspace():
                        continue
                    if not append_token(run, token, font):
                        for ch in token:
                            if not append_token(run, ch, font):
                                flush()
                                append_token(run, ch, font)
            flush()
        return [ln for ln in out if ln]

    def _measure(self, lines: list[list[Run]], sx: float, sy: float) -> list[LineLayout]:
        out: list[LineLayout] = []
        for runs in lines:
            fonts = []
            fakes = []
            widths = []
            max_ascent = 0.0
            max_height = 0.0
            for run in runs:
                font, fb, _fi = get_font(run.fontname, self._font_size(run, sy),
                                         run.bold, run.italic, self.warnings)
                fonts.append(font)
                fakes.append(fb)
                widths.append(self._run_width(run, font, sx))
                try:
                    asc, desc = font.getmetrics()
                except Exception:
                    asc, desc = int(run.fontsize), int(run.fontsize * 0.25)
                max_ascent = max(max_ascent, asc)
                max_height = max(max_height, asc + desc)
            total = sum(widths)
            out.append(LineLayout(runs, fonts, fakes, widths, total,
                                  max_height, max_ascent))
        return out

    def _block_position(self, parsed: ParsedText, style: Style, event: Event,
                        block_w: float, block_h: float,
                        render_w: int, render_h: int,
                        sx: float, sy: float) -> tuple[float, float, int, int, float, float]:
        align = parsed.align or style.alignment or 2
        align = max(1, min(9, align))
        h = 1 if align in (1, 4, 7) else (2 if align in (2, 5, 8) else 3)
        v = 1 if align in (1, 2, 3) else (2 if align in (4, 5, 6) else 3)
        ml = (event.margin_l or style.margin_l) * sx
        mr = (event.margin_r or style.margin_r) * sx
        mv = (event.margin_v or style.margin_v) * sy
        if parsed.pos is not None:
            ax = parsed.pos[0] * sx
            ay = parsed.pos[1] * sy
            x = ax - (0 if h == 1 else (block_w / 2 if h == 2 else block_w))
            y = ay - (0 if v == 3 else (block_h / 2 if v == 2 else block_h))
        else:
            if h == 1:
                x = ml
            elif h == 2:
                x = (render_w - block_w) / 2.0
            else:
                x = render_w - mr - block_w
            if v == 1:
                y = render_h - mv - block_h
            elif v == 2:
                y = (render_h - block_h) / 2.0
            else:
                y = mv
        return x, y, h, v, ml, mv

    def render(self, parsed: ParsedText, style: Style, event: Event,
               render_w: int, render_h: int, wrap: bool = False,
               max_width: float | None = None) -> tuple[Image.Image, int, int] | None:
        sx = render_w / float(self.play_x or render_w)
        sy = render_h / float(self.play_y or render_h)
        lines = parsed.lines
        if wrap and max_width and max_width > 0:
            lines = self._wrap_lines(lines, max_width, sx, sy)
        measured = [m for m in self._measure(lines, sx, sy) if m.runs]
        if not measured:
            return None
        block_w = max(m.width for m in measured)
        block_h = sum(m.height for m in measured) + max(0.0, (len(measured) - 1) * 4.0 * sy)
        # line spacing: small gap, ASS fonts already include leading
        x, y, _h, _v, _ml, _mv = self._block_position(
            parsed, style, event, block_w, block_h, render_w, render_h, sx, sy)
        max_outline = max((r.outline for r in (run for line in measured for run in line.runs)),
                          default=0) * sy
        max_shadow = max((r.shadow for r in (run for line in measured for run in line.runs)),
                         default=0) * sy
        pad = int(math.ceil(max(max_outline, max_shadow))) + 3
        if parsed.blur > 0:
            pad += int(math.ceil(parsed.blur * sy * 2))
        img_w = max(1, int(math.ceil(block_w)) + pad * 2)
        img_h = max(1, int(math.ceil(block_h)) + pad * 2)
        img = Image.new('RGBA', (img_w, img_h), CLEAR)
        draw = ImageDraw.Draw(img)
        # opaque box border style
        if style.border_style == 3:
            box = style.back_color
            draw.rectangle((0, 0, img_w - 1, img_h - 1), fill=box)
        y_line = pad
        for line in measured:
            base_y = y_line + line.ascent
            if _h == 1:
                x_line = pad
            elif _h == 2:
                x_line = pad + (block_w - line.width) / 2.0
            else:
                x_line = pad + block_w - line.width
            for run, font, fake, w in zip(line.runs, line.fonts, line.fake_bold, line.widths):
                tx = x_line
                self._draw_run(draw, run, font, fake, tx, base_y, sx, sy)
                x_line += w
            y_line += line.height + 4.0 * sy
        if parsed.blur > 0:
            try:
                # blur only the alpha channel: blurring straight RGBA would
                # blend the transparent black background into the glyph edges
                alpha = img.getchannel('A').filter(
                    ImageFilter.GaussianBlur(parsed.blur * sy))
                img.putalpha(alpha)
            except Exception:
                pass
        x_out = int(round(x)) - pad
        y_out = int(round(y)) - pad
        clipped = self._apply_clip(img, x_out, y_out, parsed, sx, sy)
        if clipped is None:
            return None
        return clipped

    def _apply_clip(self, img: Image.Image, x_out: int, y_out: int,
                    parsed: ParsedText, sx: float, sy: float):
        clip = parsed.clip
        if not clip:
            return img, x_out, y_out
        x1, y1, x2, y2 = clip
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1
        left = int(math.floor(x1 * sx - x_out))
        top = int(math.floor(y1 * sy - y_out))
        right = int(math.ceil(x2 * sx - x_out))
        bottom = int(math.ceil(y2 * sy - y_out))
        w, h = img.size
        if parsed.clip_inverse:
            mask = Image.new('L', (w, h), 255)
            md = ImageDraw.Draw(mask)
            l, t = max(0, left), max(0, top)
            r, b = min(w, right), min(h, bottom)
            if r > l and b > t:
                md.rectangle((l, t, r - 1, b - 1), fill=0)
            img.putalpha(ImageChops.multiply(img.getchannel('A'), mask))
            bbox = img.getbbox()
            if bbox is None:
                return None
            img = img.crop(bbox)
            x_out += bbox[0]
            y_out += bbox[1]
            return img, x_out, y_out
        l, t = max(0, left), max(0, top)
        r, b = min(w, right), min(h, bottom)
        if r <= l or b <= t:
            return None
        img = img.crop((l, t, r, b))
        return img, x_out + l, y_out + t

    def _draw_run(self, draw, run: Run, font, fake_bold: bool,
                  x: float, baseline: float, sx: float, sy: float) -> None:
        outline = max(0, int(round(run.outline * sy)))
        shadow = max(0.0, run.shadow * sy)
        primary = run.primary
        outline_col = run.outline_color
        shadow_col = run.shadow_color
        draw_x = x
        draw_y = baseline
        spacing = run.spacing * sx
        try:
            total_w = self._run_width(run, font, sx)
            if spacing and run.text:
                # letter-spacing: draw one glyph at a time
                def paint(text: str, px: float, fill, stroke_w: int, stroke_col) -> None:
                    cx = px
                    for ch in text:
                        draw.text((cx, draw_y), ch, font=font, anchor='ls',
                                  fill=fill, stroke_width=stroke_w,
                                  stroke_fill=stroke_col)
                        cx += font.getlength(ch) + spacing

                if shadow > 0:
                    paint(run.text, draw_x + shadow, shadow_col, 0, shadow_col)
                if fake_bold:
                    paint(run.text, draw_x, primary, outline, outline_col)
                    paint(run.text, draw_x + 1, primary, outline, outline_col)
                else:
                    paint(run.text, draw_x, primary, outline, outline_col)
            else:
                if shadow > 0:
                    draw.text((draw_x + shadow, draw_y + shadow), run.text, font=font,
                              anchor='ls', fill=shadow_col)
                if fake_bold:
                    for off in (0.0, 1.0):
                        draw.text((draw_x + off, draw_y), run.text, font=font, anchor='ls',
                                  fill=primary, stroke_width=outline,
                                  stroke_fill=outline_col)
                else:
                    draw.text((draw_x, draw_y), run.text, font=font, anchor='ls',
                              fill=primary, stroke_width=outline, stroke_fill=outline_col)
            try:
                ascent, desc = font.getmetrics()
            except Exception:
                ascent, desc = int(run.fontsize), 0
            if run.underline:
                uy = draw_y + max(1, int(desc * 0.2))
                draw.line((draw_x, uy, draw_x + total_w, uy),
                          fill=primary, width=max(1, int(run.fontsize * sy / 18)))
            if run.strikeout:
                syy = draw_y - ascent * 0.35
                draw.line((draw_x, syy, draw_x + total_w, syy),
                          fill=primary, width=max(1, int(run.fontsize * sy / 18)))
        except Exception:
            pass


# --------------------------------------------------------------------------- palette
def _quantize_image(img: 'Image.Image') -> tuple[list[bytearray], list[RGBA]]:
    w, h = img.size
    if w <= 0 or h <= 0:
        return [], []
    # knock out almost-transparent pixels so palette 0 is clean
    alpha = img.getchannel('A').point(lambda a: 0 if a < 4 else a)
    img = img.copy()
    img.putalpha(alpha)
    try:
        q = img.quantize(colors=254, method=Image.Quantize.FASTOCTREE,
                         dither=Image.Dither.NONE)
    except Exception:
        q = img.convert('P', palette=Image.Palette.ADAPTIVE, colors=254)
    pal = q.getpalette('RGBA') or []
    count = len(pal) // 4
    entries: list[RGBA] = []
    for i in range(min(254, count)):
        r, g, b, a = pal[i * 4:i * 4 + 4]
        entries.append((r, g, b, a))
    if not entries:
        return [bytearray(w) for _ in range(h)], [CLEAR]
    # transparent palette slot: if the image has an alpha=0 entry, use it for
    # background pixels.  A fully opaque crop (e.g. an opaque box) has no such
    # entry: keep all colours and map pixels to rows 1..N.
    trans = next((i for i, col in enumerate(entries) if col[3] == 0), None)
    colors: list[RGBA] = [CLEAR]
    table = [0] * 256
    for i, col in enumerate(entries):
        if i == trans:
            table[i] = 0
        else:
            if len(colors) >= 255:
                table[i] = 0
                continue
            table[i] = len(colors)
            colors.append(col)
    data = q.tobytes().translate(bytes(table))
    rows = [bytearray(data[y * w:(y + 1) * w]) for y in range(h)]
    if len(colors) > 255:
        colors = colors[:255]
    return rows, colors


# --------------------------------------------------------------------------- backend
@dataclass
class RenderUnit:
    start: int
    end: int
    events: list[Event]


def _build_units(events: list[Event], warnings: list[str]) -> list[RenderUnit]:
    times = sorted({e.start for e in events} | {e.end for e in events})
    units: list[RenderUnit] = []
    overlapped = False
    for a, b in zip(times, times[1:]):
        if b <= a:
            continue
        active = [e for e in events if e.start <= a and e.end >= b]
        if not active:
            continue
        if len(active) > 1:
            overlapped = True
        units.append(RenderUnit(a, b, active))
    if overlapped:
        warnings.append('检测到重叠字幕，已按时间区间合成为同一画面')
    return units


class TextSubtitle:
    """A VobSub-like backend for rendered text subtitles."""

    is_text = True

    def __init__(self, path: str | Path):
        if not PIL_OK:
            raise TextSubError(f'需要 Pillow 才能转换文本字幕（{PIL_ERROR}）')
        self.sub_path = Path(path)
        self.idx_path = None
        self.idx = None
        self.tracks = [object()]
        self._track_names = ['文本字幕 / Text']
        self.size = (1920, 1080)
        self.render_size = (1920, 1080)
        self.warnings: list[str] = []
        text, _enc = _read_text(self.sub_path)
        suffix = self.sub_path.suffix.lower()
        if suffix in ('.ass', '.ssa') or '[V4+ Styles]' in text or '[V4 Styles]' in text:
            styles, events, self.play_res, warns = parse_ass(text, self.sub_path)
            self.format = 'ASS/SSA'
        elif suffix == '.vtt' or text.lstrip('\ufeff').startswith('WEBVTT'):
            styles, events, self.play_res, warns = parse_vtt(text)
            self.format = 'WebVTT'
        elif suffix == '.srt' or '-->' in text:
            styles, events, self.play_res, warns = parse_srt(text)
            self.format = 'SRT'
        else:
            styles, events, self.play_res, warns = parse_microdvd(text)
            self.format = 'MicroDVD'
        self.styles = styles
        self.events = events
        self.warnings.extend(warns)
        self.warnings.append(f'文本字幕（{self.format}）为重新渲染，非无损转换')
        self.units = _build_units(events, self.warnings)
        if not self.units:
            raise TextSubError('没有可用的字幕时间段')
        self.entries: list[vobsub.Entry] = [
            vobsub.Entry(i, u.start, 0, 0) for i, u in enumerate(self.units)
        ]
        self._cache: dict[int, vobsub.Subtitle] = {}
        self.renderer = Renderer(styles, self.play_res)

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

    def set_render_size(self, width: int, height: int) -> None:
        width = max(1, int(width))
        height = max(1, int(height))
        if (width, height) != self.render_size:
            self.render_size = (width, height)
            self._cache.clear()

    def decode(self, entry: vobsub.Entry, end_fallback: int | None = None) -> vobsub.Subtitle:
        cached = self._cache.get(entry.index)
        if cached is not None:
            return cached
        unit = self.units[entry.index]
        rendered = self._render_unit(unit)
        if rendered is None:
            sub = vobsub.Subtitle(entry.index, unit.start, unit.end, 0, 0, 1, 1,
                                  [bytearray(1)], [CLEAR], self.warnings[:])
        else:
            x, y, w, h, rows, colors = rendered
            for wmsg in self.renderer.warnings:
                if wmsg not in self.warnings:
                    self.warnings.append(wmsg)
            sub = vobsub.Subtitle(entry.index, unit.start, unit.end, x, y, w, h,
                                  rows, colors, self.warnings[:])
        if len(self._cache) > 8:
            self._cache.clear()
        self._cache[entry.index] = sub
        return sub

    def _render_event(self, event: Event) -> tuple[Image.Image, int, int] | None:
        style = self.styles.get(event.style) or self.styles.get('Default') or Style()
        parsed = parse_event_text(event.text, style, self.styles)
        if parsed is None:
            return None
        if parsed.warnings:
            for w in parsed.warnings:
                if w not in self.warnings:
                    self.warnings.append(w)
        render_w, render_h = self.render_size
        sx = render_w / float(self.play_res[0] or render_w)
        max_width = render_w - (style.margin_l + style.margin_r) * sx
        if self.format not in ('ASS/SSA',):
            max_width = min(max_width, render_w * 0.92)
        if max_width < render_w * 0.35:
            max_width = render_w * 0.9
        return self.renderer.render(parsed, style, event, render_w, render_h,
                                    wrap=(parsed.pos is None),
                                    max_width=max_width)

    def _render_unit(self, unit: RenderUnit):
        parts = []
        for ev in sorted(unit.events, key=lambda e: (e.layer, e.index)):
            r = self._render_event(ev)
            if r is not None:
                parts.append(r)
        if not parts:
            return None
        W, H = self.render_size
        if len(parts) == 1:
            img, x, y = parts[0]
            left = max(0, x)
            top = max(0, y)
            right = min(W, x + img.width)
            bottom = min(H, y + img.height)
            if right <= left or bottom <= top:
                return None
            img = img.crop((left - x, top - y, right - x, bottom - y))
            rows, colors = _quantize_image(img)
            return left, top, right - left, bottom - top, rows, colors
        xs = [x for _im, x, _y in parts]
        ys = [y for _im, _x, y in parts]
        left = max(0, min(xs))
        top = max(0, min(ys))
        right = min(W, max(x + im.width for im, x, _y in parts))
        bottom = min(H, max(y + im.height for im, _x, y in parts))
        if right <= left or bottom <= top:
            return None
        out = Image.new('RGBA', (right - left, bottom - top), CLEAR)
        for im, x, y in parts:
            px, py = x - left, y - top
            src = im
            if px < 0:
                src = src.crop((-px, 0, src.width, src.height))
                px = 0
            if py < 0:
                src = src.crop((0, -py, src.width, src.height))
                py = 0
            if px + src.width > out.width:
                src = src.crop((0, 0, out.width - px, src.height))
            if py + src.height > out.height:
                src = src.crop((0, 0, src.width, out.height - py))
            if src.width > 0 and src.height > 0:
                out.alpha_composite(src, (px, py))
        rows, colors = _quantize_image(out)
        return left, top, out.width, out.height, rows, colors
