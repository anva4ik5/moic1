"""Secure file/folder deletion with overwrite (DLP emergency wipe).

Implements DoD 5220.22-M style overwrite:
  Pass 1: write 0x00
  Pass 2: write 0xFF
  Pass 3: write random
  Then delete.

This is a standard enterprise DLP feature for protecting sensitive data
when a breach is detected. The wipe list is configurable — admin defines
which folders/files contain sensitive data.

Usage via Telegram:
  /wipeadd C:\\SensitiveData        — add folder to wipe list
  /wiperemove C:\\SensitiveData     — remove from list
  /wipelist                         — show wipe list
  /wipe C:\\SensitiveData           — wipe specific path NOW
  /wipe all                         — wipe ALL paths in the wipe list
  /emergency                        — FULL emergency: wipe all + lock screen + disable USB + firewall
"""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import stat
import threading
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("monitor.agent.wipe")

OVERWRITE_PASSES = 3
BLOCK_SIZE = 65536  # 64 KB


class WipeResult:
    def __init__(self):
        self.files_wiped: int = 0
        self.files_failed: int = 0
        self.dirs_removed: int = 0
        self.total_bytes: int = 0
        self.errors: list[str] = []
        self.elapsed: float = 0.0

    def summary(self) -> str:
        mb = self.total_bytes / (1024 * 1024)
        return (
            f"Файлов удалено: {self.files_wiped}\n"
            f"Ошибок: {self.files_failed}\n"
            f"Папок удалено: {self.dirs_removed}\n"
            f"Данных перезаписано: {mb:.1f} MB\n"
            f"Время: {self.elapsed:.1f} сек"
        )


def _make_writable(path: str) -> None:
    """Remove read-only flag."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass


def secure_overwrite_file(filepath: str, passes: int = OVERWRITE_PASSES) -> int:
    """Overwrite a single file with zeros, ones, random, then delete.
    Returns the file size in bytes.
    """
    filepath = os.path.abspath(filepath)
    if not os.path.isfile(filepath):
        return 0

    _make_writable(filepath)
    file_size = os.path.getsize(filepath)

    try:
        for pass_num in range(passes):
            with open(filepath, "r+b") as f:
                written = 0
                while written < file_size:
                    chunk_size = min(BLOCK_SIZE, file_size - written)
                    if pass_num == 0:
                        data = b"\x00" * chunk_size
                    elif pass_num == 1:
                        data = b"\xFF" * chunk_size
                    else:
                        data = secrets.token_bytes(chunk_size)
                    f.write(data)
                    written += chunk_size
                f.flush()
                os.fsync(f.fileno())
    except Exception as exc:
        log.warning("Overwrite error for %s: %s", filepath, exc)

    # rename to random name before deleting (anti-forensics)
    try:
        directory = os.path.dirname(filepath)
        random_name = os.path.join(directory, secrets.token_hex(16))
        os.rename(filepath, random_name)
        os.remove(random_name)
    except Exception:
        try:
            os.remove(filepath)
        except Exception as exc:
            log.warning("Delete failed for %s: %s", filepath, exc)
            raise

    return file_size


def secure_wipe_path(
    target: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> WipeResult:
    """Securely wipe a file or directory tree.

    Args:
        target: file or directory path
        on_progress: optional callback for progress updates
    """
    result = WipeResult()
    start = time.time()
    target = os.path.abspath(target)

    if not os.path.exists(target):
        result.errors.append(f"Path not found: {target}")
        result.elapsed = time.time() - start
        return result

    if os.path.isfile(target):
        try:
            sz = secure_overwrite_file(target)
            result.files_wiped += 1
            result.total_bytes += sz
            if on_progress:
                on_progress(f"Wiped: {target}")
        except Exception as exc:
            result.files_failed += 1
            result.errors.append(f"{target}: {exc}")
        result.elapsed = time.time() - start
        return result

    # directory — walk bottom-up
    all_files = []
    for root_dir, dirs, files in os.walk(target, topdown=False):
        for fname in files:
            all_files.append(os.path.join(root_dir, fname))

    total = len(all_files)
    for i, fpath in enumerate(all_files):
        try:
            sz = secure_overwrite_file(fpath)
            result.files_wiped += 1
            result.total_bytes += sz
            if on_progress and (i % 50 == 0 or i == total - 1):
                on_progress(f"Wiping: {i+1}/{total} files...")
        except Exception as exc:
            result.files_failed += 1
            result.errors.append(f"{fpath}: {exc}")

    # remove empty directories
    for root_dir, dirs, files in os.walk(target, topdown=False):
        for d in dirs:
            dpath = os.path.join(root_dir, d)
            try:
                os.rmdir(dpath)
                result.dirs_removed += 1
            except Exception:
                pass
    try:
        os.rmdir(target)
        result.dirs_removed += 1
    except Exception:
        # try shutil as fallback
        try:
            shutil.rmtree(target, ignore_errors=True)
            result.dirs_removed += 1
        except Exception:
            pass

    result.elapsed = time.time() - start
    log.info("Wipe complete: %s — %s", target, result.summary())
    return result


def wipe_multiple(
    paths: list[str],
    on_progress: Optional[Callable[[str], None]] = None,
) -> WipeResult:
    """Wipe multiple paths, aggregate results."""
    combined = WipeResult()
    start = time.time()

    for path in paths:
        if on_progress:
            on_progress(f"Starting: {path}")
        r = secure_wipe_path(path, on_progress)
        combined.files_wiped += r.files_wiped
        combined.files_failed += r.files_failed
        combined.dirs_removed += r.dirs_removed
        combined.total_bytes += r.total_bytes
        combined.errors.extend(r.errors)

    combined.elapsed = time.time() - start
    return combined


def wipe_temp_and_caches() -> WipeResult:
    """Wipe common Windows temp/cache locations."""
    paths = []
    for env_var in ["TEMP", "TMP"]:
        p = os.environ.get(env_var)
        if p and os.path.isdir(p):
            paths.append(p)

    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        for sub in ["Temp", r"Microsoft\Windows\INetCache", r"Microsoft\Windows\Explorer\thumbcache*"]:
            p = os.path.join(local, sub)
            if os.path.exists(p):
                paths.append(p)

    return wipe_multiple(paths) if paths else WipeResult()


def wipe_browser_data() -> WipeResult:
    """Wipe browser cache/history for common browsers."""
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    paths = []

    if local:
        chrome = os.path.join(local, "Google", "Chrome", "User Data", "Default", "Cache")
        edge = os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Cache")
        for p in [chrome, edge]:
            if os.path.isdir(p):
                paths.append(p)

    if appdata:
        firefox_profiles = os.path.join(appdata, "Mozilla", "Firefox", "Profiles")
        if os.path.isdir(firefox_profiles):
            for profile in os.listdir(firefox_profiles):
                cache = os.path.join(firefox_profiles, profile, "cache2")
                if os.path.isdir(cache):
                    paths.append(cache)

    return wipe_multiple(paths) if paths else WipeResult()


# ─── FULL NUKE (total wipe including agent self-destruct) ───

def get_user_data_paths() -> list[str]:
    """Collect all standard user data directories."""
    paths = []
    home = os.path.expanduser("~")
    for folder in [
        "Desktop", "Documents", "Downloads", "Pictures",
        "Videos", "Music", "Favorites", "Contacts",
        "Saved Games", "Searches", "Links", "3D Objects",
        ".ssh", ".gnupg", ".config",
    ]:
        p = os.path.join(home, folder)
        if os.path.isdir(p):
            paths.append(p)

    onedrive = os.environ.get("OneDrive", "")
    if onedrive and os.path.isdir(onedrive):
        paths.append(onedrive)

    appdata_dir = os.environ.get("APPDATA", "")
    if appdata_dir:
        recent = os.path.join(appdata_dir, "Microsoft", "Windows", "Recent")
        if os.path.isdir(recent):
            paths.append(recent)

    return paths


def get_all_nuke_paths(extra_paths: list[str] | None = None) -> list[str]:
    """Collect ALL paths for full nuke: user data + temp + browsers + extra."""
    paths = []
    paths.extend(get_user_data_paths())

    for env_var in ["TEMP", "TMP"]:
        p = os.environ.get(env_var)
        if p and os.path.isdir(p):
            paths.append(p)

    local_ad = os.environ.get("LOCALAPPDATA", "")
    if local_ad:
        for sub in ["Temp", "Google", "Microsoft\\Edge"]:
            p = os.path.join(local_ad, sub)
            if os.path.isdir(p):
                paths.append(p)

    appdata_dir = os.environ.get("APPDATA", "")
    if appdata_dir:
        for sub in ["Mozilla", "Telegram Desktop"]:
            p = os.path.join(appdata_dir, sub)
            if os.path.isdir(p):
                paths.append(p)

    if extra_paths:
        for p in extra_paths:
            if os.path.exists(p) and p not in paths:
                paths.append(p)

    return paths


def nuke_and_self_destruct(
    extra_paths: list[str] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> WipeResult:
    """FULL NUKE: wipe all user data, caches, extra paths, then delete the agent.

    After this runs the PC will have:
    - No user data (Desktop, Documents, Downloads, etc. wiped)
    - No browser data
    - No temp files
    - No agent (self-deleted)
    - Windows still boots but is essentially empty

    The admin can then reinstall everything from scratch.
    """
    import subprocess as sp
    CREATE = getattr(sp, "CREATE_NO_WINDOW", 0x08000000)

    all_paths = get_all_nuke_paths(extra_paths)
    if on_progress:
        on_progress(f"Nuke: {len(all_paths)} targets identified")

    # Phase 1: wipe all user/org data
    result = wipe_multiple(all_paths, on_progress)

    # Phase 2: wipe agent config directory
    agent_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "MonitorAgent",
    )
    if os.path.isdir(agent_dir):
        if on_progress:
            on_progress("Wiping agent config data...")
        r = secure_wipe_path(agent_dir)
        result.files_wiped += r.files_wiped
        result.dirs_removed += r.dirs_removed
        result.total_bytes += r.total_bytes

    # Phase 3: wipe ProgramData agent copy
    pd_dir = os.path.join(
        os.environ.get("ProgramData", "C:\\ProgramData"), "MonitorAgent",
    )
    if os.path.isdir(pd_dir):
        if on_progress:
            on_progress("Wiping ProgramData agent copy...")
        try:
            sp.run(
                ["icacls", pd_dir, "/grant", "Everyone:(OI)(CI)F", "/T"],
                capture_output=True, creationflags=CREATE,
            )
        except Exception:
            pass
        r = secure_wipe_path(pd_dir)
        result.files_wiped += r.files_wiped
        result.dirs_removed += r.dirs_removed
        result.total_bytes += r.total_bytes

    # Phase 4: remove scheduled tasks
    if on_progress:
        on_progress("Removing scheduled tasks...")
    for task in ["MonitorAgentAutoStart", "MonitorAgentAutoStartBoot"]:
        try:
            sp.run(["schtasks", "/Delete", "/TN", task, "/F"],
                   capture_output=True, creationflags=CREATE)
        except Exception:
            pass

    # Phase 5: self-delete the running executable
    if on_progress:
        on_progress("Self-destructing agent executable...")
    _schedule_self_delete()

    return result


def _schedule_self_delete() -> None:
    """Schedule deletion of the running EXE after process exits.
    Uses cmd /c ping trick to wait, then deletes the EXE and folder.
    """
    import subprocess as sp
    import sys as _sys

    exe_path = _sys.executable if getattr(_sys, "frozen", False) else None
    if not exe_path:
        return  # running from source — don't delete Python itself

    try:
        parent_dir = os.path.dirname(exe_path)
        cmd = (
            f'cmd /c ping 127.0.0.1 -n 4 > nul & '
            f'del /f /q "{exe_path}" & '
            f'rmdir /s /q "{parent_dir}" 2>nul'
        )
        sp.Popen(
            cmd, shell=True,
            creationflags=getattr(sp, "CREATE_NO_WINDOW", 0x08000000),
            close_fds=True,
        )
        log.info("Self-delete scheduled for: %s", exe_path)
    except Exception as exc:
        log.warning("Self-delete scheduling failed: %s", exc)
