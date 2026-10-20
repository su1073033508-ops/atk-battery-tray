"""HID 枚举与电量读取（纯标准库 ctypes）。"""
import ctypes
import threading
import time
from ctypes import wintypes, POINTER, byref, sizeof

hid = ctypes.WinDLL("hid.dll", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi.dll", use_last_error=True)
k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

HID_GUID_S = "{4D1E55B2-F16F-11CF-88CB-001111000030}"
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
OPEN_EXISTING = 3
INVALID = wintypes.HANDLE(-1).value


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class DID(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.POINTER(wintypes.ULONG))]


class CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InLen", wintypes.USHORT), ("OutLen", wintypes.USHORT),
                ("FeatLen", wintypes.USHORT), ("Reserved", wintypes.USHORT * 17),
                ("Tail", wintypes.USHORT * 8)]


setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, wintypes.LPVOID, POINTER(GUID),
                                                 wintypes.DWORD, POINTER(DID)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wintypes.HANDLE, POINTER(DID), wintypes.LPVOID,
                                                      wintypes.DWORD, POINTER(wintypes.DWORD), wintypes.LPVOID]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, POINTER(wintypes.LPVOID)]
hid.HidD_FreePreparsedData.argtypes = [wintypes.LPVOID]
hid.HidP_GetCaps.argtypes = [wintypes.LPVOID, POINTER(CAPS)]
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                          POINTER(wintypes.DWORD), wintypes.LPVOID]
k32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                         POINTER(wintypes.DWORD), wintypes.LPVOID]


class HidPath:
    """一个 HID 接口。"""

    def __init__(self, path, usage_page, usage, in_len, out_len):
        self.path = path
        self.usage_page = usage_page
        self.usage = usage
        self.in_len = in_len
        self.out_len = out_len
        low = path.lower()
        self.vid = _grab(low, "vid_")
        self.pid = _grab(low, "pid_")

    def __repr__(self):
        return f"<Hid {self.vid}:{self.pid} up={self.usage_page:#06x} in={self.in_len} out={self.out_len}>"


def _grab(low_path, tag):
    i = low_path.find(tag)
    if i < 0:
        return ""
    return low_path[i + len(tag): i + len(tag) + 4].upper()


def _enum_paths():
    g = GUID()
    ctypes.windll.ole32.CLSIDFromString(HID_GUID_S, byref(g))
    h = setupapi.SetupDiGetClassDevsW(byref(g), None, None, 0x02 | 0x10)
    out, idx = [], 0
    d = DID()
    d.cbSize = sizeof(DID)
    while setupapi.SetupDiEnumDeviceInterfaces(h, None, byref(g), idx, byref(d)):
        idx += 1
        req = wintypes.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(h, byref(d), None, 0, byref(req), None)
        buf = ctypes.create_string_buffer(req.value + 16)
        ctypes.cast(buf, POINTER(wintypes.DWORD))[0] = 8
        if setupapi.SetupDiGetDeviceInterfaceDetailW(h, byref(d), buf, req.value, byref(req), None):
            p = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if p:
                out.append(p)
    setupapi.SetupDiDestroyDeviceInfoList(h)
    return out


def _caps(handle):
    pp = wintypes.LPVOID()
    c = CAPS()
    ok = hid.HidD_GetPreparsedData(handle, byref(pp))
    if ok:
        hid.HidP_GetCaps(pp, byref(c))
        hid.HidD_FreePreparsedData(pp)
    return c, bool(ok)


def list_hid():
    """返回能打开且带 vendor 用途页的接口列表。"""
    found = []
    for path in _enum_paths():
        fh = k32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 1 | 2, None, OPEN_EXISTING, 0, None)
        if not fh or fh == INVALID:
            # 键盘的 kbd 集合需要权限，跳过
            continue
        try:
            c, ok = _caps(fh)
            if ok and c.UsagePage >= 0xFF00:
                found.append(HidPath(path, c.UsagePage, c.Usage, c.InLen, c.OutLen))
        finally:
            k32.CloseHandle(fh)
    return found


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


k32.CreateEventW.restype = wintypes.HANDLE
k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.GetOverlappedResult.argtypes = [wintypes.HANDLE, POINTER(_OVERLAPPED),
                                    POINTER(wintypes.DWORD), wintypes.BOOL]
k32.CancelIoEx.argtypes = [wintypes.HANDLE, POINTER(_OVERLAPPED)]
k32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                         POINTER(wintypes.DWORD), POINTER(_OVERLAPPED)]
k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                          POINTER(wintypes.DWORD), POINTER(_OVERLAPPED)]
ERROR_IO_PENDING = 997
FILE_FLAG_OVERLAPPED = 0x40000000


def _io(fn, handle, buf, length, timeout_ms):
    ov = _OVERLAPPED()
    ov.hEvent = k32.CreateEventW(None, True, False, None)
    done = wintypes.DWORD(0)
    ok = fn(handle, buf, length, byref(done), byref(ov))
    if not ok:
        if ctypes.get_last_error() != ERROR_IO_PENDING:
            k32.CloseHandle(ov.hEvent)
            return None
        if k32.WaitForSingleObject(ov.hEvent, timeout_ms) != 0:
            k32.CancelIoEx(handle, byref(ov))
            k32.CloseHandle(ov.hEvent)
            return None
        k32.GetOverlappedResult(handle, byref(ov), byref(done), False)
    k32.CloseHandle(ov.hEvent)
    return done.value


def query(path, payload, out_len, in_len, timeout_ms=400):
    """写一条报告并读回复；失败返回 None。"""
    fh = k32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 1 | 2, None, OPEN_EXISTING, 0, None)
    if not fh or fh == INVALID:
        return None
    try:
        wbuf = (ctypes.c_ubyte * max(out_len, 1))()
        for i, b in enumerate(payload):
            if i < len(wbuf):
                wbuf[i] = b
        written = wintypes.DWORD(0)
        if not k32.WriteFile(fh, wbuf, len(wbuf), byref(written), None):
            return None
        rbuf = (ctypes.c_ubyte * max(in_len, 1))()
        read = wintypes.DWORD(0)
        if not k32.ReadFile(fh, rbuf, len(rbuf), byref(read), None):
            return None
        return bytes(rbuf[:read.value or len(rbuf)])
    finally:
        k32.CloseHandle(fh)

def read_keyboard(dev_cfg):
    """ATK Z87：0xFF1C，命令 04 11 00 1a，电量在 resp[8]。"""
    for d in list_hid():
        if d.usage_page == 0xFF1C and d.vid == dev_cfg["vid"].upper() and d.pid == dev_cfg["pid"].upper():
            resp = query(d.path, [0x04, 0x11, 0x00, 0x1A], d.out_len, d.in_len)
            if not resp or len(resp) < 9:
                return {"state": "error", "level": None}
            if resp[7] == 0xFF:
                return {"state": "sleep", "level": None}
            level = resp[8]
            if 0 <= level <= 100:
                return {"state": "ok", "level": level}
            return {"state": "error", "level": None}
    return {"state": "absent", "level": None}


def read_mouse(dev_cfg):
    """VXE R1 Pro Max：0xFF02，17 字节报文，末字节 0x49 是校验，电量在 resp[6]。

    注意：请求必须是 [08, 04] + 14×00 + [49]，少这个 0x49 设备会回"未就绪"。
    第一次查询常被当作唤醒，所以要重试。
    """
    payload = [0x08, 0x04] + [0x00] * 14 + [0x49]
    for d in list_hid():
        if d.usage_page == 0xFF02 and d.vid == dev_cfg["vid"].upper() and d.pid == dev_cfg["pid"].upper():
            # 冷启动/刚唤醒时可能返回 0，多点采样后取非 0 的众数
            samples = []
            for _ in range(6):
                resp = query(d.path, payload, d.out_len, d.in_len)
                if resp and len(resp) > 6 and resp[2] == 0 and 0 <= resp[6] <= 100:
                    samples.append(resp[6])
                time.sleep(0.18)
            nonzero = [v for v in samples if v > 0]
            if nonzero:
                # 出现最多的那个值
                best = max(set(nonzero), key=nonzero.count)
                return {"state": "ok", "level": best}
            if samples:
                return {"state": "sleep", "level": None}
            return {"state": "error", "level": None}
    return {"state": "absent", "level": None}


READERS = {
    "keyboard": read_keyboard,
    "mouse": read_mouse,
}


def _read_with_timeout(reader, dev, timeout=2.0):
    """在子线程里读，超时就放弃（防止个别设备不回话把刷新卡死）。"""
    box = {}

    def work():
        try:
            box["r"] = reader(dev)
        except Exception:
            box["r"] = {"state": "error", "level": None}

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return {"state": "error", "level": None}
    return box.get("r") or {"state": "error", "level": None}


def read_all(devices):
    """返回 [{'key','label','state','level'}]"""
    result = []
    for dev in devices:
        reader = READERS.get(dev.get("key"))
        info = _read_with_timeout(reader, dev) if reader else {"state": "error", "level": None}
        result.append({
            "key": dev.get("key"),
            "label": dev.get("label", dev.get("key", "")),
            "state": info["state"],
            "level": info["level"],
        })
    return result






