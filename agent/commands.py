"""Handlers for remote commands queued by admin."""
from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import threading
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None

from . import blocker

log = logging.getLogger("monitor.agent.commands")


def _show_message(text: str, title: str = "Сообщение администратора") -> None:
    """Show a non-modal message box without blocking the agent."""
    if sys.platform != "win32":
        log.info("[message] %s: %s", title, text)
        return
    MB_OK = 0x0
    MB_ICONINFO = 0x40
    MB_SETFOREGROUND = 0x10000
    MB_TOPMOST = 0x40000
    flags = MB_OK | MB_ICONINFO | MB_SETFOREGROUND | MB_TOPMOST

    def _worker():
        try:
            ctypes.windll.user32.MessageBoxW(0, str(text), str(title), flags)
        except Exception as exc:  # noqa: BLE001
            log.warning("MessageBox failed: %s", exc)

    threading.Thread(target=_worker, daemon=True).start()


def _lock() -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.LockWorkStation()


def _logoff() -> None:
    if sys.platform == "win32":
        subprocess.Popen(["shutdown", "/l"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _shutdown(seconds: int = 30) -> None:
    if sys.platform == "win32":
        subprocess.Popen(
            ["shutdown", "/s", "/t", str(int(seconds))],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def _restart(seconds: int = 30) -> None:
    if sys.platform == "win32":
        subprocess.Popen(
            ["shutdown", "/r", "/t", str(int(seconds))],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def _kill(process_name: str) -> str:
    killed = blocker.kill_blocked([process_name])
    return f"killed {len(killed)} processes: {killed}"


class CommandRouter:
    """Routes server commands to handler functions.

    `state` is the AgentState (mutable) so set_interval can ask the main loop to refresh.
    `take_screenshot_now` is a callback supplied by main to trigger immediate capture.
    """

    def __init__(self, state, take_screenshot_now):
        self.state = state
        self.take_screenshot_now = take_screenshot_now

    def handle(self, cmd: dict[str, Any]) -> tuple[str, str]:
        ctype = cmd.get("type", "")
        payload = cmd.get("payload") or {}
        try:
            if ctype == "lock":
                _lock()
                return "done", "workstation locked"
            if ctype == "logoff":
                _logoff()
                return "done", "logoff initiated"
            if ctype == "shutdown":
                _shutdown(int(payload.get("seconds", 30)))
                return "done", "shutdown scheduled"
            if ctype == "restart":
                _restart(int(payload.get("seconds", 30)))
                return "done", "restart scheduled"
            if ctype == "message":
                text = str(payload.get("text", ""))
                title = str(payload.get("title", "Сообщение"))
                _show_message(text, title)
                return "done", "message shown"
            if ctype == "kill":
                proc = str(payload.get("process", "")).strip()
                if not proc:
                    return "failed", "missing process"
                return "done", _kill(proc)
            if ctype == "set_interval":
                seconds = max(0, int(payload.get("seconds", 0)))
                with self.state.lock:
                    self.state.runtime.screenshot_interval = seconds
                return "done", f"screenshot interval set to {seconds}s"
            if ctype == "reload_rules":
                self.state.request_heartbeat.set()
                return "done", "heartbeat requested"
            if ctype == "screenshot_now":
                ok = self.take_screenshot_now()
                return ("done" if ok else "failed"), "screenshot uploaded" if ok else "no screenshot"
            return "failed", f"unknown command: {ctype}"
        except Exception as exc:  # noqa: BLE001
            log.exception("Command %s failed", ctype)
            return "failed", str(exc)[:500]
