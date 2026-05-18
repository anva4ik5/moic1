"""Security hardening functions for emergency response.

- Windows Firewall management (block all / restore)
- USB storage disable/enable via registry
- Network adapter disable/enable
- Emergency lockdown (all at once)
"""
from __future__ import annotations

import logging
import subprocess
import winreg

log = logging.getLogger("monitor.agent.security")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


# ─── Windows Firewall ───

def firewall_block_all() -> bool:
    """Set Windows Firewall to block ALL inbound AND outbound connections.
    This effectively isolates the PC from the network.
    """
    try:
        cmds = [
            ["netsh", "advfirewall", "set", "allprofiles", "firewallpolicy", "blockinbound,blockoutbound"],
            ["netsh", "advfirewall", "set", "allprofiles", "state", "on"],
        ]
        for cmd in cmds:
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                log.warning("Firewall cmd failed: %s — %s", cmd, r.stderr)
                return False
        log.info("Firewall set to BLOCK ALL")
        return True
    except Exception as exc:
        log.warning("firewall_block_all error: %s", exc)
        return False


def firewall_restore_default() -> bool:
    """Restore firewall to default policy (block inbound, allow outbound)."""
    try:
        cmds = [
            ["netsh", "advfirewall", "set", "allprofiles", "firewallpolicy", "blockinbound,allowoutbound"],
        ]
        for cmd in cmds:
            subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
        log.info("Firewall restored to default")
        return True
    except Exception as exc:
        log.warning("firewall_restore error: %s", exc)
        return False


def firewall_status() -> str:
    """Get current firewall status."""
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "show", "allprofiles", "state"],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        return r.stdout.strip()[:500]
    except Exception:
        return "Не удалось получить статус"


# ─── USB Storage ───

_USB_REG_PATH = r"SYSTEM\CurrentControlSet\Services\USBSTOR"
_usb_was_disabled = False


def disable_usb_storage() -> bool:
    """Disable USB mass storage devices via registry. Prevents flash drives."""
    global _usb_was_disabled
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _USB_REG_PATH, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE)
        try:
            val, _ = winreg.QueryValueEx(key, "Start")
            _usb_was_disabled = val == 4
        except FileNotFoundError:
            _usb_was_disabled = False
        # 4 = disabled, 3 = enabled
        winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, 4)
        winreg.CloseKey(key)
        log.info("USB storage disabled")
        return True
    except Exception as exc:
        log.warning("disable_usb_storage error: %s", exc)
        return False


def enable_usb_storage() -> bool:
    """Re-enable USB mass storage devices."""
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _USB_REG_PATH, 0,
                            winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, 3)
        winreg.CloseKey(key)
        log.info("USB storage enabled")
        return True
    except Exception as exc:
        log.warning("enable_usb_storage error: %s", exc)
        return False


def usb_storage_status() -> str:
    """Check if USB storage is enabled or disabled."""
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _USB_REG_PATH, 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, "Start")
        winreg.CloseKey(key)
        return "ЗАБЛОКИРОВАНО" if val == 4 else "РАЗРЕШЕНО"
    except Exception:
        return "Неизвестно"


# ─── Network adapter disable/enable ───

def disable_network_adapters() -> bool:
    """Disable all network adapters (emergency network isolation)."""
    try:
        r = subprocess.run(
            ["powershell", "-Command",
             "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Disable-NetAdapter -Confirm:$false"],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        log.info("Network adapters disabled")
        return r.returncode == 0
    except Exception as exc:
        log.warning("disable_network error: %s", exc)
        return False


def enable_network_adapters() -> bool:
    """Re-enable all network adapters."""
    try:
        r = subprocess.run(
            ["powershell", "-Command",
             "Get-NetAdapter | Enable-NetAdapter -Confirm:$false"],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        log.info("Network adapters enabled")
        return r.returncode == 0
    except Exception as exc:
        log.warning("enable_network error: %s", exc)
        return False


# ─── RDP disable ───

def disable_rdp() -> bool:
    """Disable Remote Desktop access."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Terminal Server",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "fDenyTSConnections", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
        # also firewall rule
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "set", "rule",
             'group="Remote Desktop"', "new", "enable=No"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        log.info("RDP disabled")
        return True
    except Exception as exc:
        log.warning("disable_rdp error: %s", exc)
        return False


def enable_rdp() -> bool:
    """Re-enable Remote Desktop."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Terminal Server",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "fDenyTSConnections", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        log.info("RDP enabled")
        return True
    except Exception as exc:
        log.warning("enable_rdp error: %s", exc)
        return False


# ─── Emergency lockdown ───

def emergency_lockdown() -> dict[str, bool]:
    """Execute full emergency lockdown:
    - Block all firewall
    - Disable USB storage
    - Disable RDP
    (Network adapters NOT disabled — Telegram bot needs internet to stay online)
    """
    results = {
        "firewall_block_all": firewall_block_all(),
        "usb_disabled": disable_usb_storage(),
        "rdp_disabled": disable_rdp(),
    }
    log.info("EMERGENCY LOCKDOWN: %s", results)
    return results


def emergency_restore() -> dict[str, bool]:
    """Undo emergency lockdown."""
    results = {
        "firewall_restored": firewall_restore_default(),
        "usb_enabled": enable_usb_storage(),
        "rdp_enabled": enable_rdp(),
    }
    log.info("Emergency restore: %s", results)
    return results
