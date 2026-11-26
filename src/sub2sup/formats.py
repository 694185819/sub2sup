"""Open either a VobSub bitmap subtitle or a text subtitle (ASS/SRT/VTT)."""
from __future__ import annotations

from pathlib import Path

from . import vobsub

try:
    from . import textsub as _textsub
except Exception:  # noqa: BLE001 - text rendering is optional
    _textsub = None
try:
    from . import supread as _supread
except Exception:  # noqa: BLE001
    _supread = None

TEXT_EXTENSIONS = {'.ass', '.ssa', '.srt', '.vtt'}


def _is_text_sub(path: Path) -> bool:
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    if head.startswith(b'\xef\xbb\xbf'):
        head = head[3:]
    elif head.startswith((b'\xff\xfe', b'\xfe\xff')):
        return True
    try:
        text = head.decode('utf-8', 'ignore')
    except Exception:
        text = ''
    low = text.lower()
    if low.lstrip().startswith(('webvtt', '[script info]')):
        return True
    if low.lstrip().startswith('{'):
        return True
    return '-->' in low or '[events]' in low


def open_subtitle(path: str | Path):
    """Return a backend exposing iter_entries/decode/decode_all like VobSub."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == '.sup':
        if _supread is None:
            raise RuntimeError('SUP 读取组件不可用')
        return _supread.SupSubtitle(p)
    if suffix in TEXT_EXTENSIONS or (suffix == '.sub' and _is_text_sub(p)):
        if _textsub is None:
            raise RuntimeError('文本字幕渲染组件不可用')
        return _textsub.TextSubtitle(p)
    return vobsub.VobSub(str(p))
