"""External guard process that watches MonitorAgent.exe and restarts it if killed.

Run this as a separate subprocess so it survives the main process termination.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Guard] %(levelname)s: %(message)s",
)
log = logging.getLogger("guard")

# Seconds between checks
CHECK_INTERVAL = 3
# How long without agent before restart
MAX_ABSENT_SECONDS = 5


def _get_agent_pids() -> list[int]:
    """Return PIDs of running MonitorAgent.exe processes (excluding self if same)."""
    pids = []
    try:
        import psutil
        for proc in psutil.process_iter(attrs=["pid", "name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == "monitoragent.exe":
                    pids.append(proc.info["pid"])
            except Exception:
                pass
    except ImportError:
        pass
    return pids


def _start_agent(exe_path: str) -> None:
    """Start the agent executable."""
    try:
        subprocess.Popen(
            [exe_path],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log.info("Agent restarted: %s", exe_path)
    except Exception as exc:
        log.error("Failed to restart agent: %s", exc)


def run_guard(exe_path: str) -> None:
    """Main guard loop."""
    log.info("Guard started for: %s", exe_path)
    absent_since: float | None = None

    while True:
        pids = _get_agent_pids()
        if pids:
            absent_since = None
            log.debug("Agent alive: PIDs %s", pids)
        else:
            now = time.time()
            if absent_since is None:
                absent_since = now
                log.warning("Agent absent — waiting %ds before restart...", MAX_ABSENT_SECONDS)
            elif now - absent_since >= MAX_ABSENT_SECONDS:
                log.error("Agent gone for %ds — RESTARTING", MAX_ABSENT_SECONDS)
                _start_agent(exe_path)
                absent_since = None
        time.sleep(CHECK_INTERVAL)


def run_dual_guard(exe_path: str) -> None:
    """Same as run_guard but also restarts the guard if the agent starts us."""
    run_guard(exe_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor Agent Guard")
    parser.add_argument("--exe", required=True, help="Path to MonitorAgent.exe")
    args = parser.parse_args()
    run_guard(args.exe)
