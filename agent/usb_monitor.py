"""USB device monitoring via WMI (Windows only).

Detects when USB storage devices are connected/disconnected and reports
the device name, drive letter, and volume serial.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("monitor.agent.usb")


@dataclass
class USBEvent:
    action: str  # "connected" | "disconnected"
    device_name: str
    drive_letter: str
    serial: str
    size_gb: float


_usb_error_logged = False

def get_usb_drives() -> list[dict]:
    """Return a list of currently attached USB removable drives."""
    global _usb_error_logged
    if sys.platform != "win32":
        return []
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        import wmi  # type: ignore
        c = wmi.WMI()
        drives = []
        for disk in c.Win32_DiskDrive():
            if "USB" not in (disk.InterfaceType or ""):
                continue
            for part in disk.associators("Win32_DiskDriveToDiskPartition"):
                for logical in part.associators("Win32_LogicalDiskToPartition"):
                    drives.append({
                        "device": disk.Model or "USB Drive",
                        "letter": logical.DeviceID or "?",
                        "serial": str(disk.SerialNumber or "").strip(),
                        "size_gb": round((int(disk.Size or 0)) / (1024**3), 2),
                    })
        _usb_error_logged = False
        return drives
    except Exception as exc:
        if not _usb_error_logged:
            log.warning("get_usb_drives error (suppressing further): %s", exc)
            _usb_error_logged = True
        return []
    finally:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass


class USBMonitor:
    """Polls USB drives every `poll_seconds` and calls `on_event` on change."""

    def __init__(self, on_event: Callable[[USBEvent], None], poll_seconds: int = 5):
        self.on_event = on_event
        self.poll_seconds = poll_seconds
        self._known: dict[str, dict] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        # seed known set
        for d in get_usb_drives():
            self._known[d["letter"]] = d
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="usb", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                current = {d["letter"]: d for d in get_usb_drives()}
                # new
                for letter, d in current.items():
                    if letter not in self._known:
                        ev = USBEvent("connected", d["device"], letter, d["serial"], d["size_gb"])
                        log.info("USB connected: %s (%s)", d["device"], letter)
                        try:
                            self.on_event(ev)
                        except Exception:
                            log.exception("USB callback error")
                # removed
                for letter, d in self._known.items():
                    if letter not in current:
                        ev = USBEvent("disconnected", d["device"], letter, d["serial"], d["size_gb"])
                        log.info("USB disconnected: %s (%s)", d["device"], letter)
                        try:
                            self.on_event(ev)
                        except Exception:
                            log.exception("USB callback error")
                self._known = current
            except Exception:
                log.exception("USB poll error")
            self._stop.wait(self.poll_seconds)
