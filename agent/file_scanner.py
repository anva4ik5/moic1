"""File scanner module for scanning all drives and listing files.

Provides functionality to scan all drives on the system and list files
with detailed information (size, type, path). Useful for DLP and
file inventory purposes.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("monitor.agent.file_scanner")


class ScanResult:
    """Result of a file scan operation."""

    def __init__(self):
        self.total_files: int = 0
        self.total_size: int = 0
        self.by_drive: Dict[str, Dict[str, int]] = {}  # drive -> {extension: count}
        self.large_files: List[Dict[str, Any]] = []  # files > 10 MB
        self.recent_files: List[Dict[str, Any]] = []  # files modified in last 7 days
        self.scan_time: Optional[datetime] = None
        self.error: Optional[str] = None

    def summary(self) -> str:
        """Get a text summary of the scan results."""
        if self.error:
            return f"❌ Ошибка сканирования: {self.error}"

        lines = [
            f"<b>📊 Результаты сканирования файлов</b>\n",
            f"⏰ Время: {self.scan_time.strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"📁 Всего файлов: {self.total_files:,}",
            f"💾 Общий размер: {self.total_size / (1024**3):.2f} GB",
        ]

        # Top drives by file count
        if self.by_drive:
            lines.append("\n<b>📂 По дискам:</b>")
            for drive, exts in sorted(self.by_drive.items(), key=lambda x: sum(x[1].values()), reverse=True)[:5]:
                count = sum(exts.values())
                lines.append(f"• <b>{drive}</b>: {count:,} файлов")

        # Large files
        if self.large_files:
            lines.append(f"\n<b>📦 Крупные файлы (>10 MB):</b> (первые 10)")
            for f in self.large_files[:10]:
                size_mb = f["size"] / (1024 * 1024)
                lines.append(f"• <code>{f['path'][:80]}</code> — {size_mb:.1f} MB")

        # Recent files
        if self.recent_files:
            lines.append(f"\n<b>🆕 Недавно изменённые (7 дней):</b> (первые 15)")
            for f in self.recent_files[:15]:
                lines.append(f"• <code>{f['path'][:80]}</code> — {f['modified'].strftime('%Y-%m-%d %H:%M')}")

        return "\n".join(lines)


class FileScanner:
    """Scanner for all files on all drives."""

    def __init__(self):
        self._scanning = False
        self._stop_event = threading.Event()
        self._result: Optional[ScanResult] = None

    def scan_all_drives(
        self,
        min_size_mb: float = 0,
        max_files: int = 100000,
        include_hidden: bool = False,
    ) -> ScanResult:
        """Scan all drives on the system.

        Args:
            min_size_mb: Minimum file size in MB to include in results
            max_files: Maximum number of files to scan per drive
            include_hidden: Whether to include hidden files

        Returns:
            ScanResult with scan data
        """
        result = ScanResult()
        result.scan_time = datetime.now()

        try:
            # Get all drives
            drives = self._get_drives()
            if not drives:
                result.error = "Не найдены диски"
                return result

            log.info("Starting scan of %d drives: %s", len(drives), drives)

            for drive in drives:
                if self._stop_event.is_set():
                    result.error = "Сканирование прервано"
                    return result

                try:
                    self._scan_drive(
                        drive,
                        result,
                        min_size_mb=min_size_mb,
                        max_files=max_files,
                        include_hidden=include_hidden,
                    )
                except Exception as exc:
                    log.warning("Failed to scan drive %s: %s", drive, exc)
                    continue

            log.info("Scan complete: %d files, %.2f GB", result.total_files, result.total_size / (1024**3))

        except Exception as exc:
            result.error = f"Ошибка сканирования: {exc}"
            log.error("Scan failed: %s", exc)

        return result

    def _get_drives(self) -> List[str]:
        """Get all available drives on Windows."""
        import ctypes

        drives = []
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()

        for i in range(26):
            if bitmask & (1 << i):
                drive_letter = chr(ord("A") + i) + ":\\"
                if os.path.exists(drive_letter):
                    drives.append(drive_letter)

        return drives

    def _scan_drive(
        self,
        drive: str,
        result: ScanResult,
        min_size_mb: float = 0,
        max_files: int = 100000,
        include_hidden: bool = False,
    ):
        """Scan a single drive."""
        log.info("Scanning drive %s...", drive)
        drive_files = 0

        for root, dirs, files in os.walk(drive, onerror=self._handle_error):
            if self._stop_event.is_set():
                break

            # Skip hidden directories if not included
            if not include_hidden and any(d.startswith(".") for d in dirs):
                dirs[:] = [d for d in dirs if not d.startswith(".")]

            for filename in files:
                if self._stop_event.is_set():
                    break

                if not include_hidden and filename.startswith("."):
                    continue

                try:
                    filepath = os.path.join(root, filename)
                    if not os.path.isfile(filepath):
                        continue

                    stat = os.stat(filepath)
                    size = stat.st_size
                    modified = datetime.fromtimestamp(stat.st_mtime)

                    result.total_files += 1
                    result.total_size += size
                    drive_files += 1

                    # Track by extension
                    ext = os.path.splitext(filename)[1].lower() or "no_ext"
                    if drive not in result.by_drive:
                        result.by_drive[drive] = {}
                    result.by_drive[drive][ext] = result.by_drive[drive].get(ext, 0) + 1

                    # Track large files (>10 MB)
                    if size > 10 * 1024 * 1024:
                        result.large_files.append(
                            {
                                "path": filepath,
                                "size": size,
                                "modified": modified,
                            }
                        )

                    # Track recent files (last 7 days)
                    age_days = (datetime.now() - modified).days
                    if age_days <= 7:
                        result.recent_files.append(
                            {
                                "path": filepath,
                                "size": size,
                                "modified": modified,
                            }
                        )

                    # Limit per drive
                    if drive_files >= max_files:
                        log.info("Reached max files for drive %s", drive)
                        break

                except (PermissionError, OSError):
                    continue

        log.info("Drive %s scan complete: %d files", drive, drive_files)

    def _handle_error(self, exc):
        """Handle os.walk errors."""
        if isinstance(exc, PermissionError):
            pass  # Ignore permission errors
        else:
            log.warning("Scan error: %s", exc)

    def stop(self):
        """Stop an ongoing scan."""
        self._stop_event.set()
