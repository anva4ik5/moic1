"""Process blocker: kills processes whose exe name is in the block list."""
from __future__ import annotations

import logging
import os
from typing import Iterable

try:
    import psutil
except ImportError:
    psutil = None

log = logging.getLogger("monitor.agent.blocker")

# Never kill these even if listed - prevents foot-guns/locking out the OS.
SAFE_NEVER_KILL = {
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "fontdrvhost.exe",
    "dwm.exe",
    "svchost.exe",
    "explorer.exe",
    "ctfmon.exe",
    "taskhostw.exe",
    "runtimebroker.exe",
    "searchhost.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "sihost.exe",
    "applicationframehost.exe",
    "python.exe",
    "pythonw.exe",
}


def kill_blocked(blocked: Iterable[str]) -> list[tuple[str, int]]:
    """Returns list of (process_name, pid) that were killed."""
    if not psutil:
        log.warning("psutil not available - cannot kill processes")
        return []
    targets = {p.lower().strip() for p in blocked if p}
    if not targets:
        return []
    own_pid = os.getpid()
    killed: list[tuple[str, int]] = []
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if not name or name in SAFE_NEVER_KILL or name not in targets:
                continue
            if proc.info["pid"] == own_pid:
                continue
            proc.kill()
            killed.append((name, proc.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception as exc:  # noqa: BLE001
            log.debug("kill failed for %s: %s", proc, exc)
    if killed:
        log.info("Blocked processes killed: %s", killed)
    return killed
