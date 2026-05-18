"""Autonomous LAN discovery and agent deployment.

On startup (if enabled), scans the local /24 subnet for online PCs,
attempts to deploy the agent via Windows admin shares (\\PC\C$),
and creates a scheduled task to auto-start the agent on each target.

This requires:
  - The agent running under an account with admin access to target PCs
    (domain admin, or local admin with matching credentials)
  - Admin shares (C$) enabled on target PCs (default on domain PCs)
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("monitor.agent.autodeploy")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
REMOTE_DIR = r"C:\ProgramData\MonitorAgent"
TASK_NAME = "MonitorAgentAutoStart"


@dataclass
class DeployResult:
    pcs_found: int = 0
    pcs_deployed: int = 0
    pcs_skipped: int = 0
    pcs_failed: int = 0
    details: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Найдено ПК: {self.pcs_found}\n"
            f"Развёрнуто: {self.pcs_deployed}\n"
            f"Пропущено (уже есть): {self.pcs_skipped}\n"
            f"Ошибок: {self.pcs_failed}"
        )


def get_local_ip() -> Optional[str]:
    """Get local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def get_subnet() -> Optional[str]:
    """Get /24 subnet prefix (e.g. '192.168.1')."""
    ip = get_local_ip()
    if ip:
        parts = ip.split(".")
        return f"{parts[0]}.{parts[1]}.{parts[2]}"
    return None


def scan_network(subnet: Optional[str] = None, timeout: int = 25) -> list[dict]:
    """Scan local /24 subnet for online PCs. Returns list of {ip, host}."""
    if not subnet:
        subnet = get_subnet()
    if not subnet:
        log.warning("Cannot detect local subnet")
        return []

    my_name = socket.gethostname().lower()
    results = []

    def ping(ip: str):
        try:
            r = subprocess.run(
                ["ping", "-n", "1", "-w", "500", ip],
                capture_output=True, creationflags=CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                try:
                    host = socket.gethostbyaddr(ip)[0]
                except Exception:
                    host = ip
                return {"ip": ip, "host": host}
        except Exception:
            pass
        return None

    log.info("Scanning subnet %s.0/24...", subnet)
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        futures = {pool.submit(ping, f"{subnet}.{i}"): i for i in range(1, 255)}
        try:
            for f in concurrent.futures.as_completed(futures, timeout=timeout):
                try:
                    r = f.result()
                    if r and r["host"].lower().split(".")[0] != my_name:
                        results.append(r)
                except Exception:
                    pass
        except concurrent.futures.TimeoutError:
            log.warning("Network scan timed out after %ds", timeout)

    log.info("Found %d PCs on network", len(results))
    return sorted(results, key=lambda x: x["ip"])


def get_agent_exe_path() -> Optional[str]:
    """Get path to the agent executable (only works when frozen)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return None


def _is_agent_installed(ip: str) -> bool:
    """Check if agent is already installed on remote PC."""
    share = f"\\\\{ip}\\C$"
    remote_exe = share + REMOTE_DIR.replace("C:", "") + "\\MonitorAgent.exe"
    try:
        return os.path.exists(remote_exe)
    except Exception:
        return False


def _has_admin_share(ip: str) -> bool:
    """Check if we can access admin share on remote PC."""
    share = f"\\\\{ip}\\C$"
    try:
        return os.path.exists(share)
    except Exception:
        return False


def deploy_to_pc(ip: str, exe_path: str) -> tuple[bool, str]:
    """Deploy agent to a single remote PC.
    
    Returns (success, message).
    """
    hostname = ip
    try:
        hostname = socket.gethostbyaddr(ip)[0].split(".")[0]
    except Exception:
        pass

    # Check admin share access
    share = f"\\\\{ip}\\C$"
    if not _has_admin_share(ip):
        return False, f"{hostname}: нет доступа к {share}"

    # Check if already installed
    if _is_agent_installed(ip):
        return None, f"{hostname}: агент уже установлен"  # None = skipped

    # Create remote directory
    remote_dir = share + REMOTE_DIR.replace("C:", "")
    try:
        os.makedirs(remote_dir, exist_ok=True)
    except Exception as exc:
        return False, f"{hostname}: не могу создать папку — {exc}"

    # Copy EXE
    remote_exe = os.path.join(remote_dir, "MonitorAgent.exe")
    try:
        shutil.copy2(exe_path, remote_exe)
    except Exception as exc:
        return False, f"{hostname}: ошибка копирования — {exc}"

    # Create scheduled task remotely
    remote_exe_path = os.path.join(REMOTE_DIR, "MonitorAgent.exe")
    try:
        r = subprocess.run(
            [
                "schtasks", "/Create", "/S", ip,
                "/TN", TASK_NAME,
                "/TR", f'"{remote_exe_path}"',
                "/SC", "ONLOGON", "/RL", "HIGHEST", "/F",
            ],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        if r.returncode != 0:
            log.warning("schtasks failed for %s: %s", ip, r.stderr)
    except Exception as exc:
        log.warning("Task creation failed for %s: %s", ip, exc)

    # Try to start agent now
    try:
        subprocess.run(
            ["schtasks", "/Run", "/S", ip, "/TN", TASK_NAME],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass

    return True, f"{hostname}: развёрнут успешно"


def auto_deploy(
    on_progress: Optional[Callable[[str], None]] = None,
) -> DeployResult:
    """Full autonomous deploy: scan network → deploy to all found PCs.
    
    Only works when running as a frozen EXE.
    """
    result = DeployResult()

    exe_path = get_agent_exe_path()
    if not exe_path:
        log.info("Auto-deploy skipped: not running as frozen EXE")
        if on_progress:
            on_progress("Пропуск: агент запущен из исходников, не EXE")
        return result

    if on_progress:
        on_progress("Сканирую локальную сеть...")

    pcs = scan_network()
    result.pcs_found = len(pcs)

    if not pcs:
        if on_progress:
            on_progress("Других ПК не найдено в сети")
        return result

    if on_progress:
        on_progress(f"Найдено {len(pcs)} ПК. Начинаю развёртывание...")

    for pc in pcs:
        ip = pc["ip"]
        host = pc["host"]
        if on_progress:
            on_progress(f"→ {host} ({ip})...")

        success, msg = deploy_to_pc(ip, exe_path)
        result.details.append(msg)

        if success is True:
            result.pcs_deployed += 1
            log.info("Deployed to %s", host)
        elif success is None:
            result.pcs_skipped += 1
        else:
            result.pcs_failed += 1
            log.warning("Deploy failed: %s", msg)

        if on_progress:
            on_progress(msg)

    log.info("Auto-deploy complete: %s", result.summary())
    return result


def auto_deploy_background(
    on_complete: Optional[Callable[[DeployResult], None]] = None,
) -> threading.Thread:
    """Run auto_deploy in background thread. Returns the thread."""
    def _run():
        try:
            result = auto_deploy()
            if on_complete:
                on_complete(result)
        except Exception:
            log.exception("Auto-deploy background error")

    t = threading.Thread(target=_run, name="auto_deploy", daemon=True)
    t.start()
    return t
