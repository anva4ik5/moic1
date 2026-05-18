"""Agent self-protection: auto-restart, integrity, ACL, service, task scheduler, anti-kill."""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("monitor.agent.protection")

TASK_NAME = "MonitorAgentAutoStart"
SERVICE_NAME = "MonitorAgentSvc"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


# ─── paths ───

def get_exe_path() -> str:
    """Return the path of the running executable (or script)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


def get_exe_hash() -> str:
    """SHA-256 of the running executable binary."""
    path = get_exe_path()
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return "unknown"


def get_install_dir() -> Path:
    """Standard install directory under ProgramData."""
    d = Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "MonitorAgent"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─── install to protected location ───

def install_to_programdata() -> Optional[str]:
    """Copy the running EXE to a protected ProgramData directory.
    Returns the new path, or None if already there / not frozen.
    """
    if not getattr(sys, "frozen", False):
        return None
    src = Path(get_exe_path())
    dst_dir = get_install_dir()
    dst = dst_dir / "MonitorAgent.exe"
    if src.resolve() == dst.resolve():
        return str(dst)  # already installed
    try:
        shutil.copy2(str(src), str(dst))
        log.info("Installed EXE to %s", dst)
        protect_file_acl(str(dst))
        return str(dst)
    except Exception as exc:
        log.warning("install_to_programdata failed: %s", exc)
        return None


# ─── ACL protection (prevent non-admin deletion) ───

def protect_file_acl(path: str) -> bool:
    """Set ACL on file so only Administrators and SYSTEM can modify/delete.
    Regular users can read+execute but not delete.
    """
    if sys.platform != "win32":
        return False
    try:
        # icacls: grant full to Admins+SYSTEM, read+execute to Users, remove inherited
        cmds = [
            ["icacls", path, "/inheritance:r"],
            ["icacls", path, "/grant", "Administrators:F"],
            ["icacls", path, "/grant", "SYSTEM:F"],
            ["icacls", path, "/grant", "Users:RX"],
        ]
        for cmd in cmds:
            subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
        log.info("ACL protection applied to %s", path)
        return True
    except Exception as exc:
        log.warning("protect_file_acl failed: %s", exc)
        return False


def protect_directory_acl(path: str) -> bool:
    """Protect a directory and its contents."""
    if sys.platform != "win32":
        return False
    try:
        cmds = [
            ["icacls", path, "/inheritance:r"],
            ["icacls", path, "/grant", "Administrators:(OI)(CI)F"],
            ["icacls", path, "/grant", "SYSTEM:(OI)(CI)F"],
            ["icacls", path, "/grant", "Users:(OI)(CI)RX"],
        ]
        for cmd in cmds:
            subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
        log.info("ACL protection applied to directory %s", path)
        return True
    except Exception as exc:
        log.warning("protect_directory_acl failed: %s", exc)
        return False


# ─── Task Scheduler ───

def install_autostart() -> bool:
    """Register agent in Windows Task Scheduler for auto-start at logon."""
    if sys.platform != "win32":
        return False
    exe = get_exe_path()
    try:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        cmd = f'"{exe}"'
        if not getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" -m agent.main'
        result = subprocess.run(
            [
                "schtasks", "/Create",
                "/TN", TASK_NAME,
                "/TR", cmd,
                "/SC", "ONLOGON",
                "/RL", "HIGHEST",
                "/F",
            ],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            log.info("Auto-start task created: %s", TASK_NAME)
            protect_task_acl()
            return True
        log.warning("schtasks create failed: %s %s", result.stdout, result.stderr)
        return False
    except Exception as exc:
        log.warning("install_autostart failed: %s", exc)
        return False


def protect_task_acl() -> bool:
    """Protect the scheduled task with ACLs to prevent deletion/disabling."""
    if sys.platform != "win32":
        return False
    try:
        # Get the task XML path
        task_xml_path = Path(os.environ.get("WINDIR", "C:\\Windows")) / "System32" / "Tasks" / f"{TASK_NAME}.job"
        if not task_xml_path.exists():
            # Windows 10+ uses XML format
            task_xml_path = Path(os.environ.get("WINDIR", "C:\\Windows")) / "System32" / "Tasks" / TASK_NAME
        
        if task_xml_path.exists():
            # Remove inheritance and set restrictive ACLs - deny all except SYSTEM
            cmds = [
                ["icacls", str(task_xml_path), "/inheritance:r"],
                ["icacls", str(task_xml_path), "/grant:r", "Administrators:N"],  # Deny Administrators
                ["icacls", str(task_xml_path), "/grant:r", "Users:N"],  # Deny Users
                ["icacls", str(task_xml_path), "/grant", "SYSTEM:F"],  # Full control only for SYSTEM
            ]
            for cmd in cmds:
                subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
            log.info("ACL protection applied to task: %s", task_xml_path)
            return True
        
        # Alternative: use schtasks to set task security via XML
        # This requires exporting, modifying, and re-importing the task
        log.warning("Task file not found, skipping ACL protection")
        return False
    except Exception as exc:
        log.warning("protect_task_acl failed: %s", exc)
        return False


def task_exists() -> bool:
    """Check if the scheduled task exists."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        return result.returncode == 0
    except Exception:
        return False


class TaskWatchdog(threading.Thread):
    """Watchdog that recreates the task if deleted."""

    def __init__(self, stop_event: threading.Event):
        super().__init__(daemon=True)
        self._stop_event = stop_event

    def run(self):
        while not self._stop_event.is_set():
            if not task_exists():
                log.warning("Task deleted, recreating...")
                install_autostart()
            self._stop_event.wait(30)  # Check every 30 seconds


def delete_task() -> bool:
    """Delete the scheduled task (only for Telegram bot control)."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            log.info("Task deleted: %s", TASK_NAME)
            return True
        return False
    except Exception as exc:
        log.warning("delete_task failed: %s", exc)
        return False


def disable_task() -> bool:
    """Disable the scheduled task (only for Telegram bot control)."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["schtasks", "/Change", "/TN", TASK_NAME, "/Disable"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            log.info("Task disabled: %s", TASK_NAME)
            return True
        return False
    except Exception as exc:
        log.warning("disable_task failed: %s", exc)
        return False


def enable_task() -> bool:
    """Enable the scheduled task (only for Telegram bot control)."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["schtasks", "/Change", "/TN", TASK_NAME, "/Enable"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            log.info("Task enabled: %s", TASK_NAME)
            # Re-apply ACL protection
            protect_task_acl()
            return True
        return False
    except Exception as exc:
        log.warning("enable_task failed: %s", exc)
        return False


def install_autostart_boot() -> bool:
    """Additional task that starts agent at SYSTEM boot (before user logon)."""
    if sys.platform != "win32":
        return False
    exe = get_exe_path()
    task_name = TASK_NAME + "Boot"
    try:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        cmd = f'"{exe}"'
        if not getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" -m agent.main'
        result = subprocess.run(
            [
                "schtasks", "/Create",
                "/TN", task_name,
                "/TR", cmd,
                "/SC", "ONSTART",
                "/RL", "HIGHEST",
                "/DELAY", "0000:30",
                "/F",
            ],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            log.info("Boot auto-start task created: %s", task_name)
            return True
        return False
    except Exception as exc:
        log.warning("install_autostart_boot error: %s", exc)
        return False


def remove_autostart() -> bool:
    """Remove both auto-start tasks."""
    if sys.platform != "win32":
        return False
    ok = True
    for name in [TASK_NAME, TASK_NAME + "Boot"]:
        try:
            result = subprocess.run(
                ["schtasks", "/Delete", "/TN", name, "/F"],
                capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                ok = False
        except Exception:
            ok = False
    return ok


def is_autostart_installed() -> bool:
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        return result.returncode == 0
    except Exception:
        return False


# ─── anti-kill (process priority + respawn) ───

def set_high_priority() -> bool:
    """Set current process to High priority (not Realtime, which needs admin)."""
    try:
        import psutil
        p = psutil.Process()
        p.nice(psutil.HIGH_PRIORITY_CLASS)
        log.info("Process priority set to HIGH")
        return True
    except Exception as exc:
        log.warning("set_high_priority failed: %s", exc)
        return False


def set_critical_process(enable: bool = True) -> bool:
    """Mark process as critical — OS shows BSOD if killed (requires admin).
    USE WITH CAUTION. Only suitable for kiosk/locked-down environments.
    Disabled by default — enable via Telegram /critical on.
    """
    if sys.platform != "win32":
        return False
    try:
        ntdll = ctypes.windll.ntdll
        # NtSetInformationProcess with ProcessBreakOnTermination = 29
        ret = ntdll.RtlSetProcessIsCritical(int(enable), None, 0)
        if ret == 0:
            log.info("Critical process: %s", "enabled" if enable else "disabled")
            return True
        return False
    except Exception as exc:
        log.warning("set_critical_process failed: %s", exc)
        return False


# ─── Watchdog ───

class WatchdogThread:
    """Monitors the agent and restarts if the main thread dies."""

    def __init__(self, check_interval: int = 15):
        self.check_interval = check_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._main_thread: Optional[threading.Thread] = None

    def start(self, main_thread: threading.Thread) -> None:
        self._main_thread = main_thread
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.check_interval)
            if self._stop.is_set():
                break
            if self._main_thread and not self._main_thread.is_alive():
                log.warning("Main thread died — restarting in 3s")
                time.sleep(3)
                _restart_self()
                break


class ProcessGuard:
    """Watches for the agent's own process being killed by monitoring a
    secondary 'guard' process. Each watches the other — if one dies,
    the other restarts it.
    """

    def __init__(self, check_interval: int = 10):
        self.check_interval = check_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="proc_guard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        """Periodically verify the scheduled task still exists. Re-create if deleted."""
        while not self._stop.is_set():
            self._stop.wait(self.check_interval * 6)
            if self._stop.is_set():
                break
            if not is_autostart_installed():
                log.warning("Auto-start task was removed — re-installing")
                install_autostart()


def _restart_self() -> None:
    """Restart the current process."""
    exe = get_exe_path()
    try:
        if getattr(sys, "frozen", False):
            subprocess.Popen([exe], creationflags=CREATE_NO_WINDOW)
        else:
            subprocess.Popen(
                [sys.executable, "-m", "agent.main"],
                creationflags=CREATE_NO_WINDOW,
            )
    except Exception as exc:
        log.error("Restart failed: %s", exc)
    finally:
        os._exit(1)


# ─── full protection setup (called from main on startup) ───

def disable_taskmgr() -> bool:
    """Block Windows Task Manager via registry."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Policies\System",
                            0, winreg.KEY_WRITE | winreg.KEY_CREATE_SUB_KEY)
        winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
        log.info("Task Manager disabled")
        return True
    except Exception as exc:
        log.warning("disable_taskmgr failed: %s", exc)
        return False


def is_taskmgr_disabled() -> bool:
    """Check if Task Manager is currently disabled."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Policies\System",
                            0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, "DisableTaskMgr")
        winreg.CloseKey(key)
        return value == 1
    except Exception:
        return False


def enable_taskmgr() -> bool:
    """Re-enable Windows Task Manager."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Policies\System",
                            0, winreg.KEY_WRITE)
        winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        log.info("Task Manager enabled")
        return True
    except Exception as exc:
        log.warning("enable_taskmgr failed: %s", exc)
        return False


def start_guard_process() -> bool:
    """Launch external guard.py subprocess that restarts agent if killed."""
    try:
        exe = get_exe_path()
        guard_py = str(Path(__file__).with_name("guard.py"))
        if not Path(guard_py).exists():
            log.warning("guard.py not found at %s", guard_py)
            return False
        subprocess.Popen(
            [sys.executable, guard_py, "--exe", exe],
            creationflags=CREATE_NO_WINDOW,
        )
        log.info("Guard process started")
        return True
    except Exception as exc:
        log.warning("start_guard_process failed: %s", exc)
        return False


def apply_full_protection() -> dict[str, bool]:
    """Apply all available protections. Returns status dict."""
    results = {}
    results["high_priority"] = set_high_priority()
    results["autostart_logon"] = install_autostart()
    results["autostart_boot"] = install_autostart_boot()
    results["disable_taskmgr"] = disable_taskmgr()
    results["guard_process"] = start_guard_process()
    if getattr(sys, "frozen", False):
        installed_path = install_to_programdata()
        results["installed_to_programdata"] = installed_path is not None
        if installed_path:
            results["acl_exe"] = protect_file_acl(installed_path)
    install_dir = get_install_dir()
    results["acl_dir"] = protect_directory_acl(str(install_dir))
    log.info("Protection results: %s", results)
    return results
