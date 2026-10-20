"""全局按键互换（WASD <-> 方向键），用低级键盘钩子，纯标准库。"""
import ctypes
import os
import threading
from ctypes import wintypes, POINTER

user32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
LLKHF_INJECTED = 0x10
MAGIC = 0xE2B00001  # 自己注入的按键标记，防止互相触发成死循环

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

# vk -> (scancode, is_extended)
MODIFIER_VKS = (0x11, 0x12, 0x10, 0x5B, 0x5C)  # Ctrl/Alt/Shift/Win


FORWARD = {
    0x57: (0x48, True),   # W -> Up
    0x41: (0x4B, True),   # A -> Left
    0x53: (0x50, True),   # S -> Down
    0x44: (0x4D, True),   # D -> Right
}
BACKWARD = {
    0x26: (0x11, False),  # Up -> W
    0x25: (0x1E, False),  # Left -> A
    0x28: (0x1F, False),  # Down -> S
    0x27: (0x20, False),  # Right -> D
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_ubyte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.SendInput.argtypes = [wintypes.UINT, POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
k32.GetModuleHandleW.restype = wintypes.HMODULE
k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def _modifier_down():
    for vk in MODIFIER_VKS:
        if user32.GetAsyncKeyState(vk) & 0x8000:
            return True
    return False


def _send_scan(scan, extended, keyup):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = 0
    inp.ki.wScan = scan
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if keyup:
        flags |= KEYEVENTF_KEYUP
    inp.ki.dwFlags = flags
    inp.ki.dwExtraInfo = MAGIC
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class KeySwap:
    """开/关 WASD 与方向键互换。"""

    def __init__(self, enabled=False, on_change=None):
        self._enabled = bool(enabled)
        self._on_change = on_change
        self._hook = None
        self._thread = None
        self._proc = HOOKPROC(self._callback)
        self._lock = threading.Lock()

    @property
    def enabled(self):
        with self._lock:
            return self._enabled

    def set_enabled(self, value, notify=True):
        with self._lock:
            changed = self._enabled != bool(value)
            self._enabled = bool(value)
        if changed and notify and self._on_change:
            self._on_change(self._enabled)

    def toggle(self):
        self.set_enabled(not self.enabled)
        return self.enabled

    def _callback(self, n_code, w_param, l_param):
        try:
            if n_code == 0 and os.environ.get('EZB_DEBUG') == '1':
                import sys
                kb0 = ctypes.cast(l_param, POINTER(KBDLLHOOKSTRUCT)).contents
                print(f'[hook] ev vk={kb0.vkCode:#04x} flags={kb0.flags:#x} enabled={self._enabled}', file=sys.stderr, flush=True)
            if n_code == 0 and self._enabled:
                kb = ctypes.cast(l_param, POINTER(KBDLLHOOKSTRUCT)).contents
                foreign = not (kb.flags & LLKHF_INJECTED) or os.environ.get('EZB_ALLOW_INJECTED') == '1'
                if kb.dwExtraInfo == MAGIC:
                    foreign = False
                if foreign and not _modifier_down():
                    down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    up = w_param in (WM_KEYUP, WM_SYSKEYUP)
                    if down or up:
                        target = None
                        if kb.vkCode in FORWARD:
                            target = FORWARD[kb.vkCode]
                        elif kb.vkCode in BACKWARD:
                            target = BACKWARD[kb.vkCode]
                        if target is not None:
                            scan, ext = target
                            _send_scan(scan, ext, up)
                            if os.environ.get("EZB_DEBUG") == "1":
                                import sys
                                print(f"[swap] vk={kb.vkCode:#04x} -> scan={scan:#04x} up={up}",
                                      file=sys.stderr, flush=True)
                            return 1  # 吞掉原按键
        except Exception:
            pass
        return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="keyswap-hook", daemon=True)
        self._thread.start()

    def _loop(self):
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                              k32.GetModuleHandleW(None), 0)
        if os.environ.get('EZB_DEBUG') == '1':
            import sys
            print(f'[hook] install ok={bool(self._hook)} err={ctypes.get_last_error()}', file=sys.stderr, flush=True)
        if not self._hook:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None






