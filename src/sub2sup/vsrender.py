"""Optional high-fidelity ASS rendering via easyavs2bdnxml + VSFilterMod.

This is not used by the built-in renderer; it lets users who have AviSynth,
VSFilterMod and avs2bdnxml installed render complex ASS effects (drawings,
animation, rotation, karaoke) and then load the resulting SUP in Sub2Sup for
preview / deletion / export.
"""
from __future__ import annotations

import ctypes
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import supread, textsub, updater


class VsRenderError(Exception):
    pass


KNOWN_ROOTS = [
    Path(r'I:\ass-sup'),
    Path(r'D:\ass-sup'),
    Path.home() / 'ass-sup',
]


def _known_roots() -> list[Path]:
    roots: list[Path] = []
    for value in (os.environ.get('SUB2SUP_TOOLS'),):
        if value:
            try:
                p = Path(value).resolve()
                if p.is_dir():
                    roots.append(p)
            except OSError:
                pass
    roots.append(Path(sys.executable).resolve().parent)
    for p in KNOWN_ROOTS:
        try:
            p = p.resolve()
            if p.is_dir() and p not in roots:
                roots.append(p)
        except OSError:
            pass
    try:
        cwd = Path.cwd().resolve()
        if cwd not in roots:
            roots.append(cwd)
    except OSError:
        pass
    return roots


def _short_path(path: Path) -> str:
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
        if n:
            return buf.value
    except Exception:
        pass
    return str(path)


def _first_existing(paths: list[Path]) -> str:
    for p in paths:
        try:
            if p and p.is_file():
                return str(p.resolve())
        except OSError:
            continue
    return ''


def detect_components() -> dict:
    settings = updater._settings()
    roots = _known_roots()
    avs_candidates: list[Path] = []
    for value in (os.environ.get('SUB2SUP_AVS2BDNXML'), settings.get('vs_avs2bdnxml')):
        if value:
            avs_candidates.append(Path(value))
    which = shutil.which('avs2bdnxml.exe') or shutil.which('avs2bdnxml')
    if which:
        avs_candidates.append(Path(which))
    for root in roots:
        avs_candidates += [
            root / '02 easyavs2bdnxml' / 'core' / 'avs2bdnxml.exe',
            root / 'easyavs2bdnxml-0.3.4' / 'core' / 'avs2bdnxml.exe',
            root / 'VerySup' / 'tools' / 'avs2bdnxml.exe',
            root / 'avs2bdnxml.exe',
        ]
    avs2bdnxml = _first_existing(avs_candidates)

    vf_candidates: list[Path] = []
    for value in (os.environ.get('SUB2SUP_VSFILTER'), settings.get('vs_vsfilter')):
        if value:
            vf_candidates.append(Path(value))
    for root in roots:
        vf_candidates += [
            root / 'csri' / 'VSFilterMod.dll',
            root / 'mod' / 'S-VSFilterMod.dll',
            root / 'mod' / 'VSFilterMod.dll',
            root / '02 easyavs2bdnxml' / 'core' / 'VSFilterMod.dll',
        ]
    vsfilter = _first_existing(vf_candidates)

    avs_ok = False
    win = Path(os.environ.get('WINDIR', r'C:\Windows'))
    if (win / 'System32' / 'avisynth.dll').exists() or (win / 'SysWOW64' / 'avisynth.dll').exists():
        avs_ok = True
    try:
        import winreg
        for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\AviSynth'),
                          (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\AviSynth')):
            try:
                winreg.OpenKey(hive, sub)
                avs_ok = True
            except OSError:
                pass
    except Exception:
        pass
    return {
        'avs2bdnxml': avs2bdnxml,
        'vsfilter': vsfilter,
        'avisynth': avs_ok,
        'found': bool(avs2bdnxml and vsfilter and avs_ok),
    }


def save_components(avs2bdnxml: str, vsfilter: str) -> None:
    data = updater._settings()
    data['vs_avs2bdnxml'] = str(avs2bdnxml)
    data['vs_vsfilter'] = str(vsfilter)
    updater._save_settings(data)


def _decode_output(data: bytes) -> str:
    for enc in ('utf-8', 'gb18030', 'cp936', 'latin-1'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1', 'replace')


def render_ass(ass_path: str | Path, out_sup: str | Path,
               avs2bdnxml: str, vsfilter: str,
               fps: float = 23.976, language: str = 'und',
               progress=None, cancel=None, log=None) -> dict:
    """Render one ASS/SSA file to SUP with avs2bdnxml + VSFilterMod."""
    ass_path = Path(ass_path)
    out_sup = Path(out_sup)
    avs2bdnxml_p = Path(avs2bdnxml)
    vsfilter_p = Path(vsfilter)
    if not ass_path.is_file():
        raise VsRenderError(f'ASS 文件不存在: {ass_path}')
    if not avs2bdnxml_p.is_file():
        raise VsRenderError('avs2bdnxml.exe 路径无效')
    if not vsfilter_p.is_file():
        raise VsRenderError('VSFilterMod.dll 路径无效')
    try:
        comp = detect_components()
        if not comp.get('avisynth'):
            raise VsRenderError('系统未检测到 AviSynth；请先安装 AviSynth 2.5/2.6+ 或 AviSynth+')
    except VsRenderError:
        raise
    except Exception:
        pass
    text, _enc = textsub._read_text(ass_path)
    styles, events, play_res, warns = textsub.parse_ass(text, ass_path)
    if not events:
        raise VsRenderError('ASS 中没有可渲染的 Dialogue 事件')
    end = max(e.end for e in events)
    frames = max(2, int(math.ceil(end / 90000.0 * fps)) + 2)
    tmp = Path(tempfile.mkdtemp(prefix='sub2sup-vs-'))
    try:
        utf16 = tmp / 'input.ass'
        utf16.write_text(text, encoding='utf-16')
        avs = tmp / 'render.avs'
        script = (
            f'LoadPlugin("{_short_path(vsfilter_p)}")\n'
            f'MaskSubMod("{_short_path(utf16)}", 1920, 1080, {fps:.3f}, {frames})\n'
            'LanczosResize(1920, 1080)\n'
        )
        avs.write_text(script, encoding='ascii', errors='replace')
        out_sup.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_sup.unlink(missing_ok=True)
        except OSError:
            pass
        cmd = [str(avs2bdnxml_p), '-t', 'Sub2Sup', '-l', language,
               '-v', '1080p', '-f', f'{fps:g}', '-a1', '-p0', '-b0', '-u0',
               '-o', str(out_sup), str(avs)]
        flags = 0
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            flags |= subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=flags, bufsize=0)
        out_lines: list[str] = []
        last = {'p': ''}
        prog_re = re.compile(r'Progress:\s*(\d+)/(\d+)')

        def reader() -> None:
            assert proc.stdout is not None
            while True:
                try:
                    chunk = proc.stdout.read(1024)
                except Exception:
                    break
                if not chunk:
                    break
                msg = _decode_output(chunk)
                out_lines.append(msg)
                if log is not None:
                    log(msg)
                for m in prog_re.finditer(msg):
                    try:
                        done, total = int(m.group(1)), int(m.group(2))
                        if progress is not None and total:
                            progress(done, total)
                    except ValueError:
                        pass

        th = threading.Thread(target=reader, daemon=True)
        th.start()
        while proc.poll() is None:
            if cancel is not None and cancel():
                try:
                    proc.kill()
                except OSError:
                    pass
                raise VsRenderError('已取消高级渲染')
            time.sleep(0.15)
        proc.wait()
        th.join(timeout=2)
        if proc.returncode != 0:
            tail = ''.join(out_lines)[-1200:]
            raise VsRenderError(f'avs2bdnxml 失败（exit {proc.returncode}）：{tail}')
        if not out_sup.exists() or out_sup.stat().st_size < 100:
            tail = ''.join(out_lines)[-1200:]
            raise VsRenderError(f'高级渲染没有生成有效 SUP：{tail}')
        sup = supread.SupSubtitle(out_sup)
        if not sup.entries:
            raise VsRenderError('高级渲染生成的 SUP 没有字幕段')
        return {'entries': len(sup.entries), 'size': sup.size, 'warnings': sup.warnings}
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
