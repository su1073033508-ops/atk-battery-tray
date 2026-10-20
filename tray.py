"""Win32 托盘图标（ctypes，无第三方依赖）。"""
import ctypes
import os
import sys
import threading
import time
import winreg
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_DESTROY = 0x0002

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x10, 0x40
IDI_APPLICATION = 32512
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x0203


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
k32.GetModuleHandleW.restype = wintypes.HMODULE

class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", GUID)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


shell32.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(RECT)]
shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long


# ---------- 「钉在任务栏」还是「收进 ^ 隐藏区」 ----------
# Windows 11 把新图标默认塞进「^」，此时 Shell_NotifyIconGetRect 仍会返回一个矩形，
# 但那是「图标本该在的空位」，鼠标扫过它（就在 ^ 旁边）就会误触发悬停提示。
NOTIFY_ICON_SETTINGS = r"Control Panel\NotifyIconSettings"
OVERFLOW_WINDOW_CLASSES = ("TopLevelWindowForOverflowXamlIsland",   # Win11
                           "NotifyIconOverflowWindow")              # Win10
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]


def self_exe():
    """本程序的 exe 路径（注册表就是按它认图标的）。"""
    try:
        return os.path.abspath(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
    except Exception:
        return ""


def _norm_path(p):
    try:
        return os.path.normcase(os.path.abspath(os.path.expandvars(str(p))))
    except Exception:
        return str(p).lower()


def _find_icon_entry(exe=None):
    """NotifyIconSettings 下本程序的子键名；找不到返回 None。"""
    want = _norm_path(exe or self_exe())
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_ICON_SETTINGS) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                name = winreg.EnumKey(root, i)
                try:
                    with winreg.OpenKey(root, name) as k:
                        path, _ = winreg.QueryValueEx(k, "ExecutablePath")
                except OSError:
                    continue
                if _norm_path(path) == want:
                    return name
    except OSError:
        return None
    return None


def promoted(exe=None):
    """True=钉在任务栏；False=在「^」隐藏区；None=查不到（老系统 / 还没建记录）。"""
    name = _find_icon_entry(exe)
    if not name:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_ICON_SETTINGS + "\\" + name) as k:
            try:
                val, _ = winreg.QueryValueEx(k, "IsPromoted")
            except FileNotFoundError:
                return None
            return bool(val)
    except OSError:
        return None


def set_promoted(value, exe=None):
    name = _find_icon_entry(exe)
    if not name:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_ICON_SETTINGS + "\\" + name,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, 1 if value else 0)
        return True
    except OSError:
        return False


def overflow_open():
    """「^」隐藏图标面板是否正开着。"""
    hits = []

    def cb(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buf, 128)
        if buf.value in OVERFLOW_WINDOW_CLASSES and user32.IsWindowVisible(hwnd):
            hits.append(hwnd)
            return False
        return True

    try:
        user32.EnumWindows(ENUMPROC(cb), 0)
    except Exception:
        pass
    return bool(hits)


class Tray:
    """托盘图标。on_left/on_right 在工作线程里被调用，UI 侧自行调度。"""

    def __init__(self, icon_path=None, on_left=None, on_right=None, tip="设备电量", pin=True):
        self.icon_path = icon_path
        self.on_left = on_left
        self.on_right = on_right
        self._hwnd = None
        self._icon = None
        self._nid = None
        self._proc = WNDPROC(self._wndproc)
        self._tip = tip
        self.pin = pin                     # 启动时把图标钉在任务栏（别塞进「^」）
        self._promoted = None
        self._promote_checked = 0.0

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            ev = lparam & 0xFFFF
            try:
                if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK) and self.on_left:
                    self.on_left()
                elif ev == WM_RBUTTONUP and self.on_right:
                    self.on_right()
            except Exception:
                pass
            return 0
        if msg == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def start(self):
        hinst = k32.GetModuleHandleW(None)
        cls = WNDCLASSW()
        cls.lpfnWndProc = self._proc
        cls.hInstance = hinst
        cls.lpszClassName = "EZTrayWnd"
        user32.RegisterClassW(ctypes.byref(cls))
        self._hwnd = user32.CreateWindowExW(0, "EZTrayWnd", "EZTrayWnd", 0, 0, 0, 0, 0,
                                            None, None, hinst, None)
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        # 图标
        hicon = None
        if self.icon_path and os.path.exists(self.icon_path):
            hicon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            if not hicon:
                hicon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if not hicon:
            hicon = user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        self._icon = hicon

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = hicon
        nid.szTip = self._tip[:127]
        self._nid = nid
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

        if self.pin:
            try:
                self.ensure_promoted()
            except Exception:
                pass

        threading.Thread(target=self._loop, name="tray-loop", daemon=True).start()

    def ensure_promoted(self):
        """把自己钉在任务栏上：Windows 11 默认把新图标塞进「^」隐藏区。

        改注册表里的 IsPromoted 后必须删掉图标再加一次，Explorer 才会重新读设置。
        """
        try:
            state = promoted()
            if state is None:
                return False                  # 还没生成记录，下次启动再试
            if state:
                self._promoted = True
                return True
            if not set_promoted(1):
                return False
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            time.sleep(0.08)
            shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid))
            self._promoted = True
            self._promote_checked = time.time()
            return True
        except Exception:
            return False

    def hover_rect(self):
        """能真正悬停到图标时才返回它的矩形。

        图标被收进「^」且面板没打开时，Shell 也会给一个矩形——那是「图标本该在的空位」，
        紧挨着「^」，鼠标扫过就误弹提示；这种情况返回 None。
        """
        rect = self.icon_rect()
        if not rect:
            return None
        now = time.time()
        if now - self._promote_checked > 2.0:
            self._promote_checked = now
            st = promoted()
            if st is not None:
                self._promoted = st
        if self._promoted is not False:       # True 或未知 → 照旧
            return rect
        if overflow_open():                   # 面板开着，图标就在面板里，矩形是真的
            return rect
        return None

    def _loop(self):
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def set_tip(self, text):
        self._tip = text
        if not self._nid:
            return
        self._nid.szTip = text[:127]
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    def icon_rect(self):
        """托盘图标在屏幕上的矩形；取不到返回 None。"""
        if not self._nid:
            return None
        try:
            ident = NOTIFYICONIDENTIFIER()
            ident.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
            ident.hWnd = self._nid.hWnd
            ident.uID = self._nid.uID
            r = RECT()
            hr = shell32.Shell_NotifyIconGetRect(ctypes.byref(ident), ctypes.byref(r))
            if hr != 0:
                return None
            return (r.left, r.top, r.right, r.bottom)
        except Exception:
            return None

    def stop(self):
        try:
            if self._nid:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                self._nid = None
            if self._hwnd:
                user32.DestroyWindow(self._hwnd)
                self._hwnd = None
        except Exception:
            pass


