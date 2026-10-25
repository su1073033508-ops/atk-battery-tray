"""界面：托盘面板（PIL 抗锯齿绘制）+ 设置窗口（tkinter）。"""
import ctypes
import ctypes.wintypes
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from PIL import ImageTk

import render
from hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, format_spec, parse

BG = "#141619"
CARD = "#202329"
CARD_HI = "#2b2f37"
LINE = "#3a3f48"
TXT = "#e8ecf3"
SUB = "#97a0ad"
ACC = "#3a82f7"

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.GetAncestor.restype = ctypes.c_void_p
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]


def _mods_now(state=0):
    """当前按住的修饰键，返回 hotkey 里的 MOD_* 位。

    Windows 下 Tk 的 event.state 里 Alt 是 0x20000（不是 Mod1），而且不带 Win 键，
    所以再问一遍 GetAsyncKeyState，多键组合和 Alt/Win 才录得准。
    """
    mods = 0
    if state & 0x4:
        mods |= MOD_CONTROL
    if state & 0x1:
        mods |= MOD_SHIFT
    if state & 0x8:
        mods |= MOD_ALT
    if state & 0x20000:
        mods |= MOD_ALT
    try:
        if user32.GetAsyncKeyState(0x11) & 0x8000:
            mods |= MOD_CONTROL
        if user32.GetAsyncKeyState(0x12) & 0x8000:
            mods |= MOD_ALT
        if user32.GetAsyncKeyState(0x10) & 0x8000:
            mods |= MOD_SHIFT
        if (user32.GetAsyncKeyState(0x5B) | user32.GetAsyncKeyState(0x5C)) & 0x8000:
            mods |= MOD_WIN
    except Exception:
        pass
    return mods


def _pick_font(root):
    families = set(tkfont.families(root))
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI"):
        if name in families:
            return name
    return "TkDefaultFont"



def screen_scale():
    """界面缩放：优先系统 DPI，取不到就用 物理/逻辑 分辨率比值兜底。"""
    try:
        dpi = user32.GetDpiForSystem()
    except Exception:
        dpi = 96
    s = dpi / 96.0
    try:
        hdc = user32.GetDC(0)
        phys = gdi32.GetDeviceCaps(hdc, 118)  # DESKTOPHORZRES
        user32.ReleaseDC(0, hdc)
        logical = user32.GetSystemMetrics(0)
        if phys and logical:
            s = max(s, phys / float(logical))
    except Exception:
        pass
    return max(1.0, s)


def work_area():
    """工作区（屏幕减去任务栏）；取不到返回 None。"""
    try:
        r = ctypes.wintypes.RECT()
        if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return None
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def anchor_for_rect(rect, w, h, gap, sw, sh):
    """按托盘图标矩形算弹框左上角：贴着图标居中，不跟鼠标走（否则每次偏差几像素）。

    任务栏在哪条边，就把框放到那条边的内侧；侧边栏则左右贴靠。
    任务栏自动隐藏时工作区等于整屏，退回「图标在上半还是下半」判断。
    """
    try:
        l, t, r, b = [int(v) for v in rect]
    except Exception:
        return None
    cx, cy = (l + r) // 2, (t + b) // 2
    wa = work_area()
    if wa:
        wl, wt, wr, wb = wa
    else:
        wl, wt, wr, wb = 0, 0, sw, sh
    if wb < sh - 2:
        edge = "bottom"
    elif wt > 2:
        edge = "top"
    elif wl > 2:
        edge = "left"
    elif wr < sw - 2:
        edge = "right"
    else:
        edge = "bottom" if cy >= sh // 2 else "top"
    if edge == "bottom":
        x, y = cx - w // 2, t - h - gap
    elif edge == "top":
        x, y = cx - w // 2, b + gap
    elif edge == "left":
        x, y = r + gap, cy - h // 2
    else:
        x, y = l - w - gap, cy - h // 2
    x = max(wl + 4, min(x, wr - w - 4))
    y = max(wt + 4, min(y, wb - h - 4))
    return x, y


def round_region(win, w, h, r):
    try:
        hwnd = user32.GetAncestor(int(win.winfo_id()), 2)
        if not hwnd:
            hwnd = int(win.winfo_id())
        rgn = gdi32.CreateRoundRectRgn(0, 0, int(w), int(h), int(r * 2), int(r * 2))
        user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
    except Exception:
        pass


class Panel:
    """托盘面板：整块用 PIL 画好再显示，边缘和文字都是抗锯齿的。"""

    DW, DH = 328, 264
    PAD = 16

    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.win = None
        self.lbl = None
        self.photo = None
        self.ds = 1.0
        self._menu_rects = []
        self._menu_labels = []
        self._hover = None
        self._toggle_rect = None
        self._refresh_rect = None
        self._hover_refresh = False

    # ---------- 显示 ----------
    def show(self):
        if self.win and self.win.winfo_exists():
            self.hide()
            return
        try:
            self.ds = screen_scale()
        except Exception:
            self.ds = 1.0
        if __import__('os').environ.get('EZB_DEBUG') == '1':
            import sys; print(f'[panel] ds={self.ds}', file=sys.stderr, flush=True)
        win = tk.Toplevel(self.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=CARD)
        self.lbl = tk.Label(win, bd=0, bg=CARD, highlightthickness=0)
        self.lbl.pack()
        self._redraw()

        w, h = int(self.DW * self.ds), int(self.DH * self.ds)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        rect = None
        try:
            rect = self.app.tray.icon_rect()
        except Exception:
            rect = None
        xy = anchor_for_rect(rect, w, h, int(round(10 * self.ds)), sw, sh) if rect else None
        if xy:
            x, y = xy
        else:
            try:
                px, py = self.root.winfo_pointerxy()
            except Exception:
                px, py = 200, 200
            x = min(max(px - w + int(70 * self.ds), 6), sw - w - 6)
            y = max(py - h - int(18 * self.ds), 6)
        try:
            rect = ctypes.wintypes.RECT()
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
            wa_bottom, wa_right = rect.bottom, rect.right
        except Exception:
            wa_bottom, wa_right = sh, sw
        y = min(y, wa_bottom - h - 8)
        x = min(x, wa_right - w - 8)
        win.geometry(f"{w}x{h}+{x}+{y}")

        self.lbl.bind("<Motion>", self._on_motion)
        self.lbl.bind("<Leave>", self._on_leave)
        self.lbl.bind("<Button-1>", self._on_click)
        win.bind("<FocusOut>", self._on_focus_out)
        win.bind("<Escape>", lambda e: self.hide())

        def finish():
            round_region(win, w, h, 14 * self.ds)
            try:
                win.focus_force()
            except Exception:
                pass
        win.after(20, finish)

    def _on_focus_out(self, _e=None):
        if self.app.settings_open():
            return
        self.root.after(150, self._maybe_hide)

    def _maybe_hide(self):
        if self.app.settings_open():
            return
        try:
            focused = self.root.focus_get()
        except Exception:
            focused = None
        if focused is None:
            self.hide()

    def hide(self):
        if self.win and self.win.winfo_exists():
            self.win.destroy()
        self.win = None
        self.lbl = None

    # ---------- 绘制 ----------
    def _menu_items(self):
        items = [("设置…", self.app.open_settings), ("退出", self.app.quit)]
        for label, cb in getattr(self.app.plugins, "menu_items", []):
            items.insert(1, (label, cb))
        return items

    def _redraw(self):
        if not self.lbl or not self.win or not self.win.winfo_exists():
            return
        items = self._menu_items()
        self._menu_labels = [i[0] for i in items]
        img, rects, geo = render.compose(
            self.app.battery_snapshot(), self.app.keyswap.enabled, self._hover,
            self._menu_labels, self.DW, self.DH, scale=self.ds,
            hover_refresh=self._hover_refresh,
            good=self.app.config.get("good_level", 80),
            warn=self.app.config.get("warn_level", 20))
        self.photo = ImageTk.PhotoImage(img)
        self.lbl.configure(image=self.photo)
        self._menu_rects = [(r[0], r[1], r[2], r[3]) for r in rects]
        self._toggle_rect = geo["toggle"]
        self._refresh_rect = geo["refresh"]

    def update(self):
        self._redraw()

    # ---------- 交互 ----------
    def _to_design(self, evt):
        return evt.x / self.ds, evt.y / self.ds

    def _hit(self, x, y):
        if self._refresh_rect:
            x1, y1, x2, y2 = self._refresh_rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                return "refresh", None
        if self._toggle_rect:
            x1, y1, x2, y2 = self._toggle_rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                return "toggle", None
        for i, (x1, y1, x2, y2) in enumerate(self._menu_rects):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return "menu", i
        return None, None

    def _pointer_design(self):
        """鼠标在面板里的设计坐标；窗口没开时返回 (-1, -1)。"""
        try:
            px, py = self.win.winfo_pointerxy()
            return ((px - self.win.winfo_rootx()) / self.ds,
                    (py - self.win.winfo_rooty()) / self.ds)
        except Exception:
            return -1, -1

    def _on_motion(self, evt):
        x, y = self._to_design(evt)
        kind, idx = self._hit(x, y)
        label = self._menu_labels[idx] if kind == "menu" else None
        hov_btn = kind == "refresh"
        if label != self._hover or hov_btn != self._hover_refresh:
            self._hover = label
            self._hover_refresh = hov_btn
            self._redraw()

    def _on_leave(self, _evt):
        if self._hover is not None or self._hover_refresh:
            self._hover = None
            self._hover_refresh = False
            self._redraw()

    def _on_click(self, evt):
        x, y = self._to_design(evt)
        kind, idx = self._hit(x, y)
        if kind == "refresh":
            self.app.refresh_battery()
            self._hover_refresh = True
            self._redraw()
            # 按下去亮一下，之后按鼠标是否还在按钮上恢复
            def restore():
                if not (self.win and self.win.winfo_exists()):
                    return
                px, py = self._pointer_design()
                k, _i = self._hit(px, py)
                self._hover_refresh = (k == "refresh")
                self._redraw()
            self.root.after(350, restore)
        elif kind == "toggle":
            self.app.on_swap_changed(not self.app.keyswap.enabled)
            self._redraw()
        elif kind == "menu":
            items = self._menu_items()
            if idx < len(items):
                cb = items[idx][1]
                if cb is self.app.quit:
                    self.hide()
                cb()


class Settings:
    """设置窗口：热键、开机自启、刷新间隔。"""

    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.font = _pick_font(root)
        self.win = None
        self._recording = False
        self._pending = None
        self._pending_spec = None

    def _f(self, size, bold=False):
        return tkfont.Font(family=self.font, size=size, weight="bold" if bold else "normal")

    def open(self):
        if self.win and self.win.winfo_exists():
            self.win.lift()
            return
        if __import__('os').environ.get('EZB_DEBUG') == '1':
            import sys; print(f'[panel] ds={self.ds}', file=sys.stderr, flush=True)
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("设备电量 · 设置")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)

        wrap = tk.Frame(win, bg=BG, padx=20, pady=16)
        wrap.pack(fill="both", expand=True)

        def label(text, size=10, color=TXT, bold=False, pady=(0, 0)):
            tk.Label(wrap, text=text, font=self._f(size, bold), bg=BG, fg=color,
                     anchor="w").pack(fill="x", pady=pady)

        label("快捷键", 11, TXT, True)
        label("按一下就能切换「WASD / 方向键」", 9, SUB, pady=(2, 8))

        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x")
        self.hk_var = tk.StringVar(value=self._current_text())
        self.hk_entry = tk.Entry(row, textvariable=self.hk_var, font=self._f(11), width=16,
                                 bg=CARD_HI, fg=TXT, insertbackground=TXT, bd=0,
                                 readonlybackground=CARD_HI, justify="center")
        self.hk_entry.pack(side="left", ipady=6)
        self.rec_btn = tk.Button(row, text="录制", font=self._f(9), bg=ACC, fg="white", bd=0,
                                 activebackground=ACC, padx=14, pady=6, cursor="hand2",
                                 command=self._start_record)
        self.rec_btn.pack(side="left", padx=6)
        tk.Button(row, text="清除", font=self._f(9), bg=CARD_HI, fg=TXT, bd=0,
                  activebackground=CARD_HI, padx=14, pady=6, cursor="hand2",
                  command=self._clear_hotkey).pack(side="left")

        self.hk_on = tk.BooleanVar(value=bool(self.app.config.get("hotkey_enabled", True)))
        tk.Checkbutton(wrap, text="启用快捷键", variable=self.hk_on, font=self._f(9),
                       bg=BG, fg=TXT, selectcolor=CARD_HI, activebackground=BG,
                       activeforeground=TXT, anchor="w").pack(fill="x", pady=(10, 0))

        tk.Frame(wrap, bg=LINE, height=1).pack(fill="x", pady=14)

        self.auto_var = tk.BooleanVar(value=bool(self.app.config.get("autostart", False)))
        tk.Checkbutton(wrap, text="开机自动启动", variable=self.auto_var, font=self._f(10),
                       bg=BG, fg=TXT, selectcolor=CARD_HI, activebackground=BG,
                       activeforeground=TXT, anchor="w").pack(fill="x")

        tk.Frame(wrap, bg=LINE, height=1).pack(fill="x", pady=14)

        r2 = tk.Frame(wrap, bg=BG)
        r2.pack(fill="x")
        tk.Label(r2, text="电量刷新间隔（秒）", font=self._f(10), bg=BG, fg=TXT,
                 anchor="w").pack(side="left")
        self.refresh_var = tk.StringVar(value=str(self.app.config.get("refresh_seconds", 60)))
        tk.Spinbox(r2, from_=15, to=600, increment=15, width=6, textvariable=self.refresh_var,
                   font=self._f(10), bg=CARD_HI, fg=TXT, bd=0, justify="center",
                   buttonbackground=CARD_HI).pack(side="right", ipady=3)

        r3 = tk.Frame(wrap, bg=BG)
        r3.pack(fill="x", pady=(12, 0))
        tk.Label(r3, text="悬停多久显示提示（秒）", font=self._f(10), bg=BG, fg=TXT,
                 anchor="w").pack(side="left")
        self.hover_var = tk.StringVar(value=str(self.app.config.get("hover_delay", 0.35)))
        tk.Spinbox(r3, from_=0, to=3, increment=0.1, width=6, textvariable=self.hover_var,
                   font=self._f(10), bg=CARD_HI, fg=TXT, bd=0, justify="center",
                   buttonbackground=CARD_HI).pack(side="right", ipady=3)

        tk.Frame(wrap, bg=LINE, height=1).pack(fill="x", pady=14)
        tk.Label(wrap, text="电量颜色区间（只改「起」，终点自动衔接）", font=self._f(10),
                 bg=BG, fg=SUB, anchor="w").pack(fill="x")

        self.good_var = tk.StringVar(value=str(self.app.config.get("good_level", 80)))
        self.warn_var = tk.StringVar(value=str(self.app.config.get("warn_level", 20)))

        def _nums():
            try:
                good = max(1, min(100, int(self.good_var.get())))
            except ValueError:
                good = 80
            try:
                warn = max(0, min(99, int(self.warn_var.get())))
            except ValueError:
                warn = 20
            if warn >= good:
                warn = max(0, good - 1)
            return good, warn

        def _sync_ranges(*_):
            good, warn = _nums()
            self.good_to_lbl.configure(text="100%")
            self.warn_to_lbl.configure(text=f"{good - 1}%")
            self.red_to_lbl.configure(text=(f"{warn - 1}%" if warn > 0 else "—"))

        def _range_row(name, fg_color, var, lo, hi, end_attr):
            row = tk.Frame(wrap, bg=BG)
            row.pack(fill="x", pady=(8, 0))
            tk.Label(row, text=name, font=self._f(10), bg=BG, fg=fg_color,
                     anchor="w").pack(side="left")
            lbl = tk.Label(row, text="", font=self._f(10), bg=BG, fg=TXT)
            lbl.pack(side="right")
            tk.Label(row, text="到", font=self._f(10), bg=BG, fg=SUB).pack(side="right", padx=4)
            tk.Spinbox(row, from_=lo, to=hi, increment=1, width=4, textvariable=var,
                       command=_sync_ranges, font=self._f(10), bg=CARD_HI, fg=TXT, bd=0,
                       justify="center", buttonbackground=CARD_HI).pack(side="right", ipady=3)
            tk.Label(row, text="起", font=self._f(10), bg=BG, fg=SUB).pack(side="right")
            setattr(self, end_attr, lbl)

        _range_row("绿色", "#4ade80", self.good_var, 1, 100, "good_to_lbl")
        _range_row("黄色", "#fabe46", self.warn_var, 0, 99, "warn_to_lbl")

        # 红色没有「起」可改：它接在黄色下面，从 0 到 黄起-1
        red_row = tk.Frame(wrap, bg=BG)
        red_row.pack(fill="x", pady=(8, 0))
        tk.Label(red_row, text="红色", font=self._f(10), bg=BG, fg="#f87171",
                 anchor="w").pack(side="left")
        self.red_to_lbl = tk.Label(red_row, text="", font=self._f(10), bg=BG, fg=TXT)
        self.red_to_lbl.pack(side="right")
        tk.Label(red_row, text="到", font=self._f(10), bg=BG, fg=SUB).pack(side="right", padx=4)
        tk.Label(red_row, text="从 0", font=self._f(10), bg=BG, fg=SUB).pack(side="right")

        self.good_var.trace_add("write", _sync_ranges)
        self.warn_var.trace_add("write", _sync_ranges)
        _sync_ranges()

        btn_row = tk.Frame(wrap, bg=BG)
        btn_row.pack(fill="x", pady=(18, 0))
        tk.Button(btn_row, text="保存", font=self._f(10), bg=ACC, fg="white", bd=0,
                  activebackground=ACC, padx=22, pady=7, cursor="hand2",
                  command=self._save).pack(side="right")
        tk.Button(btn_row, text="取消", font=self._f(10), bg=CARD_HI, fg=TXT, bd=0,
                  activebackground=CARD_HI, padx=18, pady=7, cursor="hand2",
                  command=self.close).pack(side="right", padx=8)

        win.bind("<KeyPress>", self._on_keypress)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.update_idletasks()
        w, h = max(win.winfo_reqwidth(), 400), max(win.winfo_reqheight(), 320)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw - w)//2}+{(sh - h)//2}")
        win.deiconify()
        win.lift()
        win.focus_force()

    def _current_text(self):
        spec = self.app.config.get("hotkey", "")
        if not spec:
            return "未设置"
        mods, vk = parse(spec)
        if mods is None or vk is None:
            return "未设置"
        return format_spec(mods, vk)

    def _start_record(self):
        self._recording = True
        self._pending = None
        self._pending_spec = None
        self.hk_var.set("请按组合键…")
        self.rec_btn.configure(text="按下…", bg="#fabe46")
        if self.win:
            self.win.focus_force()

        def hint():
            # 按键被别的程序全局热键吃掉时，这里一直收不到事件，提示一下
            if self._recording and not self._pending:
                self.hk_var.set("没反应？可能这个组合被别的程序占了")

        try:
            self.root.after(3500, hint)
        except Exception:
            pass

    def _stop_record(self):
        self._recording = False
        if self.win and self.win.winfo_exists():
            self.rec_btn.configure(text="录制", bg=ACC)
        if self._pending:
            self.hk_var.set(self._pending)
        else:
            self.hk_var.set(self._current_text())

    def _on_keypress(self, evt):
        if not self._recording:
            return
        mods = _mods_now(evt.state)
        vk = evt.keycode
        # 只按了修饰键本身：继续等真正的按键（允许 Ctrl+Alt+Shift+W 这种多键组合）
        if vk in (0x11, 0x12, 0x10, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C, 0x5D, 0x5E):
            self.hk_var.set("再按一个普通键…")
            return
        if mods == 0:
            self.hk_var.set("至少要带 Ctrl/Alt/Shift/Win")
            return
        spec = self._to_spec(mods, vk)
        if not spec:
            self.hk_var.set("这个键不支持，换个键试试")
            return
        self._pending_spec = spec
        self._pending = format_spec(mods, vk)
        self._stop_record()

    OEM_KEYS = {0xC0: "`", 0xBD: "-", 0xBB: "=", 0xDB: "[", 0xDD: "]", 0xDC: "\\",
                0xBA: ";", 0xDE: "'", 0xBC: ",", 0xBE: ".", 0xBF: "/"}

    def _to_spec(self, mods, vk):
        parts = []
        if mods & MOD_CONTROL:
            parts.append("ctrl")
        if mods & MOD_ALT:
            parts.append("alt")
        if mods & MOD_SHIFT:
            parts.append("shift")
        if mods & MOD_WIN:
            parts.append("win")
        if (0x30 <= vk <= 0x39) or (0x41 <= vk <= 0x5A):
            parts.append(chr(vk).lower())
        elif 0x70 <= vk <= 0x87:
            parts.append(f"f{vk - 0x70 + 1}")
        elif vk in self.OEM_KEYS:
            parts.append(self.OEM_KEYS[vk])
        else:
            for name, code in {"backspace": 0x08, "tab": 0x09, "enter": 0x0D, "space": 0x20,
                               "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
                               "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
                               "pageup": 0x21, "pagedown": 0x22}.items():
                if code == vk:
                    parts.append(name)
                    break
            else:
                return None
        return "+".join(parts)

    def _clear_hotkey(self):
        self._pending = None
        self._pending_spec = ""
        self.hk_var.set("未设置")

    def _save(self):
        spec = self._pending_spec
        if spec is None:
            spec = "" if self.hk_var.get() == "未设置" else self.app.config.get("hotkey", "")
        try:
            warn = max(0, min(99, int(self.warn_var.get() or 20)))
        except ValueError:
            warn = 20
        try:
            good = max(1, min(100, int(self.good_var.get() or 80)))
        except ValueError:
            good = 80
        if good <= warn:
            good = min(100, warn + 1)
        ok = self.app.apply_settings({
            "hotkey": spec or "",
            "hotkey_enabled": bool(self.hk_on.get()),
            "autostart": bool(self.auto_var.get()),
            "refresh_seconds": max(15, int(self.refresh_var.get() or 60)),
            "hover_delay": min(3.0, max(0.0, float(self.hover_var.get() or 0.35))),
            "warn_level": warn,
            "good_level": good,
        })
        if ok is False and self.win and self.win.winfo_exists():
            messagebox.showwarning(
                "热键没生效",
                f"「{self._current_text()}」这个组合被系统或别的程序占用了，\n换一个组合再试（比如 Ctrl+Alt+W）。",
                parent=self.win)
            return
        self.close()

    def close(self):
        if self.win and self.win.winfo_exists():
            self.win.destroy()
        self.win = None
        self._recording = False

    def is_open(self):
        return bool(self.win and self.win.winfo_exists())




class Tooltip:
    """悬停提示：自绘两行卡片（键盘/鼠标各一行）。"""

    TW, TH = 248, 94

    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.win = None
        self.lbl = None
        self.photo = None
        self.ds = 1.0
        self._visible = False
        self._place_key = None
        self._place_xy = None

    def _ensure(self):
        if self.win and self.win.winfo_exists():
            return
        try:
            self.ds = screen_scale()
        except Exception:
            self.ds = 1.0
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=CARD)
        self.lbl = tk.Label(w, bd=0, bg=CARD, highlightthickness=0)
        self.lbl.pack()
        self.win = w

    def _pos(self, w, h):
        """位置固定贴着托盘图标；取不到图标矩形时沿用上次的结果或在鼠标位置兜底。"""
        rect = None
        try:
            rect = self.app.tray.icon_rect()
        except Exception:
            rect = None
        try:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        except Exception:
            sw, sh = 1920, 1080
        if rect:
            key = (tuple(rect), w, h)
            if key == self._place_key and self._place_xy:
                return self._place_xy          # 图标没变 → 一个像素都不动
            xy = anchor_for_rect(rect, w, h, int(round(8 * self.ds)), sw, sh)
            if xy:
                self._place_key, self._place_xy = key, xy
                return xy
        if self._place_xy:
            return self._place_xy
        px, py = cursor_pos()
        return (min(max(px - w // 2, 6), sw - w - 6),
                max(py - h - int(22 * self.ds), 6))

    def show(self):
        self._ensure()
        img = render.render_tooltip(self.app.battery_snapshot(), self.TW, self.TH, scale=self.ds,
                                    good=self.app.config.get("good_level", 80),
                                    warn=self.app.config.get("warn_level", 20))
        self.photo = ImageTk.PhotoImage(img)
        self.lbl.configure(image=self.photo)
        w, h = int(self.TW * self.ds), int(self.TH * self.ds)
        x, y = self._pos(w, h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.deiconify()
        self.win.lift()
        round_region(self.win, w, h, 10 * self.ds)
        self._visible = True

    def hide(self):
        self._visible = False
        if self.win and self.win.winfo_exists():
            self.win.withdraw()

    def update(self):
        if self._visible:
            self.show()

    def is_visible(self):
        return self._visible


def cursor_pos():
    try:
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    except Exception:
        return 0, 0





