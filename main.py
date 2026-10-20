"""设备电量 · 托盘工具

单图标 + 键盘/鼠标电量 + WASD 互换开关。纯标准库。
"""
import ctypes
import ctypes.wintypes
import importlib.util
import queue
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import autostart as autostart_mod
import hiddev
from config import Config, exe_path, program_dir
from hotkey import Hotkey, parse
from keyswap import KeySwap
from tray import Tray
from panel import Panel, Settings, Tooltip

APP_NAME = "设备电量"
MUTEX_NAME = "Global\\EZBatterySingleInstance"


def _enable_dpi_awareness():
    """按真实分辨率渲染；否则 Windows 会把窗口整体拉伸 1.25 倍，字和边缘就发毛。"""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def single_instance():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def icon_path():
    for name in ("app.ico", os.path.join("assets", "app.ico")):
        p = os.path.join(program_dir(), name)
        if os.path.exists(p):
            return p
    p = os.path.join(HERE, "assets", "app.ico")
    return p if os.path.exists(p) else None


class PluginContext:
    """给 plugins/*.py 的上下文。"""

    def __init__(self, app):
        self.app = app
        self.menu_items = []

    def add_menu_item(self, label, callback):
        self.menu_items.append((label, callback))

    def battery(self):
        return self.app.battery_snapshot()

    def config(self):
        return self.app.config.data

    def toggle_swap(self):
        return self.app.toggle_swap()


class App:
    def __init__(self):
        self.config = Config()
        self.devices = self.config.devices()
        self._battery = [{"key": d.get("key"), "label": d.get("label", ""), "state": "unknown", "level": None}
                         for d in self.devices]
        self._battery_lock = threading.Lock()
        self._last_level = dict(self.config.get("last_levels") or {})
        self._refreshing = threading.Event()
        self._stop = threading.Event()

        self._ui_queue = queue.Queue()
        self.root = tk.Tk()
        try:
            self.root.tk.call('tk', 'scaling', self.root.winfo_fpixels('1i') / 72.0)
        except Exception:
            pass
        self.root.withdraw()
        self.root.title(APP_NAME)

        self.keyswap = KeySwap(enabled=bool(self.config.get("swap_on", False)))
        self.hotkey = Hotkey(spec=self.config.get("hotkey") or None, on_fire=self._on_hotkey)
        self.panel = Panel(self.root, self)
        self.settings = Settings(self.root, self)
        self.tooltip = Tooltip(self.root, self)

        self.tray = Tray(icon_path=icon_path(),
                         on_left=lambda: self.post(self.open_panel),
                         on_right=lambda: self.post(self.open_panel),
                         tip=APP_NAME,
                         pin=bool(self.config.get("pin_tray", True)))

        self.plugins = PluginContext(self)
        self._loaded_plugins = []

    # ---------- 线程 → UI ----------
    def post(self, fn):
        """从非 UI 线程安全地把调用丢回主线程执行。"""
        self._ui_queue.put(fn)

    def _drain(self):
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:
                print(f"[ui] {exc!r}", file=sys.stderr, flush=True)
        try:
            self.root.after(80, self._drain)
        except Exception:
            pass

    # ---------- 电量 ----------
    def battery_snapshot(self):
        with self._battery_lock:
            return [dict(x) for x in self._battery]

    def refresh_battery(self, blocking=False):
        if self._refreshing.is_set():
            return
        self._refreshing.set()

        def work():
            try:
                result = hiddev.read_all(self.devices)
                changed = False
                for dev in result:
                    if dev["state"] == "ok" and dev["level"] is not None:
                        if self._last_level.get(dev["key"]) != dev["level"]:
                            self._last_level[dev["key"]] = dev["level"]
                            changed = True
                    else:
                        prev = self._last_level.get(dev["key"])
                        if prev is not None:
                            dev["last"] = prev
                if changed:
                    self._save_last_levels()
                with self._battery_lock:
                    self._battery = result
                self.post(self._on_battery_updated)
            finally:
                self._refreshing.clear()

        if blocking:
            work()
        else:
            threading.Thread(target=work, name="battery", daemon=True).start()

    def _save_last_levels(self):
        """上次读到的电量存进配置，重启后休眠设备也能显示数字。"""
        try:
            if self.config.get("last_levels") != self._last_level:
                self.config.set("last_levels", dict(self._last_level))
        except Exception:
            pass

    def _on_battery_updated(self):
        if os.environ.get("EZB_DEBUG") == "1":
            for dev in self.battery_snapshot():
                print(f"[battery] {dev['label']}: {dev['state']} {dev['level']}", flush=True)
        self.panel.update()
        self.tooltip.update()

    def _tip_text(self):
        parts = []
        for dev in self.battery_snapshot():
            short = dev["label"].split(" ")[0]
            if dev["state"] == "ok" and dev["level"] is not None:
                parts.append(f"{short} {dev['level']}%")
            elif dev["state"] == "sleep":
                parts.append(f"{short} 休眠")
            elif dev["state"] == "absent":
                parts.append(f"{short} 未连接")
            else:
                parts.append(f"{short} --")
        return " ｜ ".join(parts)[:120]


    def _hover_loop(self):
        """监听鼠标是否停在托盘图标上，控制自绘提示。"""
        inside = False
        show_at = None
        while not self._stop.is_set():
            rect = self.tray.hover_rect()
            if os.environ.get('EZB_DEBUG') == '1' and int(time.time()) % 5 == 0:
                print(f'[hover] icon rect={rect}', file=sys.stderr, flush=True)
            try:
                pt = ctypes.wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                px, py = pt.x, pt.y
            except Exception:
                px, py = 0, 0
            if os.environ.get('EZB_DUMP_RECT') == '1' and rect:
                try:
                    with open(os.path.join(os.environ.get('TEMP','.'), 'ezb_rect.txt'), 'w') as fh:
                        fh.write(','.join(str(v) for v in rect))
                except Exception:
                    pass
            now = bool(rect) and rect[0] <= px <= rect[2] and rect[1] <= py <= rect[3]
            if now and not inside:
                # 刚进入：开始计时，停留够久才显示
                show_at = time.time() + max(0.0, float(self.config.get("hover_delay", 0.35)))
            elif not now and inside:
                show_at = None
                if self.tooltip.is_visible():
                    self.post(self.tooltip.hide)
            inside = now
            if inside and show_at is not None and not self.tooltip.is_visible() and time.time() >= show_at:
                show_at = None
                self.post(self.tooltip.show)
            self._stop.wait(0.08)
    def _refresh_loop(self):
        while not self._stop.is_set():
            self.refresh_battery()
            interval = max(15, int(self.config.get("refresh_seconds", 60)))
            self._stop.wait(interval)

    # ---------- 互换 ----------
    def _on_hotkey(self):
        self.post(self.toggle_swap)

    def toggle_swap(self):
        enabled = self.keyswap.toggle()
        self.config.set("swap_on", enabled)
        self.panel.update()
        return enabled

    def on_swap_changed(self, value):
        self.keyswap.set_enabled(value, notify=False)
        self.config.set("swap_on", bool(value))
        self.panel.update()

    # ---------- 设置 ----------
    def open_panel(self):
        self.tooltip.hide()
        self.refresh_battery()
        self.panel.show()

    def open_settings(self):
        self.settings.open()

    def settings_open(self):
        return self.settings.is_open()

    def apply_settings(self, values):
        for k, v in values.items():
            self.config.set(k, v)
        autostart_mod.sync(bool(values.get("autostart", False)), exe_path())
        # 热键；返回 False 表示这个组合没注册上（被系统或其他程序占了）
        ok = True
        spec = values.get("hotkey", "")
        if values.get("hotkey_enabled") and spec:
            mods, vk = parse(spec)
            if mods and vk:
                ok = bool(self.hotkey.rebind(spec))
        else:
            self.hotkey.stop()
        self.panel.update()
        return ok

    # ---------- 插件 ----------
    def load_plugins(self):
        pdir = os.path.join(program_dir(), "plugins")
        if not os.path.isdir(pdir):
            return
        for name in sorted(os.listdir(pdir)):
            if not name.endswith(".py") or name.startswith("_"):
                continue
            path = os.path.join(pdir, name)
            try:
                spec = importlib.util.spec_from_file_location(f"ezplugin_{name[:-3]}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register(self.plugins)
                    self._loaded_plugins.append(name)
            except Exception as exc:
                print(f"[plugin] {name} 加载失败: {exc}", file=sys.stderr)

    # ---------- 生命周期 ----------
    def quit(self):
        self._stop.set()
        try:
            self.config.set("swap_on", self.keyswap.enabled)
        except Exception:
            pass
        try:
            self.keyswap.stop()
            self.hotkey.stop()
            self.tray.stop()
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        from config import config_path
        try:
            if not os.path.exists(config_path()):
                self.config.save()
        except Exception:
            pass
        self.tray.start()
        self.keyswap.start()
        if self.config.get("hotkey_enabled") and self.config.get("hotkey"):
            self.hotkey.start()
            if os.environ.get("EZB_DEBUG") == "1":
                print(f"[hotkey] {self.config.get('hotkey')} registered={self.hotkey.registered}", flush=True)
        self.load_plugins()
        autostart_mod.sync(bool(self.config.get("autostart", False)), exe_path())

        self.tray.set_tip('')
        self.root.after(80, self._drain)
        self.refresh_battery()
        if os.environ.get('EZB_DEBUG_TOOLTIP') == '1':
            self.root.after(2000, self.tooltip.show)
        if os.environ.get('EZB_DEBUG_PANEL') == '1':
            self.root.after(1500, self.panel.show)
        if os.environ.get('EZB_DEBUG_SETTINGS') == '1':
            self.root.after(1800, self.open_settings)
        threading.Thread(target=self._refresh_loop, name="refresh", daemon=True).start()
        threading.Thread(target=self._hover_loop, name="hover", daemon=True).start()
        self.root.mainloop()


def main():
    _enable_dpi_awareness()
    handle = single_instance()
    if handle is None:
        # 已经在跑
        return 0
    app = App()
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ctypes.windll.kernel32.ReleaseMutex(handle)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())














