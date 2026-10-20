"""界面：托盘面板（PIL 抗锯齿绘制）+ 设置窗口（tkinter）。"""
import ctypes
import ctypes.wintypes
import tkinter as tk
import tkinter.font as tkfont

from PIL import ImageTk

import render
from hotkey import format_spec, parse

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

    DW, DH = 328, 268
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
        try:
            px, py = self.root.winfo_pointerxy()
        except Exception:
            px, py = 200, 200
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
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
        items = [("设置…", self.app.open_settings), ("刷新电量", self.app.refresh_battery),
                 ("退出", self.app.quit)]
        for label, cb in getattr(self.app.plugins, "menu_items", []):
            items.insert(2, (label, cb))
        return items

    def _redraw(self):
        if not self.lbl or not self.win or not self.win.winfo_exists():
            return
        items = self._menu_items()
        self._menu_labels = [i[0] for i in items]
        img, rects = render.compose(
            self.app.battery_snapshot(), self.app.keyswap.enabled, self._hover,
            self._menu_labels, self.DW, self.DH, scale=self.ds)
        self.photo = ImageTk.PhotoImage(img)
        self.lbl.configure(image=self.photo)
        self._menu_rects = [(r[0], r[1], r[2], r[3]) for r in rects]
        y = 14 + 56 * len(self.app.battery_snapshot())
        ty = y + 16
        self._toggle_rect = (self.PAD - 6, ty - 8, self.DW - self.PAD + 6, ty + 30)

    def update(self):
        self._redraw()

    # ---------- 交互 ----------
    def _to_design(self, evt):
        return evt.x / self.ds, evt.y / self.ds

    def _hit(self, x, y):
        if self._toggle_rect:
            x1, y1, x2, y2 = self._toggle_rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                return "toggle", None
        for i, (x1, y1, x2, y2) in enumerate(self._menu_rects):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return "menu", i
        return None, None

    def _on_motion(self, evt):
        x, y = self._to_design(evt)
        kind, idx = self._hit(x, y)
        label = self._menu_labels[idx] if kind == "menu" else None
        if label != self._hover:
            self._hover = label
            self._redraw()

    def _on_leave(self, _evt):
        if self._hover is not None:
            self._hover = None
            self._redraw()

    def _on_click(self, evt):
        x, y = self._to_design(evt)
        kind, idx = self._hit(x, y)
        if kind == "toggle":
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
        mods = 0
        state = evt.state
        if state & 0x4:
            mods |= 0x0002
        if state & 0x8:
            mods |= 0x0001
        if state & 0x1:
            mods |= 0x0004
        vk = evt.keycode
        if vk in (0x11, 0x12, 0x10, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C):
            return
        if mods == 0:
            self.hk_var.set("至少要带 Ctrl/Alt/Shift")
            return
        spec = self._to_spec(mods, vk)
        if not spec:
            return
        self._pending_spec = spec
        self._pending = format_spec(mods, vk)
        self._stop_record()

    def _to_spec(self, mods, vk):
        parts = []
        if mods & 0x0002:
            parts.append("ctrl")
        if mods & 0x0001:
            parts.append("alt")
        if mods & 0x0004:
            parts.append("shift")
        if 0x30 <= vk <= 0x5A:
            parts.append(chr(vk).lower())
        elif 0x70 <= vk <= 0x87:
            parts.append(f"f{vk - 0x70 + 1}")
        else:
            for name, code in {"backspace": 0x08, "tab": 0x09, "enter": 0x0D, "space": 0x20,
                               "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
                               "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23}.items():
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
        self.app.apply_settings({
            "hotkey": spec or "",
            "hotkey_enabled": bool(self.hk_on.get()),
            "autostart": bool(self.auto_var.get()),
            "refresh_seconds": max(15, int(self.refresh_var.get() or 60)),
        })
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

    def show(self):
        self._ensure()
        img = render.render_tooltip(self.app.battery_snapshot(), self.TW, self.TH, scale=self.ds)
        self.photo = ImageTk.PhotoImage(img)
        self.lbl.configure(image=self.photo)
        w, h = int(self.TW * self.ds), int(self.TH * self.ds)
        px, py = cursor_pos()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = min(max(px - w // 2, 6), sw - w - 6)
        y = max(py - h - int(22 * self.ds), 6)
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




