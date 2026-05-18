"""Active window tracker (Windows-focused, falls back gracefully)."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None

log = logging.getLogger("monitor.agent.tracker")

try:
    import win32gui  # type: ignore
    import win32process  # type: ignore
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


@dataclass
class WindowInfo:
    title: str
    process: str  # exe name e.g. "chrome.exe"
    app: str      # human-friendly name
    pid: int


def _app_name_from_process(process_name: str) -> str:
    """Map exe -> friendly name (best effort)."""
    name = process_name.lower()
    mapping = {
        "chrome.exe": "Google Chrome",
        "msedge.exe": "Microsoft Edge",
        "firefox.exe": "Mozilla Firefox",
        "explorer.exe": "File Explorer",
        "code.exe": "VS Code",
        "windsurf.exe": "Windsurf",
        "devenv.exe": "Visual Studio",
        "pycharm64.exe": "PyCharm",
        "discord.exe": "Discord",
        "telegram.exe": "Telegram",
        "steam.exe": "Steam",
        "spotify.exe": "Spotify",
        "winword.exe": "Microsoft Word",
        "excel.exe": "Microsoft Excel",
        "powerpnt.exe": "PowerPoint",
        "outlook.exe": "Outlook",
        "notepad.exe": "Notepad",
    }
    return mapping.get(name, process_name)


def get_active_window() -> Optional[WindowInfo]:
    if not HAS_WIN32:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0:
            return None
        try:
            if psutil:
                proc = psutil.Process(pid)
                process = proc.name() or ""
            else:
                process = ""
        except Exception:
            process = ""
        return WindowInfo(
            title=title,
            process=process,
            app=_app_name_from_process(process),
            pid=pid,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("get_active_window error: %s", exc)
        return None


class SessionAggregator:
    """Aggregates consecutive seconds on the same window into a single event."""

    def __init__(self, flush_seconds: int = 30):
        self.flush_seconds = flush_seconds
        self._current: Optional[WindowInfo] = None
        self._started_at: float = 0.0
        self._buffer: list[dict] = []
        self._last_flush: float = time.time()

    def tick(self, info: Optional[WindowInfo], idle: bool) -> None:
        now = time.time()
        if idle or info is None:
            self._close_current(now)
            return
        if (
            self._current is None
            or info.process != self._current.process
            or info.title != self._current.title
        ):
            self._close_current(now)
            self._current = info
            self._started_at = now

    def _close_current(self, now: float) -> None:
        if self._current is None:
            return
        duration = int(now - self._started_at)
        if duration >= 1:
            self._buffer.append(
                {
                    "type": "active_window",
                    "app": self._current.app,
                    "title": self._current.title[:500],
                    "process": self._current.process,
                    "duration": duration,
                }
            )
        self._current = None
        self._started_at = 0.0

    def take_batch(self, force: bool = False) -> list[dict]:
        now = time.time()
        if not force and (now - self._last_flush) < self.flush_seconds:
            return []
        # close any in-progress session so its time so far is reported, then re-open
        in_progress = self._current
        in_progress_started = self._started_at
        self._close_current(now)
        batch = self._buffer
        self._buffer = []
        self._last_flush = now
        if in_progress is not None:
            self._current = in_progress
            self._started_at = now  # restart timer from now
        return batch
