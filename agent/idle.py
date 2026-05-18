"""Idle detection on Windows via GetLastInputInfo. No-op fallback elsewhere."""
from __future__ import annotations

import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import Structure, byref, c_uint

    class _LASTINPUTINFO(Structure):
        _fields_ = [("cbSize", c_uint), ("dwTime", c_uint)]

    def get_idle_seconds() -> float:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(lii)
        if not ctypes.windll.user32.GetLastInputInfo(byref(lii)):
            return 0.0
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return max(0.0, millis / 1000.0)

else:
    def get_idle_seconds() -> float:  # pragma: no cover
        return 0.0
