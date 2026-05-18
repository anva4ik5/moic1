"""Monitors for newly installed software (Windows registry scan).

Periodically reads the Uninstall keys from the registry and detects new entries.
"""
from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger("monitor.agent.software")

SCAN_INTERVAL = 120  # seconds


@dataclass
class SoftwareEvent:
    ts: datetime
    name: str
    version: str
    publisher: str


def _list_installed() -> dict[str, dict]:
    """Read installed programs from the Windows registry."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg
    except ImportError:
        return {}
    result: dict[str, dict] = {}
    paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subkey in paths:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        name_key = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, name_key) as sub:
                            display = _reg_val(sub, "DisplayName")
                            if not display:
                                continue
                            result[display] = {
                                "name": display,
                                "version": _reg_val(sub, "DisplayVersion"),
                                "publisher": _reg_val(sub, "Publisher"),
                            }
                    except OSError:
                        continue
        except OSError:
            continue
    return result


def _reg_val(key, name: str) -> str:
    try:
        import winreg
        val, _ = winreg.QueryValueEx(key, name)
        return str(val)
    except (OSError, FileNotFoundError):
        return ""


class SoftwareMonitor:
    """Polls registry for newly installed software."""

    def __init__(self, on_new: Optional[Callable[[SoftwareEvent], None]] = None):
        self.on_new = on_new
        self._known: set[str] = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._known = set(_list_installed().keys())
        log.info("Software monitor started, %d programs known", len(self._known))
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="soft", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def get_installed_count(self) -> int:
        return len(self._known)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(SCAN_INTERVAL)
            if self._stop.is_set():
                break
            try:
                current = _list_installed()
                new_names = set(current.keys()) - self._known
                for name in sorted(new_names):
                    info = current[name]
                    ev = SoftwareEvent(
                        ts=datetime.utcnow(),
                        name=info["name"],
                        version=info["version"],
                        publisher=info["publisher"],
                    )
                    log.info("New software detected: %s %s", ev.name, ev.version)
                    if self.on_new:
                        try:
                            self.on_new(ev)
                        except Exception:
                            log.exception("Software callback error")
                self._known = set(current.keys())
            except Exception:
                log.exception("Software scan error")
