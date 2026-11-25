"""GitHub release update check / download / self-replace helper."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

OWNER = '694185819'
REPO = 'sub2sup'
ASSET = 'Sub2Sup.exe'
API_URL = f'https://api.github.com/repos/{OWNER}/{REPO}/releases/latest'
LATEST_PAGE = f'https://github.com/{OWNER}/{REPO}/releases/latest'
LATEST_DOWNLOAD = f'https://github.com/{OWNER}/{REPO}/releases/latest/download/{ASSET}'
UA = f'Sub2Sup/{__version__} (+https://github.com/{OWNER}/{REPO})'
SETTINGS = Path(os.environ.get('APPDATA') or Path.home()) / 'Sub2Sup' / 'settings.json'
UPDATES_DIR = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'Sub2Sup' / 'updates'


class UpdateError(Exception):
    pass


class DownloadCancelled(Exception):
    pass


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = re.search(r'(\d+)(?:\.(\d+))?(?:\.(\d+))?', text or '')
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())  # type: ignore[return-value]


@dataclass
class UpdateInfo:
    tag: str
    version: tuple[int, int, int]
    html_url: str
    body: str
    asset_url: str | None
    asset_size: int
    digest: str | None
    published: str = ''

    @property
    def version_text(self) -> str:
        tag = self.tag.lstrip('vV')
        return tag or '.'.join(str(x) for x in self.version)


def _request(url: str, timeout: float = 15, method: str | None = None):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'application/vnd.github+json',
    }, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def _settings() -> dict:
    try:
        if SETTINGS.exists():
            return json.loads(SETTINGS.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


def _save_settings(data: dict) -> None:
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass


def should_auto_check(hours: float = 24.0) -> bool:
    if os.environ.get('SUB2SUP_NO_UPDATE_CHECK'):
        return False
    try:
        last = float(_settings().get('last_update_check', 0))
    except Exception:
        last = 0
    return time.time() - last > hours * 3600


def mark_checked() -> None:
    data = _settings()
    data['last_update_check'] = time.time()
    _save_settings(data)


def _fallback_info() -> UpdateInfo:
    with _request(LATEST_PAGE, timeout=20) as r:
        final = r.geturl()
        tag = ''
        m = re.search(r'/releases/tag/([^/?#]+)', final)
        if m:
            tag = urllib.parse.unquote(m.group(1))
        else:
            html = r.read(300000).decode('utf-8', 'replace')
            m = re.search(r'/releases/tag/(v?[0-9][^"\'<>\s]*)', html)
            if m:
                tag = urllib.parse.unquote(m.group(1))
    if not tag:
        raise UpdateError('无法从 GitHub 页面识别最新版本')
    version = parse_version(tag)
    if version is None:
        raise UpdateError(f'无法解析版本号: {tag}')
    html_url = f'https://github.com/{OWNER}/{REPO}/releases/tag/{tag}'
    asset_url = f'https://github.com/{OWNER}/{REPO}/releases/download/{tag}/{ASSET}'
    size = 0
    try:
        with _request(asset_url, timeout=20, method='HEAD') as r:
            size = int(r.headers.get('Content-Length') or 0)
    except Exception:
        pass
    return UpdateInfo(tag, version, html_url, '', asset_url, size, None)


def check_latest(local_version: str | None = None) -> UpdateInfo | None:
    """Return UpdateInfo when a newer release exists, else None.

    Uses the GitHub API first; falls back to the releases/latest redirect when
    the API is rate-limited or unavailable.
    """
    local = parse_version(local_version or __version__)
    if local is None:
        local = (0, 0, 0)
    info: UpdateInfo | None = None
    try:
        with _request(API_URL, timeout=12) as r:
            data = json.loads(r.read().decode('utf-8'))
        tag = str(data.get('tag_name') or '')
        ver = parse_version(tag)
        if ver is not None:
            asset = None
            for a in data.get('assets') or []:
                if str(a.get('name', '')).lower().endswith('.exe'):
                    asset = a
                    break
            info = UpdateInfo(
                tag=tag,
                version=ver,
                html_url=data.get('html_url') or f'https://github.com/{OWNER}/{REPO}/releases/tag/{tag}',
                body=data.get('body') or '',
                asset_url=(asset or {}).get('browser_download_url'),
                asset_size=int((asset or {}).get('size') or 0),
                digest=(asset or {}).get('digest'),
                published=data.get('published_at') or '',
            )
    except Exception:
        info = None
    if info is None:
        info = _fallback_info()
    if info.version > local:
        return info
    return None


def download(info: UpdateInfo, dest_dir: Path | None = None,
             progress=None, cancel=None) -> Path:
    if not info.asset_url:
        raise UpdateError('该 Release 没有 exe 附件')
    dest_dir = dest_dir or UPDATES_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f'Sub2Sup-{info.tag}.exe'
    part = dest.with_suffix('.part')
    sha = hashlib.sha256()
    done = 0
    try:
        with _request(info.asset_url, timeout=60) as r, open(part, 'wb') as f:
            total = int(r.headers.get('Content-Length') or info.asset_size or 0)
            while True:
                if cancel is not None and cancel():
                    raise DownloadCancelled()
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        size = part.stat().st_size
        if info.asset_size and size != info.asset_size:
            raise UpdateError(f'文件大小不符: {size} != {info.asset_size}')
        if info.digest and info.digest.lower().startswith('sha256:'):
            want = info.digest.split(':', 1)[1].strip().lower()
            if sha.hexdigest().lower() != want:
                raise UpdateError('SHA256 校验失败')
        with open(part, 'rb') as f:
            if f.read(2) != b'MZ':
                raise UpdateError('下载的文件不是 Windows exe')
        os.replace(part, dest)
        return dest
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def can_install(target: Path | None = None) -> bool:
    target = Path(target or sys.executable)
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.sub2sup_', delete=True):
            return True
    except OSError:
        return False


def install_and_restart(new_exe: Path, target: Path | None = None) -> bool:
    """Replace the running exe after exit and restart it (Windows only)."""
    target = Path(target or sys.executable)
    if not can_install(target):
        return False
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    ps1 = UPDATES_DIR / 'apply_update.ps1'
    log = UPDATES_DIR / 'apply_update.log'
    pid = os.getpid()

    def psq(p: Path) -> str:
        return "'" + str(p).replace("'", "''") + "'"

    script = f"""$ErrorActionPreference = 'SilentlyContinue'
$p = Get-Process -Id {pid}
if ($p) {{ $null = $p.WaitForExit(120000) }}
Copy-Item -LiteralPath {psq(Path(new_exe))} -Destination {psq(target)} -Force
if (Test-Path -LiteralPath {psq(target)}) {{
    Start-Process -FilePath {psq(target)}
}} else {{
    'copy failed' | Out-File -FilePath {psq(log)} -Encoding utf8
}}
Remove-Item -LiteralPath $PSCommandPath -Force
"""
    try:
        ps1.write_text(script, encoding='utf-8-sig')
    except OSError:
        return False
    ps = shutil.which('powershell.exe') or r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    params = f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps1}"'
    try:
        import ctypes
        rc = ctypes.windll.shell32.ShellExecuteW(None, 'open', ps, params, None, 0)
        if rc and rc > 32:
            return True
    except Exception:
        pass
    flags = 0
    if hasattr(subprocess, 'CREATE_NO_WINDOW'):
        flags |= subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, 'DETACHED_PROCESS'):
        flags |= subprocess.DETACHED_PROCESS
    try:
        subprocess.Popen([ps, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                          '-WindowStyle', 'Hidden', '-File', str(ps1)],
                         creationflags=flags, close_fds=True)
    except OSError:
        return False
    return True
