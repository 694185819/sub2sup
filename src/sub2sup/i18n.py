"""Tiny two-language layer for the GUI (zh / en)."""
from __future__ import annotations

import json
import os
from pathlib import Path

LANG_CHOICES = [('zh', '中文'), ('en', 'English')]

_lang = 'zh'
_pref_file = Path(os.environ.get('APPDATA') or Path.home()) / 'Sub2Sup' / 'settings.json'

# Static UI strings: Chinese -> English.  Dynamic templates use the exact
# Chinese template as the key and the GUI calls T(template).format(...).
EN: dict[str, str] = {
    # window / menu
    '字幕转 SUP 工具  v{version}': 'Sub2Sup  v{version}',
    '字幕转 SUP 工具  v{version}  —  {name}': 'Sub2Sup  v{version}  —  {name}',
    '文件': 'File',
    '编辑': 'Edit',
    '视图': 'View',
    '帮助': 'Help',
    '打开字幕文件 (idx/sub)…': 'Open subtitle file (idx/sub)…',
    '导出为 SUP…': 'Export to SUP…',
    '打开文件所在文件夹': 'Open containing folder',
    '退出': 'Exit',
    '删除所选段落 (Delete)': 'Remove selected segment(s) (Delete)',
    '恢复所选段落': 'Restore selected segment(s)',
    '全部恢复': 'Restore all',
    '撤销上一次删除': 'Undo last removal',
    '显示缩略图': 'Show thumbnails',
    '跳转到时间…': 'Jump to time…',
    '语言': 'Language',
    '使用说明': 'Help',
    '关于': 'About',
    # toolbar
    '打开字幕文件…': 'Open subtitle file…',
    '删除所选': 'Remove selected',
    '恢复所选': 'Restore selected',
    '撤销': 'Undo',
    '筛选：': 'Filter: ',
    '跳转时间…': 'Jump to time…',
    '语言轨：': 'Language track: ',
    '语言：': 'Language: ',
    # columns / zones / filters
    '缩略图': 'Thumbnail',
    '序号': '#',
    '开始': 'Start',
    '结束': 'End',
    '时长': 'Duration',
    '位置': 'Position',
    '尺寸': 'Size',
    '顶部': 'Top',
    '中部': 'Middle',
    '底部': 'Bottom',
    '整屏': 'Full',
    '全部': 'All',
    '未删除': 'Kept',
    '已删除': 'Removed',
    '仅顶部': 'Top only',
    '仅整屏': 'Full-screen only',
    '筛选：{mode}': 'Filter: {mode}',
    # preview
    '打开字幕文件后在这里预览\n\n(黑底位置与原片一致)':
        'Open a subtitle file to preview\n\n(black frame matches the original subtitle position)',
    '打开字幕文件后在这里预览\n\n(黑底位置与原片一致)\n\n也可以把 .idx / .sub 文件拖进窗口':
        'Open a subtitle file to preview\n\n(black frame matches the original subtitle position)'
        '\n\nYou can also drop a .idx / .sub file into this window',
    '拖入的文件不是字幕': 'Not a subtitle file',
    '请拖入 .idx 或 .sub 文件。': 'Please drop a .idx or .sub file.',
    '◀ 上一段': '◀ Previous',
    '下一段 ▶': 'Next ▶',
    '删除此段 (Space)': 'Remove this segment (Space)',
    '恢复此段 (Space)': 'Restore this segment (Space)',
    '细节 1:1': 'Zoom 1:1',
    '整帧预览': 'Frame view',
    '预览失败：{exc}': 'Preview failed: {exc}',
    '#{index:04d}    {start} → {end}    {dur:.3f} 秒    位置 ({x}, {y})    {w}×{h}':
        '#{index:04d}    {start} → {end}    {dur:.3f} s    at ({x}, {y})    {w}×{h}',
    '{done} / {total} 段': '{done} / {total}',
    # status
    '未打开文件': 'No file open',
    '共 {total} 段   保留 {kept} 段   已删除 {removed} 段':
        '{total} segments · {kept} kept · {removed} removed',
    '显示 {shown} / {total} 段': 'Showing {shown} / {total}',
    '共 {total} 段': '{total} segments',
    # load dialog
    '正在读取字幕': 'Loading subtitles',
    '正在解析：{name}': 'Parsing: {name}',
    '准备中…': 'Preparing…',
    '取消': 'Cancel',
    '正在取消…': 'Cancelling…',
    '打开失败': 'Open failed',
    '单 .sub 文件：调色板使用经典 VobSub 默认值，画布尺寸请按原片在导出时确认。':
        'Lone .sub file: using the classic VobSub default palette; '
        'please confirm the canvas size when exporting.',
    # file dialogs
    '打开 VobSub 字幕（.idx 会自动找同名 .sub）':
        'Open VobSub subtitle (.idx finds the matching .sub automatically)',
    'VobSub 字幕': 'VobSub subtitle',
    '所有文件': 'All files',
    '保存 SUP 文件': 'Save SUP file',
    'Blu-ray 字幕': 'Blu-ray subtitle',
    # export dialog
    '导出为 Blu-ray SUP (PGS)': 'Export Blu-ray SUP (PGS)',
    '将导出 {kept} 段（已删除 {removed} 段）':
        'Will export {kept} segment(s) ({removed} removed)',
    '输出文件：': 'Output file: ',
    '浏览…': 'Browse…',
    '画布尺寸：': 'Canvas size: ',
    '帧率代码：': 'Frame rate code: ',
    '时间偏移 (ms)：': 'Time offset (ms): ',
    '提示：输出为 1920×1080 时按 BT.709 转换调色板，颜色误差 ≤1/255；':
        'Note: 1080p output converts the palette with BT.709 (colour error ≤1/255); ',
    '位图与时间轴完全保留。': 'bitmaps and timing are preserved exactly.',
    '开始导出': 'Start export',
    '参数错误': 'Invalid parameters',
    '输出路径为空': 'Output path is empty',
    '覆盖文件': 'Overwrite file',
    '文件已存在：\n{path}\n\n是否覆盖？':
        'File already exists:\n{path}\n\nOverwrite?',
    '导出失败': 'Export failed',
    '解码 {done}/{total} 段': 'Decoding {done}/{total}',
    '写入 {done}/{total} 段': 'Writing {done}/{total}',
    '完成': 'Done',
    '导出成功': 'Export complete',
    '导出成功！\n\n{path}\n\n{count} 段，{size:.2f} MB\n{check}':
        'Export complete!\n\n{path}\n\n{count} segments, {size:.2f} MB\n{check}',
    '\n\n是否打开所在文件夹？': '\n\nOpen the containing folder?',
    '结构自检通过：{segments} 个段 / {sets} 个显示集':
        'Structure check passed: {segments} segments / {sets} display sets',
    '结构自检异常：{error}': 'Structure check failed: {error}',
    '没有可导出的段落': 'Nothing to export',
    '所有段落都被删除了。': 'All segments have been removed.',
    # jump dialog
    '跳转到时间': 'Jump to time',
    '输入时间（如 00:03:26.5 或 206.5 秒）：':
        'Enter a time (e.g. 00:03:26.5 or 206.5 s):',
    '时间格式错误': 'Invalid time',
    '请输入 mm:ss 或 hh:mm:ss.mmm': 'Enter mm:ss or hh:mm:ss.mmm',
    '跳转': 'Jump',
    # misc
    '关闭': 'Close',
    # frame rate choices
    '23.976 (Blu-ray)': '23.976 (Blu-ray)',
    '25 (PAL)': '25 (PAL)',
    '29.97 (NTSC)': '29.97 (NTSC)',
    # UHD / HDR profile
    '输出规格：': 'Output profile: ',
    'Blu-ray 1080p (SDR)': 'Blu-ray 1080p (SDR)',
    'UHD HDR (1080p PGS，推荐)': 'UHD HDR (1080p PGS, recommended)',
    '4K PGS（试验性）': '4K PGS (experimental)',
    'HDR 字幕亮度：': 'HDR subtitle brightness: ',
    '保留原白 (Y=235)': 'Keep original white (Y=235)',
    'UHD 灰 144 (Y≈140，推荐)': 'UHD grey 144 (Y≈140, recommended)',
    '灰 160 (Y≈153)': 'Grey 160 (Y≈153)',
    '灰 180 (Y≈171)': 'Grey 180 (Y≈171)',
    '自定义 Y：': 'Custom Y: ',
    '自定义 Y': 'Custom Y',
    'UHD 推荐：保持 1920×1080 PGS（播放器放大到 4K），HDR 下降低字幕白亮度。':
        'UHD recommended: keep 1920×1080 PGS (the player upscales to 4K) and lower '
        'the subtitle white level for HDR.',
    '4K PGS 为试验性输出：部分播放器/封装工具可能不支持。':
        '4K PGS is experimental: some players and muxers may not support it.',
    '画布将由播放器放大到 4K；字幕亮度已按 HDR 预设调整。':
        'The player will upscale the 1080p graphics to 4K; the palette luma follows the HDR preset.',
    '当前源画布 {sw}×{sh}，目标 {tw}×{th}，缩放 {sx:.3f}×{sy:.3f}（非整数缩放会有 1px 取整偏差）。':
        'Source canvas {sw}×{sh}, target {tw}×{th}, scale {sx:.3f}×{sy:.3f} '
        '(non-integer scaling can shift geometry by 1 px).',
    '调色板峰值 Y：{y}': 'Peak palette Y: {y}',
    '校验问题：{issues}': 'Validation issues: {issues}',
    'UHD 帧率建议 23.976 / 24 / 25 / 50 / 59.94 / 60。':
        'UHD frame rates: 23.976 / 24 / 25 / 50 / 59.94 / 60.',
    # updates
    '检查更新…': 'Check for updates…',
    '检查更新': 'Check for updates',
    '发现新版本 {tag}，点击更新': 'New version {tag} available — click to update',
    '已是最新版本 v{version}': 'You are up to date (v{version})',
    '检查更新失败：{error}': 'Update check failed: {error}',
    '软件更新': 'Software update',
    '当前版本：v{current}': 'Current version: v{current}',
    '最新版本：v{latest}': 'Latest version: v{latest}',
    '更新说明': 'Release notes',
    '（该 Release 没有更新说明，请打开 Release 页面查看。）':
        '(No release notes in this release; open the release page for details.)',
    '打开 Release 页面': 'Open release page',
    '下载并安装': 'Download and install',
    '下载中：{done} / {total} MB': 'Downloading: {done} / {total} MB',
    '下载完成': 'Download complete',
    '立即安装并重启': 'Install and restart',
    '打开下载位置': 'Open download folder',
    '下载失败：{error}': 'Download failed: {error}',
    '无法自动安装：目标目录不可写。\\n文件已下载到：\\n{path}':
        'Cannot install automatically: target folder is not writable.\\nDownloaded to:\\n{path}',
    '安装程序将在本程序退出后替换并重启。是否继续？':
        'The updater will replace this program after it exits and restart it. Continue?',
    '已取消下载': 'Download cancelled',
    '取消下载': 'Cancel download',
}

HELP_ZH = """字幕转 SUP 工具 使用说明

一、打开字幕
  1. 点「打开字幕文件…」选择 .idx（程序会自动找同名 .sub）。
  2. 也可以直接把 .idx / .sub 拖进程序窗口，或拖到 Sub2Sup.exe 图标上。
  3. 单独一个 .sub 也能打开（缺调色板信息，使用经典 VobSub 默认调色板）。

二、预览与删除段落
  * 左侧列表显示每段的缩略图、开始/结束时间、位置和尺寸；“位置”指该段在原片
    画面中的位置（顶部/中部/底部/整屏）。点某一段，右侧显示它在 1920×1080 画面
    中的实际位置预览；点「细节 1:1」可放大看字幕本身。
  * 找字幕组信息：把「筛选」设为「仅顶部」或「仅整屏」，通常片头/片尾的组信息
    就在这类位置。
  * 选中一段或多段（Ctrl/Shift 多选），点「删除所选」（或按空格/Delete/双击）。
    删除只是标记，不改原文件；可随时「恢复所选」「撤销」或「全部恢复」。

三、导出 SUP
  1. 点「导出为 SUP…」，确认输出文件名。  2. 画布尺寸：.idx 文件会自动使用其标注的尺寸；单独 .sub 需自行确认。
  3. 帧率代码默认 23.976（常见 Blu-ray 电影）；如原片是 24/25/29.97 等请选择对应值。
  4. 点「开始导出」。导出后程序会自动做结构自检。
  5. 得到的 .sup 可直接交给 tsMuxeR / mkvmerge 封装进原盘 ISO。

四、快捷键
  Ctrl+O 打开    Ctrl+E 导出    Ctrl+Z 撤销    Ctrl+G 跳转时间
  Space 删除/恢复当前段    PgUp/PgDn 上下翻 10 段

五、关于“无损”
  * 位图：按原 2-bit 调色板索引 1:1 重编码为 PGS RLE，不重新渲染、不缩放。
  * 时间：起止时间沿用原 90 kHz 时间戳；alpha（0-15）精确映射为 0-255。
  * 颜色：调色板 RGB 转成 PGS 的 YCbCr（BT.709/BT.601 limited range），
    渲染误差 ≤1/255。

六、常见问题
  * 找不到 .sub：请确保 .idx 与 .sub 同名且在同一目录。
  * 单独 .sub 颜色不对：使用默认调色板的缘故；若能找到原 .idx，请打开 .idx。
  * 播放器不显示：确认已把 .sup 作为字幕轨封装进视频/ISO，且播放器开启了字幕。
  * 想保留全部段落：不要删除任何段，直接导出即可。

七、4K / HDR 原盘
  * UHD 原盘通常使用 1920×1080 PGS，由播放器放大到 4K；导出时选择「UHD HDR」即可。
  * HDR 下 SDR 的白会偏亮，建议将字幕白压到 144 灰（Y≈140）；可在导出窗口选择亮度。
  * 帧率仅支持 23.976 / 24 / 25 / 50 / 59.94 / 60。
  * 「4K PGS」为试验性输出，部分播放器/封装工具可能不支持，需自行测试。

八、检查更新
  * 程序启动后会静默检查 GitHub 最新版本（每 24 小时最多一次）。
  * 发现新版本时工具栏右侧会出现「发现新版本 vX，点击更新」；
    也可以用「帮助 → 检查更新…」手动检查。
  * 更新窗口可查看更新说明、打开 Release 页面，或直接「下载并安装」。
  * exe 版下载完成后可「立即安装并重启」：程序退出后自动替换并启动新版；
    源码运行时只下载并打开所在文件夹。
"""

HELP_EN = """Sub2Sup — Help

1. Opening a subtitle
  1. Click "Open subtitle file…" and choose the .idx file (the matching .sub is
     found automatically).
  2. Alternatively, drag a .idx / .sub file into this window, or onto the
     Sub2Sup.exe icon.
  3. A lone .sub can be opened too (it has no palette; the classic VobSub
     default palette is used).

2. Preview and delete segments
  * The list shows a thumbnail, start/end time, position and size for every
    segment. "Position" is where the subtitle sits in the video frame
    (Top / Middle / Bottom / Full). Select a segment to preview it on a black
    1920×1080 frame; click "Zoom 1:1" to inspect the graphic itself.
  * To find fansub credits, set the filter to "Top only" or "Full-screen only".
  * Select one or more segments (Ctrl/Shift), then click "Remove selected"
    (or press Space / Delete / double-click). Removal is only a mark; the
    source file is never modified. Use "Restore selected", "Undo" or
    "Restore all" at any time.

3. Export SUP
  1. Click "Export to SUP…" and confirm the output file name.
  2. Canvas size: .idx files provide it; for a lone .sub please confirm it.
  3. Frame-rate code defaults to 23.976 (common for Blu-ray movies). Choose
     24 / 25 / 29.97 / etc. if your source uses another rate.
  4. Click "Start export". A structure check runs automatically afterwards.
  5. The resulting .sup can be muxed into a Blu-ray ISO with tsMuxeR or
     mkvmerge.

4. Shortcuts
  Ctrl+O open    Ctrl+E export    Ctrl+Z undo    Ctrl+G jump to time
  Space remove/restore current segment    PgUp/PgDn move 10 segments

5. What "lossless" means
  * Bitmap: the original 2-bit palette indices are re-encoded 1:1 into PGS RLE
    (no re-rendering, no scaling).
  * Timing: original 90 kHz start/end timestamps are kept; 4-bit alpha
    (0-15) is mapped exactly to 0-255.
  * Colour: palette RGB is converted to PGS YCbCr (BT.709 / BT.601 limited
    range); the rendered difference is ≤1/255.

6. FAQ
  * .sub not found: make sure the .idx and .sub share the same name and folder.
  * Wrong colours with a lone .sub: the default palette is used; open the .idx
    if you have it.
  * Nothing shows in the player: make sure the .sup is muxed as a subtitle
    track and subtitles are enabled.
  * Keep everything: simply export without removing any segment.

7. 4K / HDR discs
  * UHD discs normally use 1920×1080 PGS and the player upscales it to 4K;
    choose "UHD HDR" when exporting.
  * SDR white looks too bright in HDR. Lower the subtitle white to grey 144
    (Y≈140); the brightness preset is available in the export dialog.
  * Frame rates: 23.976 / 24 / 25 / 50 / 59.94 / 60 only.
  * "4K PGS" is experimental; some players or muxers may not accept it.

8. Checking for updates
  * On startup the app quietly checks GitHub for a newer release (at most
    once per 24 hours).
  * When one exists, a "New version vX available" button appears in the
    toolbar; you can also use Help -> Check for updates.
  * The update dialog shows the release notes, can open the release page,
    and can download and install the new build.
  * In the exe build, "Install and restart" replaces the program after it
    exits and launches the new one; when running from source it only
    downloads the file and opens its folder.
"""

ABOUT_ZH = (
    '字幕转 SUP 工具 v{version}\n\n'
    'VobSub (idx/sub) → Blu-ray PGS (.sup) 无损转换工具\n'
    '支持按段删除字幕（例如字幕组信息），再导出。\n\n'
    '位图: 1:1 重编码（不重新渲染）\n'
    '时间: 保持原始 90 kHz 时间戳\n'
    '颜色: 调色板转 YCbCr，误差 ≤1/255\n\n'
    '可直接用于 tsMuxeR / mkvmerge 等封装。'
)
ABOUT_EN = (
    'Sub2Sup v{version}\n\n'
    'VobSub (idx/sub) → Blu-ray PGS (.sup) lossless converter\n'
    'Remove unwanted segments (e.g. fansub credits) before exporting.\n\n'
    'Bitmap: re-encoded 1:1 (no re-rendering)\n'
    'Timing: original 90 kHz timestamps kept\n'
    'Colour: palette converted to YCbCr, error ≤1/255\n\n'
    'Ready for muxing with tsMuxeR / mkvmerge.'
)


def T(text: str) -> str:
    if _lang == 'zh':
        return text
    return EN.get(text, text)


def get_lang() -> str:
    return _lang


def lang_display() -> str:
    return dict(LANG_CHOICES)[_lang]


def set_lang(lang: str, save: bool = True) -> None:
    global _lang
    if lang not in dict(LANG_CHOICES):
        lang = 'zh'
    _lang = lang
    if save:
        try:
            _pref_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if _pref_file.exists():
                try:
                    data = json.loads(_pref_file.read_text(encoding='utf-8'))
                except Exception:
                    data = {}
            data['lang'] = lang
            _pref_file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass


def _system_lang() -> str:
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return 'zh' if (langid & 0x3FF) == 0x04 else 'en'
    except Exception:
        return 'zh'


def init_lang(forced: str | None = None) -> str:
    global _lang
    if forced in ('zh', 'en'):
        set_lang(forced)
        return _lang
    try:
        if _pref_file.exists():
            data = json.loads(_pref_file.read_text(encoding='utf-8'))
            lang = data.get('lang')
            if lang in ('zh', 'en'):
                _lang = lang
                return _lang
    except Exception:
        pass
    _lang = _system_lang()
    return _lang


def help_text() -> str:
    return HELP_ZH if _lang == 'zh' else HELP_EN


def about_text(version: str) -> str:
    text = ABOUT_ZH if _lang == 'zh' else ABOUT_EN
    return text.format(version=version)
