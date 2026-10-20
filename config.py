"""配置读写：%APPDATA%\\设备电量\\config.json"""
import json
import os
import sys

APP_DIR_NAME = "设备电量"

DEFAULTS = {
    "swap_on": False,
    "hotkey_enabled": True,
    "hotkey": "ctrl+alt+w",
    "autostart": False,
    "refresh_seconds": 60,
    "devices": [
        {"key": "keyboard", "label": "ATK Z87 键盘", "vid": "373B", "pid": "1012"},
        {"key": "mouse", "label": "VXE R1 Pro Max 鼠标", "vid": "3554", "pid": "F58A"},
    ],
}


def app_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


def config_path():
    return os.path.join(app_dir(), "config.json")


class Config:
    def __init__(self):
        self.data = json.loads(json.dumps(DEFAULTS))
        self.load()

    def load(self):
        path = config_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        self.data[k] = v
        except Exception:
            pass

    def save(self):
        os.makedirs(app_dir(), exist_ok=True)
        tmp = config_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, config_path())

    def get(self, key, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def devices(self):
        return self.data.get("devices", DEFAULTS["devices"])


def exe_path():
    """当前可执行文件（打包后是 exe 本身）。"""
    return os.path.abspath(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])


def program_dir():
    """程序所在目录（打包后是 exe 所在目录，源码运行时是项目目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

