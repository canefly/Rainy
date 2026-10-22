import os
import json
import time
import winreg
import platform
import ctypes
import urllib.request
from typing import Optional, Dict, Any
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool

from .database import db

def get_machine_guid() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography') as k:
            return winreg.QueryValueEx(k, 'MachineGuid')[0]
    except Exception:
        return 'UNKNOWN_HWID'

def get_ram_gb() -> int:
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ('dwLength', ctypes.c_ulong),
                ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong),
                ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong),
                ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong),
                ('ullAvailVirtual', ctypes.c_ulonglong),
                ('sullAvailExtendedVirtual', ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullTotalPhys / (1024**3))
    except Exception:
        return 16

def get_system_specs() -> Dict[str, Any]:
    return {
        "hwid": get_machine_guid(),
        "cpu": platform.processor(),
        "gpu": "Windows Video Controller",
        "ram_gb": get_ram_gb()
    }


class AuthSignals(QObject):
    verified = Signal(str, bool, str)  # (editor_name, is_offline, time_left_str)
    failed = Signal(str)               # (reason)


class VerifyWorker(QRunnable):
    """Asynchronous worker to verify hardware ID against Cane's licensing server."""
    def __init__(self, alias: Optional[str] = None):
        super().__init__()
        self.alias = alias
        self.signals = AuthSignals()

    def run(self):
        specs = get_system_specs()
        if self.alias:
            specs["editor_name"] = self.alias

        endpoint = "https://click.canefly.xyz/api/verify.php"
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(specs).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': 'ClickablesTools/2.0 Native'}
        )

        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                body = res.read().decode('utf-8')
                data = json.loads(body)
                
                status = data.get("status")
                editor_name = data.get("editor_name") or db.get_setting("editorName", "CANEEE")

                def _safe_emit_verified(*args):
                    try:
                        self.signals.verified.emit(*args)
                    except RuntimeError:
                        pass

                def _safe_emit_failed(*args):
                    try:
                        self.signals.failed.emit(*args)
                    except RuntimeError:
                        pass

                if status == "approved":
                    now = time.time()
                    db.set_setting("lastVerified", str(now))
                    db.set_setting("editorName", editor_name)
                    _safe_emit_verified(editor_name, False, "")
                    return
                elif status == "pending":
                    db.set_setting("editorName", editor_name)
                    _safe_emit_verified(f"{editor_name} (Pending)", False, "")
                    return
                elif status == "revoked":
                    _safe_emit_failed("License Revoked")
                    return
                else:
                    _safe_emit_failed("Unverified HWID")
                    return
        except Exception as e:
            # Server unreachable -> Offline license fallback (7 days grace period)
            last_verified_str = db.get_setting("lastVerified")
            editor_name = db.get_setting("editorName", "CANEEE")
            seven_days = 7 * 24 * 3600

            def _safe_emit_verified(*args):
                try:
                    self.signals.verified.emit(*args)
                except RuntimeError:
                    pass

            def _safe_emit_failed(*args):
                try:
                    self.signals.failed.emit(*args)
                except RuntimeError:
                    pass

            if last_verified_str:
                try:
                    last_verified = float(last_verified_str)
                    secs_left = (last_verified + seven_days) - time.time()
                    if secs_left > 0:
                        days = int(secs_left // 86400)
                        hours = int((secs_left % 86400) // 3600)
                        time_left_str = f"{days}d {hours}h" if days > 0 else f"{hours}h"
                        _safe_emit_verified(editor_name, True, time_left_str)
                        return
                    else:
                        _safe_emit_failed("Offline License Expired")
                        return
                except Exception:
                    pass

            # Default safe fallback for Cane's desktop
            _safe_emit_verified(editor_name, True, "7d")


class AuthService:
    """Manages system authentication and editor alias state."""
    _instance = None

    def __init__(self):
        self.thread_pool = QThreadPool.globalInstance()
        self.current_editor_name = db.get_setting("editorName", "CANEEE")
        self.is_offline = False
        self.offline_time_left = ""

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def verify_async(self, on_verified=None, on_failed=None, alias=None):
        worker = VerifyWorker(alias=alias)
        if on_verified:
            worker.signals.verified.connect(on_verified)
        if on_failed:
            worker.signals.failed.connect(on_failed)
        self.thread_pool.start(worker)
