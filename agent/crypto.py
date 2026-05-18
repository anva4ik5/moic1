"""AES encryption for config and logs using Fernet (AES-128-CBC + HMAC)."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None  # type: ignore
    logging.getLogger("monitor.agent.crypto").warning("cryptography not installed — config will be stored as plaintext JSON")


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte Fernet key from a password + salt via PBKDF2."""
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations=390_000, dklen=32)
    return base64.urlsafe_b64encode(raw)


class SecureStorage:
    """Encrypts/decrypts JSON data to a file using a machine-specific key.

    The key is derived from the hostname + Windows machine GUID (or a fallback).
    This is NOT unbreakable crypto — it prevents casual file reading.
    Falls back to plaintext JSON if cryptography is not available.
    """

    def __init__(self, path: Path, extra_seed: str = ""):
        self.path = path
        self.salt_path = path.with_suffix(".salt")
        self._extra = extra_seed

    def _get_salt(self) -> bytes:
        if self.salt_path.exists():
            return self.salt_path.read_bytes()
        salt = os.urandom(16)
        self.salt_path.parent.mkdir(parents=True, exist_ok=True)
        self.salt_path.write_bytes(salt)
        return salt

    def _machine_seed(self) -> str:
        parts = [os.environ.get("COMPUTERNAME", ""), os.environ.get("USERNAME", ""), self._extra]
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                parts.append(str(guid))
        except Exception:
            parts.append("fallback-seed-42")
        return "|".join(parts)

    def _fernet(self):
        if not Fernet:
            return None
        return Fernet(self._derive_key(self._machine_seed()))

    def _derive_key(self, password: str) -> bytes:
        return _derive_key(password, self._get_salt())

    def save(self, data: dict[str, Any]) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        f = self._fernet()
        if f:
            raw = f.encrypt(raw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(raw)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_bytes()
            f = self._fernet()
            if f:
                raw = f.decrypt(raw)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def exists(self) -> bool:
        return self.path.exists()

    def delete(self) -> None:
        for p in (self.path, self.salt_path):
            if p.exists():
                p.unlink(missing_ok=True)
