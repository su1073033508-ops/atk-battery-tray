"""全局热键（RegisterHotKey）。"""
import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
WM_HOTKEY = 0x0312

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
             "shift": MOD_SHIFT, "win": MOD_WIN, "cmd": MOD_WIN}

VK_NAMES = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA,
    "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}


def parse(spec):
    """'ctrl+alt+w' -> (mods, vk)；无法解析返回 (None, None)。"""
    if not spec:
        return None, None
    mods = 0
    vk = None
    for raw in str(spec).lower().replace(" ", "").split("+"):
        if not raw:
            continue
        if raw in MOD_NAMES:
            mods |= MOD_NAMES[raw]
        elif len(raw) == 1:
            ch = raw.upper()
            if "A" <= ch <= "Z" or "0" <= ch <= "9":
                vk = ord(ch)
            else:
                vk = VK_NAMES.get(raw)
        elif raw.startswith("f") and raw[1:].isdigit() and 1 <= int(raw[1:]) <= 24:
            vk = 0x70 + int(raw[1:]) - 1
        else:
            vk = VK_NAMES.get(raw)
    return (mods, vk)


def format_spec(mods, vk):
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    if 0x70 <= vk <= 0x87:
        parts.append(f"F{vk - 0x70 + 1}")
    elif 0x30 <= vk <= 0x5A:
        parts.append(chr(vk))
    else:
        for name, code in VK_NAMES.items():
            if code == vk:
                parts.append(name.upper() if len(name) <= 3 else name.title())
                break
        else:
            parts.append(f"0x{vk:02X}")
    return "+".join(parts)


class Hotkey:
    """一个可改的全局热键；on_fire 在热键线程里调用。"""

    def __init__(self, spec=None, on_fire=None):
        self.spec = spec
        self.on_fire = on_fire
        self._thread = None
        self._tid = None
        self._registered = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self, spec=None):
        if spec is not None:
            self.spec = spec
        mods, vk = parse(self.spec)
        if mods is None or vk is None:
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, args=(mods, vk), name="hotkey", daemon=True)
        self._thread.start()
        return True

    def _loop(self, mods, vk):
        self._tid = threading.get_ident()
        ok = user32.RegisterHotKey(None, 1, mods, vk)
        self._registered = bool(ok)
        if __import__("os").environ.get("EZB_DEBUG") == "1":
            import sys
            print(f"[hotkey-loop] register ok={bool(ok)} err={ctypes.get_last_error()}", file=sys.stderr, flush=True)
        if not ok:
            return
        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            # 用 PeekMessage 轮询，方便退出
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY and self.on_fire:
                    try:
                        self.on_fire()
                    except Exception:
                        pass
                else:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            else:
                self._stop_event.wait(0.05)
        user32.UnregisterHotKey(None, 1)
        self._registered = False

    def rebind(self, spec):
        self.stop()
        self.spec = spec
        if spec:
            self.start(spec)

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    @property
    def registered(self):
        return self._registered

