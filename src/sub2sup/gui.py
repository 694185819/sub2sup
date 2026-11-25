"""Tkinter GUI for the VobSub → PGS (Blu-ray .sup) converter."""
from __future__ import annotations

import base64
import ctypes
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, pgs, render, vobsub
from .icon_data import ICON_PNG_B64

FONT = ('Microsoft YaHei UI', 10)
FONT_BOLD = ('Microsoft YaHei UI', 10, 'bold')
FONT_SMALL = ('Microsoft YaHei UI', 9)
BG = '#f5f6f8'
PREVIEW_BG = '#000000'

APP_ICON_PNG = base64.b64decode(ICON_PNG_B64)

HELP_TEXT = """字幕转 SUP 工具 使用说明

一、打开字幕
  1. 点「打开字幕文件…」选择 .idx（程序会自动找同名 .sub）。
  2. 也可以直接把 .idx / .sub 拖到 Sub2Sup.exe 上。
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
  1. 点「导出为 SUP…」，确认输出文件名。
  2. 画布尺寸：.idx 文件会自动使用其标注的尺寸；单独 .sub 需自行确认。
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

完整说明与封装 ISO 的详细步骤见随程序附带的“使用说明.txt / README.md”。
"""


def _dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


@dataclass
class ItemInfo:
    index: int
    start: int
    end: int
    x: int
    y: int
    w: int
    h: int
    colors: list
    thumb: bytes
    removed: bool = False
    warnings: list = field(default_factory=list)

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass
class Loaded:
    vobsub: object
    canvas: tuple
    items: list
    guess_canvas: tuple | None
    warnings: list


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.v: vobsub.VobSub | None = None
        self.canvas_size: tuple[int, int] | None = None
        self.items: list[ItemInfo] = []
        self.photos: dict[int, tk.PhotoImage] = {}
        self.loaded = False
        self.track = 0
        self.undo_stack: list[list[int]] = []
        self.filter_mode = '全部'
        self.show_thumbs = tk.BooleanVar(value=True)
        self.preview_mode = 'frame'  # 'frame' | 'crop'
        self.load_q: queue.Queue = queue.Queue()
        self.load_cancel = threading.Event()
        self.load_progress: tk.Toplevel | None = None
        self.current_index: int | None = None
        self._preview_token = 0
        self._busy = False
        self._load_gen = 0
        self._build_style()
        self._build_ui()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.root.after(50, self._poll_load)

    # ---------------------------------------------------------------- UI
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use('vista')
        except tk.TclError:
            pass
        self.root.configure(bg=BG)
        self.root.option_add('*Font', FONT)
        style.configure('TButton', font=FONT, padding=(10, 5))
        style.configure('TLabel', font=FONT, background=BG)
        style.configure('TCheckbutton', background=BG, font=FONT)
        style.configure('TCombobox', font=FONT)
        style.configure('Treeview', font=FONT, rowheight=46)
        style.configure('Treeview.Heading', font=FONT_BOLD)
        style.configure('Status.TLabel', font=FONT_SMALL, background='#eceef2',
                        foreground='#333333')
        style.configure('Hint.TLabel', font=FONT_SMALL, foreground='#777777',
                        background=BG)
        style.configure('Info.TLabel', font=FONT, background=BG)
        style.configure('Tree.TButton', font=FONT, padding=(6, 3))

    def _build_ui(self) -> None:
        self.root.title(f'字幕转 SUP 工具  v{__version__}')
        self.root.geometry('1480x900')
        self.root.minsize(1150, 700)

        menubar = tk.Menu(self.root)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label='打开字幕文件 (idx/sub)…', accelerator='Ctrl+O',
                           command=self.open_dialog)
        m_file.add_command(label='导出为 SUP…', accelerator='Ctrl+E',
                           command=self.export_dialog)
        m_file.add_separator()
        m_file.add_command(label='打开文件所在文件夹', command=self.open_folder)
        m_file.add_command(label='退出', command=self._on_close)
        menubar.add_cascade(label='文件', menu=m_file)

        m_edit = tk.Menu(menubar, tearoff=0)
        m_edit.add_command(label='删除所选段落 (Delete)', command=self.delete_selected)
        m_edit.add_command(label='恢复所选段落', command=self.restore_selected)
        m_edit.add_command(label='全部恢复', command=self.restore_all)
        m_edit.add_separator()
        m_edit.add_command(label='撤销上一次删除', accelerator='Ctrl+Z', command=self.undo)
        menubar.add_cascade(label='编辑', menu=m_edit)

        m_view = tk.Menu(menubar, tearoff=0)
        for mode in ('全部', '未删除', '已删除', '仅顶部', '仅整屏'):
            m_view.add_command(label=f'筛选：{mode}',
                               command=lambda m=mode: self.set_filter(m))
        m_view.add_separator()
        m_view.add_checkbutton(label='显示缩略图', variable=self.show_thumbs,
                               command=self._apply_filter)
        m_view.add_command(label='跳转到时间…', accelerator='Ctrl+G', command=self.jump_time)
        menubar.add_cascade(label='视图', menu=m_view)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label='使用说明', command=lambda: self._open_help())
        m_help.add_command(label='关于', command=self._about)
        menubar.add_cascade(label='帮助', menu=m_help)
        self.root.configure(menu=menubar)

        # toolbar
        bar = ttk.Frame(self.root, padding=(10, 8, 10, 4))
        bar.pack(fill='x')
        self.btn_open = ttk.Button(bar, text='打开字幕文件…', command=self.open_dialog)
        self.btn_open.pack(side='left')
        self.btn_export = ttk.Button(bar, text='导出为 SUP…', command=self.export_dialog,
                                     state='disabled')
        self.btn_export.pack(side='left', padx=(8, 0))
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=12)
        self.btn_del = ttk.Button(bar, text='删除所选', command=self.delete_selected,
                                  state='disabled')
        self.btn_del.pack(side='left')
        self.btn_restore = ttk.Button(bar, text='恢复所选', command=self.restore_selected,
                                      state='disabled')
        self.btn_restore.pack(side='left', padx=(6, 0))
        self.btn_undo = ttk.Button(bar, text='撤销', command=self.undo, state='disabled')
        self.btn_undo.pack(side='left', padx=(6, 0))
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=12)
        ttk.Label(bar, text='筛选：').pack(side='left')
        self.filter_var = tk.StringVar(value='全部')
        self.filter_box = ttk.Combobox(bar, textvariable=self.filter_var, width=9,
                                       state='readonly',
                                       values=['全部', '未删除', '已删除', '仅顶部', '仅整屏'])
        self.filter_box.pack(side='left')
        self.filter_box.bind('<<ComboboxSelected>>',
                             lambda e: self.set_filter(self.filter_var.get()))
        self.filter_box.configure(state='disabled')
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=12)
        self.btn_jump = ttk.Button(bar, text='跳转时间…', command=self.jump_time,
                                   state='disabled')
        self.btn_jump.pack(side='left')
        self.track_label = ttk.Label(bar, text='语言轨：')
        self.track_var = tk.StringVar()
        self.track_box = ttk.Combobox(bar, textvariable=self.track_var, width=18,
                                      state='readonly')
        self.track_box.bind('<<ComboboxSelected>>', self._on_track_change)

        self.filter_label = ttk.Label(bar, text='', style='Hint.TLabel')
        self.filter_label.pack(side='right')

        # main paned
        paned = ttk.PanedWindow(self.root, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=10, pady=(4, 4))

        left = ttk.Frame(paned)
        paned.add(left, weight=3)
        self.tree = ttk.Treeview(left, show='tree headings', selectmode='extended',
                                 columns=('idx', 'start', 'end', 'dur', 'pos', 'size'))
        self.tree.heading('#0', text='缩略图')
        self.tree.column('#0', width=252, minwidth=120, stretch=False)
        self.tree.heading('idx', text='序号')
        self.tree.column('idx', width=56, anchor='w', stretch=False)
        self.tree.heading('start', text='开始')
        self.tree.column('start', width=92, anchor='w', stretch=False)
        self.tree.heading('end', text='结束')
        self.tree.column('end', width=92, anchor='w', stretch=False)
        self.tree.heading('dur', text='时长')
        self.tree.column('dur', width=64, anchor='e', stretch=False)
        self.tree.heading('pos', text='位置')
        self.tree.column('pos', width=110, anchor='w', stretch=False)
        self.tree.heading('size', text='尺寸')
        self.tree.column('size', width=90, anchor='w', stretch=False)
        self.tree.tag_configure('removed', foreground='#a0a0a0')
        self.tree.tag_configure('warn', foreground='#b26a00')
        vsb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        self.tree.bind('<Delete>', lambda e: self.delete_selected())
        self.tree.bind('<space>', lambda e: self.toggle_selected())
        self.tree.bind('<Double-1>', self._on_double)
        self.tree.bind('<Control-z>', lambda e: self.undo())

        right = ttk.Frame(paned, padding=(10, 0, 0, 0))
        paned.add(right, weight=5)
        self.preview_frame = tk.Frame(right, bg=PREVIEW_BG, height=560)
        self.preview_frame.pack(fill='x', expand=False)
        self.preview_frame.pack_propagate(False)
        self.preview_label = tk.Label(self.preview_frame, bg=PREVIEW_BG,
                                      text='打开字幕文件后在这里预览\n\n(黑底位置与原片一致)',
                                      fg='#dddddd', font=('Microsoft YaHei UI', 15))
        self.preview_label.pack(fill='both', expand=True)
        self.preview_frame.bind('<Configure>', self._on_preview_resize)

        info = ttk.Frame(right)
        info.pack(fill='x', pady=(10, 0))
        self.info_label = ttk.Label(info, text='—', style='Info.TLabel')
        self.info_label.pack(anchor='w')
        self.warn_label = ttk.Label(info, text='', style='Hint.TLabel', foreground='#b26a00')
        self.warn_label.pack(anchor='w')

        actions = ttk.Frame(right)
        actions.pack(fill='x', pady=(10, 0))
        self.btn_prev = ttk.Button(actions, text='◀ 上一段', command=lambda: self.step(-1),
                                   state='disabled')
        self.btn_prev.pack(side='left')
        self.btn_next = ttk.Button(actions, text='下一段 ▶', command=lambda: self.step(1),
                                   state='disabled')
        self.btn_next.pack(side='left', padx=(6, 0))
        self.btn_toggle = ttk.Button(actions, text='删除此段 (Space)',
                                     command=self.toggle_selected, state='disabled')
        self.btn_toggle.pack(side='left', padx=(18, 0))
        self.btn_zoom = ttk.Button(actions, text='细节 1:1', command=self.toggle_zoom,
                                   state='disabled')
        self.btn_zoom.pack(side='right')

        # bottom status bar
        status = ttk.Frame(self.root, padding=(10, 4))
        status.pack(fill='x', side='bottom')
        self.status_file = ttk.Label(status, text='未打开文件', style='Status.TLabel')
        self.status_file.pack(side='left')
        self.status_counts = ttk.Label(status, text='', style='Status.TLabel')
        self.status_counts.pack(side='right')
        self.progress = ttk.Progressbar(status, mode='determinate', length=220)
        self.progress.pack(side='right', padx=12)
        self.progress.pack_forget()

        self.root.bind('<Control-o>', lambda e: self.open_dialog())
        self.root.bind('<Control-e>', lambda e: self.export_dialog())
        self.root.bind('<Control-z>', lambda e: self.undo())
        self.root.bind('<Control-g>', lambda e: self.jump_time())
        self.root.bind('<Prior>', lambda e: self.step(-10))
        self.root.bind('<Next>', lambda e: self.step(10))

        try:
            if APP_ICON_PNG:
                self._icon = tk.PhotoImage(data=base64.b64encode(APP_ICON_PNG))
                self.root.iconphoto(True, self._icon)
        except Exception:
            pass

    # ------------------------------------------------------------- opening
    def open_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title='打开 VobSub 字幕（.idx 会自动找同名 .sub）',
            filetypes=[('VobSub 字幕', '*.idx *.sub'), ('所有文件', '*.*')])
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        if self._busy:
            return
        self._load_gen += 1
        self.load_cancel.clear()
        self._busy = True
        self._set_controls_enabled(False)
        self.items = []
        self.photos.clear()
        self.tree.delete(*self.tree.get_children())
        self.undo_stack.clear()
        self.v = None
        self.loaded = False
        self.current_index = None
        self.path = Path(path)
        self.filter_mode = '全部'
        self.filter_var.set('全部')
        self.warn_label.configure(text='')
        self._show_load_progress(Path(path).name)
        threading.Thread(target=self._load_worker, args=(Path(path), self._load_gen),
                         name='load-sub2sup', daemon=True).start()

    def _show_load_progress(self, name: str) -> None:
        if os.environ.get('SUB2SUP_HEADLESS'):
            self.load_progress = None
            self._load_pb = None
            self._load_info = None
            return
        top = tk.Toplevel(self.root)
        top.title('正在读取字幕')
        top.transient(self.root)
        top.resizable(False, False)
        top.configure(bg=BG)
        top.grab_set()
        top.geometry('460x150')
        ttk.Label(top, text=f'正在解析：{name}', style='TLabel').pack(anchor='w', padx=16, pady=(16, 4))
        pb = ttk.Progressbar(top, mode='determinate', maximum=100, length=420)
        pb.pack(padx=16, pady=4)
        info = ttk.Label(top, text='准备中…', style='Hint.TLabel')
        info.pack(anchor='w', padx=16)
        btns = ttk.Frame(top, padding=(16, 10))
        btns.pack(fill='x', side='bottom')

        def cancel() -> None:
            self.load_cancel.set()
            info.configure(text='正在取消…')

        ttk.Button(btns, text='取消', command=cancel).pack(side='right')
        top.protocol('WM_DELETE_WINDOW', cancel)
        self.load_progress = top
        self._load_pb = pb
        self._load_info = info

    def _load_worker(self, path: Path, gen: int) -> None:
        q = self.load_q
        try:
            v = vobsub.VobSub(str(path))
            track = min(self.track, len(v.tracks) - 1)
            q.put(('meta', gen, v, path, track))
            total = len(v.iter_entries(track))
            for i, sub in enumerate(v.decode_all(track)):
                if self.load_cancel.is_set():
                    q.put(('cancelled', gen))
                    return
                tw, th, thumb = render.thumb(sub.rows, sub.colors, 236, 38)
                info = ItemInfo(sub.index, sub.start, sub.end, sub.x, sub.y,
                                sub.width, sub.height, sub.colors,
                                render.png_bytes(tw, th, thumb), warnings=list(sub.warnings))
                q.put(('item', gen, info))
                if i % 8 == 0 or i == total - 1:
                    q.put(('progress', gen, i + 1, total))
            q.put(('done', gen))
        except Exception as exc:  # noqa: BLE001
            q.put(('error', gen, exc))

    def _poll_load(self) -> None:
        try:
            while True:
                msg = self.load_q.get_nowait()
                kind = msg[0]
                gen = msg[1] if len(msg) > 1 else None
                if gen is not None and gen != self._load_gen:
                    continue
                if kind == 'meta':
                    v, path, track = msg[2], msg[3], msg[4]
                    self.v = v
                    self.track = track
                    self.path = path
                    self.canvas_size = v.size
                    self.status_file.configure(text=self._short_path(v.idx_path or v.sub_path))
                    self.root.title(f'字幕转 SUP 工具  v{__version__}  —  {path.name}')
                    if v.warnings:
                        self.warn_label.configure(text='；'.join(v.warnings[:2]))
                    self._setup_tracks(v)
                elif kind == 'item':
                    if not self._busy:
                        continue
                    info = msg[2]
                    self.items.append(info)
                    self._insert_item(info)
                elif kind == 'progress':
                    done, total = msg[2], msg[3]
                    if self.load_progress is not None and self._load_pb is not None:
                        self._load_pb['value'] = done * 100 / max(1, total)
                        self._load_info.configure(text=f'{done} / {total} 段')
                elif kind == 'done':
                    self._finish_load()
                elif kind == 'cancelled':
                    self._finish_load(cancelled=True)
                elif kind == 'error':
                    self._finish_load(error=msg[2])
        except queue.Empty:
            pass
        self.root.after(40, self._poll_load)

    def _setup_tracks(self, v: 'vobsub.VobSub') -> None:
        if len(v.tracks) > 1:
            names = v.track_names()
            self.track_box.configure(values=names)
            self.track_var.set(names[min(self.track, len(names) - 1)])
            if not self.track_label.winfo_ismapped():
                self.track_label.pack(side='left', padx=(12, 0))
                self.track_box.pack(side='left')
        else:
            self.track_label.pack_forget()
            self.track_box.pack_forget()

    def _on_track_change(self, _event=None) -> None:
        names = list(self.track_box.cget('values'))
        try:
            idx = names.index(self.track_var.get())
        except ValueError:
            return
        if idx != self.track and self.path is not None:
            self.track = idx
            self.open_path(self.path)

    def _finish_load(self, error: Exception | None = None, cancelled: bool = False) -> None:
        if self.load_progress is not None:
            self.load_progress.grab_release()
            self.load_progress.destroy()
            self.load_progress = None
        self._busy = False
        self.loaded = bool(self.items) and error is None and not cancelled
        if error is not None:
            self.items = []
            self.v = None
            messagebox.showerror('打开失败', str(error), parent=self.root)
        elif cancelled:
            self.items = []
            self.v = None
            self.tree.delete(*self.tree.get_children())
        self._set_controls_enabled(self.loaded)
        if self.loaded:
            self._update_counts()
            if self.v.size is None:
                self._guess_canvas()
            self.filter_label.configure(text=f'共 {len(self.items)} 段')
            self._apply_filter()
            first = self._first_visible()
            if first is not None:
                self.tree.selection_set(str(first))
                self.tree.see(str(first))
                self.tree.focus(str(first))
            self.status_file.configure(text=self._short_path(self.v.idx_path or self.v.sub_path))
        else:
            self.status_file.configure(text='未打开文件')
            self.status_counts.configure(text='')

    def _guess_canvas(self) -> None:
        mx = max((it.x + it.w for it in self.items), default=1920)
        my = max((it.y + it.h for it in self.items), default=1080)
        if mx <= 720 and my <= 480:
            self.canvas_size = (720, 480)
        elif mx <= 1280 and my <= 720:
            self.canvas_size = (1280, 720)
        else:
            self.canvas_size = (1920, 1080)
        self.warn_label.configure(
            text='单 .sub 文件：调色板使用经典 VobSub 默认值，画布尺寸请按原片在导出时确认。')

    def _insert_item(self, info: ItemInfo) -> None:
        values = (f'{info.index:04d}',
                  vobsub.ticks_to_time(info.start), vobsub.ticks_to_time(info.end),
                  f'{info.duration / 90000:.2f}s', self._pos_text(info),
                  f'{info.w}×{info.h}')
        image = None
        if self.show_thumbs.get():
            image = self.photos.get(info.index)
            if image is None:
                try:
                    image = tk.PhotoImage(data=base64.b64encode(info.thumb).decode('ascii'))
                    self.photos[info.index] = image
                except tk.TclError:
                    image = None
        tags = ()
        if info.removed:
            tags = ('removed',)
        self.tree.insert('', 'end', iid=str(info.index), image=image,
                         text='', values=values, tags=tags)

    def _pos_text(self, it: ItemInfo, h_video: int | None = None) -> str:
        h_video = h_video or (self.canvas_size[1] if self.canvas_size else 1080)
        cy = it.y + it.h / 2
        if it.h > h_video * 0.55:
            zone = '整屏'
        elif cy < h_video * 0.33:
            zone = '顶部'
        elif cy > h_video * 0.67:
            zone = '底部'
        else:
            zone = '中部'
        return f'{zone} y={it.y}'

    # ------------------------------------------------------------- selection
    def on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self.current_index = idx
        self._update_buttons()
        self._render_preview(idx)

    def _selected_indices(self) -> list[int]:
        return [int(i) for i in self.tree.selection()]

    def _first_visible(self) -> int | None:
        kids = self.tree.get_children()
        return int(kids[0]) if kids else None

    def step(self, delta: int) -> None:
        kids = list(self.tree.get_children())
        if not kids:
            return
        cur = self.tree.selection()
        idx = 0
        if cur:
            try:
                idx = kids.index(cur[0])
            except ValueError:
                idx = 0
        nxt = max(0, min(len(kids) - 1, idx + delta))
        self.tree.selection_set(kids[nxt])
        self.tree.see(kids[nxt])
        self.tree.focus(kids[nxt])

    # ------------------------------------------------------------- preview
    def _on_preview_resize(self, event) -> None:
        if self.current_index is None:
            return
        if getattr(self, '_resize_job', None):
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(160, lambda: self._render_preview(self.current_index))

    def _render_preview(self, index: int) -> None:
        if not self.v or index >= len(self.items):
            return
        info = self.items[index]
        if not self.loaded:
            return
        width = max(480, self.preview_frame.winfo_width() - 8)
        self._preview_token += 1
        token = self._preview_token
        try:
            entry = self.v.iter_entries(self.track)[index]
            sub = self.v.decode(entry, self._next_start(index))
        except Exception as exc:  # noqa: BLE001
            self.info_label.configure(text=f'预览失败：{exc}')
            return
        if token != self._preview_token:
            return
        vw, vh = self.canvas_size or (1920, 1080)
        if self.preview_mode == 'crop':
            tw, th, buf = render.thumb(sub.rows, sub.colors, width, 430)
        else:
            tw, th, buf = render.preview(sub.rows, sub.colors, vw, vh, sub.x, sub.y, width)
        try:
            photo = tk.PhotoImage(data=base64.b64encode(render.png_bytes(tw, th, buf)).decode('ascii'))
        except tk.TclError:
            return
        self._preview_photo = photo
        self.preview_label.configure(image=photo, text='')
        self.info_label.configure(
            text=f'#{index:04d}    {vobsub.ticks_to_time(info.start)} → {vobsub.ticks_to_time(info.end)}'
                 f'    {info.duration / 90000:.3f} 秒    位置 ({info.x}, {info.y})    {info.w}×{info.h}')
        warn = '；'.join(info.warnings)
        self.warn_label.configure(text=warn)
        removed = info.removed
        self.btn_toggle.configure(text='恢复此段 (Space)' if removed else '删除此段 (Space)')
        self.btn_zoom.configure(text='整帧预览' if self.preview_mode == 'crop' else '细节 1:1')

    def _next_start(self, index: int) -> int | None:
        entries = self.v.iter_entries(self.track)
        return entries[index + 1].start if index + 1 < len(entries) else None

    def toggle_zoom(self) -> None:
        self.preview_mode = 'crop' if self.preview_mode == 'frame' else 'frame'
        if self.current_index is not None:
            self._render_preview(self.current_index)

    # ------------------------------------------------------------- deletion
    def toggle_selected(self) -> None:
        self._set_removed(self._selected_indices(), None)

    def delete_selected(self) -> None:
        self._set_removed(self._selected_indices(), True)

    def restore_selected(self) -> None:
        self._set_removed(self._selected_indices(), False)

    def _set_removed(self, indices: list[int], value: bool | None) -> None:
        if not indices or not self.loaded:
            return
        changed = []
        for i in indices:
            info = self.items[i]
            new = (not info.removed) if value is None else value
            if info.removed != new:
                info.removed = new
                changed.append(i)
                tags = ('removed',) if new else ()
                try:
                    self.tree.item(str(i), tags=tags)
                except tk.TclError:
                    pass
        if changed and value is not False:
            self.undo_stack.append(changed)
            if len(self.undo_stack) > 200:
                self.undo_stack.pop(0)
        self._update_counts()
        self._update_buttons()
        if self.current_index is not None:
            self._render_preview(self.current_index)

    def restore_all(self) -> None:
        self._set_removed([i for i, it in enumerate(self.items) if it.removed], False)
        self.undo_stack.clear()
        self._update_counts()

    def undo(self) -> None:
        if not self.undo_stack:
            return
        batch = self.undo_stack.pop()
        self._set_removed(batch, False)
        self._update_counts()

    def _update_counts(self) -> None:
        total = len(self.items)
        removed = sum(1 for it in self.items if it.removed)
        kept = total - removed
        if total:
            self.status_counts.configure(text=f'共 {total} 段   保留 {kept} 段   已删除 {removed} 段')
            self.filter_label.configure(text=f'显示 {len(self.tree.get_children())} / {total} 段')
        else:
            self.status_counts.configure(text='')

    def _update_buttons(self) -> None:
        has = bool(self.items)
        sel = bool(self.tree.selection())
        any_removed = any(it.removed for it in self.items)
        self.btn_del.configure(state='normal' if has and sel else 'disabled')
        self.btn_restore.configure(state='normal' if has and any_removed else 'disabled')
        self.btn_undo.configure(state='normal' if self.undo_stack else 'disabled')
        self.btn_export.configure(state='normal' if has and sum(1 for it in self.items if not it.removed) else 'disabled')
        self.btn_toggle.configure(state='normal' if has and self.current_index is not None else 'disabled')
        self.btn_prev.configure(state='normal' if has else 'disabled')
        self.btn_next.configure(state='normal' if has else 'disabled')
        self.btn_zoom.configure(state='normal' if has and self.current_index is not None else 'disabled')

    def _on_double(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self._set_removed([int(row)], None)

    # ------------------------------------------------------------- filtering
    def set_filter(self, mode: str) -> None:
        self.filter_mode = mode
        self.filter_var.set(mode)
        self._apply_filter()

    def _match(self, info: ItemInfo) -> bool:
        mode = self.filter_mode
        if mode == '未删除':
            return not info.removed
        if mode == '已删除':
            return info.removed
        if mode == '仅顶部':
            vh = self.canvas_size[1] if self.canvas_size else 1080
            return (info.y + info.h / 2) < vh * 0.36
        if mode == '仅整屏':
            vh = self.canvas_size[1] if self.canvas_size else 1080
            return info.h > vh * 0.5
        return True

    def _apply_filter(self) -> None:
        if not self.loaded:
            return
        self.tree.delete(*self.tree.get_children())
        for info in self.items:
            if self._match(info):
                self._insert_item(info)
        self._update_counts()
        self._update_buttons()
        kids = self.tree.get_children()
        if kids:
            if self.current_index is not None and str(self.current_index) in kids:
                self.tree.selection_set(str(self.current_index))
            else:
                self.tree.selection_set(kids[0])
            self.tree.see(self.tree.selection()[0])
        self._update_buttons()

    # ------------------------------------------------------------- navigation
    def jump_time(self) -> None:
        if not self.loaded:
            return
        top = tk.Toplevel(self.root)
        top.title('跳转到时间')
        top.transient(self.root)
        top.grab_set()
        top.configure(bg=BG)
        top.geometry('380x150')
        ttk.Label(top, text='输入时间（如 00:03:26.5 或 206.5 秒）：').pack(anchor='w', padx=16, pady=(16, 6))
        var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=var, width=24)
        ent.pack(padx=16, anchor='w')
        ent.focus_set()

        def go() -> None:
            try:
                ticks = vobsub.parse_time(var.get())
            except Exception:
                messagebox.showerror('时间格式错误', '请输入 mm:ss 或 hh:mm:ss.mmm', parent=top)
                return
            top.destroy()
            best = min(range(len(self.items)), key=lambda i: abs(self.items[i].start - ticks))
            if str(best) not in self.tree.get_children():
                self.filter_var.set('全部')
                self.set_filter('全部')
            self.tree.selection_set(str(best))
            self.tree.see(str(best))
            self.tree.focus(str(best))

        ent.bind('<Return>', lambda e: go())
        ttk.Button(top, text='跳转', command=go).pack(anchor='e', padx=16, pady=10)

    # ------------------------------------------------------------- export
    def export_dialog(self) -> None:
        if not self.loaded:
            return
        kept = [it for it in self.items if not it.removed]
        if not kept:
            messagebox.showwarning('没有可导出的段落', '所有段落都被删除了。', parent=self.root)
            return
        dlg = tk.Toplevel(self.root)
        dlg.title('导出为 Blu-ray SUP (PGS)')
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.protocol('WM_DELETE_WINDOW', lambda: (state['cancel'].set(), dlg.destroy()))
        ttk.Label(dlg, text=f'将导出 {len(kept)} 段（已删除 {len(self.items) - len(kept)} 段）',
                  style='Info.TLabel').pack(anchor='w', padx=18, pady=(18, 8))

        form = ttk.Frame(dlg)
        form.pack(fill='x', padx=18)
        ttk.Label(form, text='输出文件：').grid(row=0, column=0, sticky='w', pady=4)
        out_default = self._default_output()
        out_var = tk.StringVar(value=str(out_default))
        ttk.Entry(form, textvariable=out_var, width=56).grid(row=0, column=1, sticky='we', pady=4)
        ttk.Button(form, text='浏览…', command=lambda: self._browse_out(out_var)).grid(
            row=0, column=2, padx=(6, 0))

        ttk.Label(form, text='画布尺寸：').grid(row=1, column=0, sticky='w', pady=4)
        canvas_var = tk.StringVar(value=f'{self.canvas_size[0]}x{self.canvas_size[1]}'
                                if self.canvas_size else '1920x1080')
        if self.v.idx is not None:
            cb = ttk.Combobox(form, textvariable=canvas_var, width=14, state='readonly',
                              values=[f'{self.canvas_size[0]}x{self.canvas_size[1]}'])
        else:
            cb = ttk.Combobox(form, textvariable=canvas_var, width=14, state='readonly',
                              values=['1920x1080', '1280x720', '720x480'])
        cb.grid(row=1, column=1, sticky='w', pady=4)

        ttk.Label(form, text='帧率代码：').grid(row=2, column=0, sticky='w', pady=4)
        fps_labels = [lab for lab, _ in pgs.FPS_LABELS]
        fps_var = tk.StringVar(value=fps_labels[0])
        ttk.Combobox(form, textvariable=fps_var, width=28, state='readonly',
                     values=fps_labels).grid(row=2, column=1, sticky='w', pady=4)

        ttk.Label(form, text='时间偏移 (ms)：').grid(row=3, column=0, sticky='w', pady=4)
        offset_var = tk.StringVar(value='0')
        ttk.Entry(form, textvariable=offset_var, width=12).grid(row=3, column=1, sticky='w', pady=4)

        ttk.Label(form, text='提示：输出为 1920×1080 时按 BT.709 转换调色板，颜色误差 ≤1/255；'
                             '位图与时间轴完全保留。',
                  style='Hint.TLabel', wraplength=560).grid(row=4, column=0, columnspan=3,
                                                            sticky='w', pady=(10, 0))
        pb = ttk.Progressbar(dlg, mode='determinate', maximum=100, length=560)
        pb.pack(fill='x', padx=18, pady=(14, 4))
        info = ttk.Label(dlg, text='', style='Hint.TLabel')
        info.pack(anchor='w', padx=18)

        btns = ttk.Frame(dlg, padding=(18, 12))
        btns.pack(fill='x', side='bottom')
        state = {'running': False, 'cancel': threading.Event()}

        def close() -> None:
            state['cancel'].set()
            dlg.destroy()

        def start() -> None:
            if state['running']:
                return
            try:
                out = Path(out_var.get()).expanduser()
                if not str(out):
                    raise ValueError('输出路径为空')
                w, h = canvas_var.get().lower().split('x')
                canvas = (int(w), int(h))
                offset = int(float(offset_var.get() or '0'))
                fps = dict(pgs.FPS_LABELS)[fps_var.get()]
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror('参数错误', str(exc), parent=dlg)
                return
            if out.exists():
                if not messagebox.askyesno('覆盖文件', f'文件已存在：\\n{out}\\n\\n是否覆盖？', parent=dlg):
                    return
            state['running'] = True
            run_btn.configure(state='disabled')
            start_btn.configure(state='disabled')
            q = queue.Queue()

            def work() -> None:
                try:
                    v = vobsub.VobSub(str(self.path))
                    entries = v.iter_entries(self.track)
                    indices = [it.index for it in self.items if not it.removed]
                    pgs_items = []
                    for n, i in enumerate(indices):
                        if state['cancel'].is_set():
                            q.put(('cancel',))
                            return
                        nxt = entries[i + 1].start if i + 1 < len(entries) else None
                        sub = v.decode(entries[i], nxt)
                        start_t = max(0, sub.start + offset * 90)
                        end_t = max(start_t + 1, sub.end + offset * 90)
                        pgs_items.append(pgs.PgsItem(start_t, end_t, sub.x, sub.y,
                                                     sub.width, sub.height, sub.rows, sub.colors))
                        q.put(('decode', n + 1, len(indices)))
                    stats = pgs.write_sup(pgs_items, str(out), canvas[0], canvas[1], fps,
                                          progress=lambda a, b: q.put(('write', a, b)))
                    q.put(('done', out, stats, len(indices)))
                except Exception as exc:  # noqa: BLE001
                    q.put(('error', exc))

            def poll() -> None:
                try:
                    while True:
                        msg = q.get_nowait()
                        if msg[0] == 'decode':
                            pb['value'] = msg[1] / max(1, msg[2]) * 60
                            info.configure(text=f'解码 {msg[1]}/{msg[2]} 段')
                        elif msg[0] == 'write':
                            pb['value'] = 60 + msg[1] / max(1, msg[2]) * 40
                            info.configure(text=f'写入 {msg[1]}/{msg[2]} 段')
                        elif msg[0] == 'done':
                            out_path, stats, count = msg[1], msg[2], msg[3]
                            pb['value'] = 100
                            info.configure(text='完成')
                            dlg.grab_release()
                            dlg.destroy()
                            self._export_done(out_path, count, stats)
                            return
                        elif msg[0] == 'cancel':
                            dlg.grab_release()
                            dlg.destroy()
                            return
                        elif msg[0] == 'error':
                            state['running'] = False
                            run_btn.configure(state='normal')
                            start_btn.configure(state='normal')
                            messagebox.showerror('导出失败', str(msg[1]), parent=dlg)
                            return
                except queue.Empty:
                    pass
                try:
                    if dlg.winfo_exists():
                        dlg.after(60, poll)
                except tk.TclError:
                    pass

            threading.Thread(target=work, daemon=True).start()
            dlg.after(60, poll)

        start_btn = ttk.Button(btns, text='开始导出', command=start)
        start_btn.pack(side='right')
        run_btn = start_btn
        ttk.Button(btns, text='取消', command=close).pack(side='right', padx=(0, 8))

    def _browse_out(self, var: tk.StringVar) -> None:
        path = filedialog.asksaveasfilename(
            title='保存 SUP 文件', defaultextension='.sup',
            filetypes=[('Blu-ray 字幕', '*.sup')],
            initialfile=Path(var.get()).name if var.get() else 'output.sup')
        if path:
            var.set(path)

    def _default_output(self) -> Path:
        src = Path(self.v.idx_path or self.v.sub_path or 'output')
        out = src.with_suffix('.sup')
        n = 1
        while out.exists():
            out = src.with_name(f'{src.stem}_{n}.sup')
            n += 1
        return out

    def _export_done(self, out: Path, count: int, stats: dict) -> None:
        size = out.stat().st_size if out.exists() else 0
        try:
            segs = pgs.scan_sup(str(out))
            n_pcs = sum(1 for s in segs if s['type'] == pgs.SEG_PCS)
            check = f'结构自检通过：{len(segs)} 个段 / {n_pcs} 个显示集'
        except Exception as exc:  # noqa: BLE001
            check = f'结构自检异常：{exc}'
        msg = (f'导出成功！\n\n{out}\n\n'
               f'{count} 段，{size / 1048576:.2f} MB\n{check}')
        if messagebox.askyesno('导出成功', msg + '\n\n是否打开所在文件夹？', parent=self.root):
            os.startfile(str(out.parent))  # noqa: S606

    # ------------------------------------------------------------- misc
    def open_folder(self) -> None:
        if self.v is not None:
            os.startfile(str((self.v.idx_path or self.v.sub_path).parent))  # noqa: S606

    def _open_help(self) -> None:
        roots = []
        if getattr(sys, 'frozen', False):
            roots.append(Path(sys.executable).parent)
        else:
            roots.append(Path(__file__).resolve().parents[2])
        roots.append(Path.cwd())
        for base in roots:
            for name in ('使用说明.txt', 'README.md'):
                p = base / name
                if p.exists():
                    os.startfile(str(p))  # noqa: S606
                    return
        self._show_help_window()

    def _show_help_window(self) -> None:
        top = tk.Toplevel(self.root)
        top.title('使用说明')
        top.geometry('780x640')
        top.transient(self.root)
        top.configure(bg=BG)
        frame = ttk.Frame(top)
        frame.pack(fill='both', expand=True)
        txt = tk.Text(frame, wrap='word', font=FONT, padx=14, pady=12,
                      borderwidth=0, background='#ffffff')
        sb = ttk.Scrollbar(frame, orient='vertical', command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        txt.insert('1.0', HELP_TEXT)
        txt.configure(state='disabled')
        ttk.Button(top, text='关闭', command=top.destroy).pack(pady=8, anchor='e', padx=14)

    def _about(self) -> None:
        messagebox.showinfo(
            '关于',
            f'字幕转 SUP 工具 v{__version__}\n\n'
            'VobSub (idx/sub) → Blu-ray PGS (.sup) 无损转换工具\n'
            '支持按段删除字幕（例如字幕组信息），再导出。\n\n'
            '位图: 1:1 重编码（不重新渲染）\n'
            '时间: 保持原始 90 kHz 时间戳\n'
            '颜色: 调色板转 YCbCr，误差 ≤1/255\n\n'
            '可直接用于 tsMuxeR / mkvmerge 等封装。',
            parent=self.root)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = 'normal' if enabled else 'disabled'
        for w in (self.btn_del, self.btn_restore, self.btn_prev, self.btn_next,
                  self.btn_toggle, self.btn_zoom, self.btn_jump):
            w.configure(state=state if enabled else 'disabled')
        self.btn_open.configure(state='normal')
        self.btn_export.configure(state='normal' if enabled else 'disabled')
        self.btn_undo.configure(state='disabled')
        self.filter_box.configure(state='readonly' if enabled else 'disabled')

    def _short_path(self, path: Path) -> str:
        s = str(path)
        return s if len(s) <= 100 else '…' + s[-97:]

    def _on_close(self) -> None:
        self.load_cancel.set()
        self.root.destroy()


def main() -> None:
    _dpi_aware()
    root = tk.Tk()
    app = App(root)
    files = [a for a in sys.argv[1:] if not a.startswith('-')]
    for a in files:
        p = Path(a)
        if p.suffix.lower() in ('.idx', '.sub') and p.exists():
            root.after(250, lambda p=p: app.open_path(p))
            break
    root.mainloop()


# ---------------------------------------------------------------- smoke test
def smoke_test(path: str) -> int:
    """Headless-ish end-to-end check: load, export first N segments, verify."""
    os.environ['SUB2SUP_HEADLESS'] = '1'
    _dpi_aware()
    root = tk.Tk()
    root.withdraw()
    app = App(root)
    app.open_path(Path(path))
    deadline = time.time() + 180
    while not app.loaded and time.time() < deadline:
        root.update()
        if app._busy is False and not app.items:
            break
        time.sleep(0.02)
    root.update()
    if not app.items:
        print('SMOKE FAIL: no items loaded')
        return 2
    print(f'SMOKE: loaded {len(app.items)} items, canvas={app.canvas_size}')
    # delete first item then export a few
    app._set_removed([0], True)
    kept = [it.index for it in app.items if not it.removed][:6]
    out = Path(tempfile.gettempdir()) / 'sub2sup_smoke.sup'
    if out.exists():
        out.unlink()
    q = queue.Queue()
    v = vobsub.VobSub(str(path))
    entries = v.iter_entries(0)

    def work() -> None:
        items = []
        for i in kept:
            sub = v.decode(entries[i], entries[i + 1].start if i + 1 < len(entries) else None)
            items.append(pgs.PgsItem(sub.start, sub.end, sub.x, sub.y, sub.width,
                                     sub.height, sub.rows, sub.colors))
        stats = pgs.write_sup(items, str(out), app.canvas_size[0], app.canvas_size[1], 23.976)
        q.put(stats)

    t = threading.Thread(target=work)
    t.start()
    while t.is_alive():
        root.update()
        time.sleep(0.01)
    stats = q.get()
    segs = pgs.scan_sup(str(out))
    print(f'SMOKE: exported {stats}, {len(segs)} segments, {out.stat().st_size} bytes')
    try:
        out.unlink()
    except OSError:
        pass
    app._on_close()
    return 0


if __name__ == '__main__':
    main()
