"""Hardware summary sent to the license server when registering a device.

Windows only for now (registry / CIM). The macOS port swaps these three functions
(IOPlatformUUID, sysctl hw.memsize, system_profiler), see docs/mac-porting-plan.md.
"""
import ctypes
import functools
import platform
import subprocess
import sys
from typing import Any, Dict

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def get_machine_guid() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
            return winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        return "UNKNOWN_HWID"


def get_ram_gb() -> int:
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullTotalPhys / (1024 ** 3))
    except Exception:
        return 0


def get_cpu_name() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except Exception:
        return platform.processor() or "Unknown"


def get_gpu_names() -> str:
    """All video adapters joined by ' | '. Best effort, never raises, 6 s cap."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_VideoController).Name -join ' | '"],
            capture_output=True, text=True, timeout=6, creationflags=_CREATE_NO_WINDOW,
        )
        name = out.stdout.strip()
        return name or "Unknown"
    except Exception:
        return "Unknown"


@functools.lru_cache(maxsize=1)
def get_system_specs() -> Dict[str, Any]:
    return {
        "hwid": get_machine_guid(),
        "cpu": get_cpu_name(),
        "gpu": get_gpu_names(),
        "ram_gb": get_ram_gb(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
    }
