"""Clipboard text monitoring (Windows).

Monitors clipboard changes and stores last N text entries.
Does NOT store passwords — any string that looks like a password
(short, mixed-case + digits + symbols) is replaced with "[filtered]".
"""
from __future__ import annotations

import ctypes
import logging
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

log = logging.getLogger("monitor.agent.clipboard")

MAX_TEXT_LEN = 500
HISTORY_SIZE = 50


@dataclass
class ClipEntry:
    ts: datetime
    text: str  # truncated, filtered


def _looks_like_password(s: str) -> bool:
    """Heuristic: short string with mixed case, digits, and symbols."""
    s = s.strip()
    if len(s) < 6 or len(s) > 64:
        return False
    has_upper = bool(re.search(r"[A-Z]", s))
    has_lower = bool(re.search(r"[a-z]", s))
    has_digit = bool(re.search(r"\d", s))
    has_symbol = bool(re.search(r"[^A-Za-z0-9\s]", s))
    # If 3+ out of 4 categories and no spaces → likely a password
    score = sum([has_upper, has_lower, has_digit, has_symbol])
    if score >= 3 and " " not in s:
        return True
    return False


def _get_clipboard_text() -> Optional[str]:
    if sys.platform != "win32":
        return None
    result: list[Optional[str]] = [None]
    def _read():
        try:
            ctypes.windll.user32.OpenClipboard(0)
            try:
                CF_UNICODETEXT = 13
                if ctypes.windll.user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    handle = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
                    if handle:
                        ptr = ctypes.windll.kernel32.GlobalLock(handle)
                        if ptr:
                            try:
                                result[0] = ctypes.wstring_at(ptr)
                            finally:
                                ctypes.windll.kernel32.GlobalUnlock(handle)
            finally:
                ctypes.windll.user32.CloseClipboard()
        except Exception:
            pass
    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout=1.0)
    return result[0]


class ClipboardMonitor:
    """Polls clipboard every `poll_seconds`, notifies on change."""

    def __init__(self, on_change: Optional[Callable[[ClipEntry], None]] = None, poll_seconds: float = 2):
        self.on_change = on_change
        self.poll_seconds = poll_seconds
        self.history: deque[ClipEntry] = deque(maxlen=HISTORY_SIZE)
        self._last_text: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._last_text = _get_clipboard_text()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="clip", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def get_last(self, n: int = 10) -> list[ClipEntry]:
        return list(self.history)[-n:]

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                text = _get_clipboard_text()
                if text is not None and text != self._last_text:
                    self._last_text = text
                    display = text[:MAX_TEXT_LEN]
                    if _looks_like_password(text):
                        display = "[filtered — possible password]"
                    entry = ClipEntry(ts=datetime.now(timezone.utc), text=display)
                    self.history.append(entry)
                    if self.on_change:
                        try:
                            self.on_change(entry)
                        except Exception:
                            log.exception("Clipboard callback error")
            except Exception:
                log.exception("Clipboard poll error")
            self._stop.wait(self.poll_seconds)
