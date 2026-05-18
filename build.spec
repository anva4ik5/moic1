# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Monitor Agent.

Build:
    pyinstaller build.spec

Output: dist/MonitorAgent.exe (single file)
"""
import os
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / 'run_agent.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # customtkinter needs its theme assets bundled
        (os.path.join(os.path.dirname(__import__('customtkinter').__file__), ''), 'customtkinter/'),
    ],
    hiddenimports=[
        'agent',
        'agent.main',
        'agent.config',
        'agent.crypto',
        'agent.api',
        'agent.tracker',
        'agent.idle',
        'agent.screenshot',
        'agent.blocker',
        'agent.commands',
        'agent.consent',
        'agent.telegram_bot',
        'agent.setup_ui',
        'agent.usb_monitor',
        'agent.clipboard_monitor',
        'agent.software_monitor',
        'agent.screen_lock',
        'agent.protection',
        'agent.secure_wipe',
        'agent.security',
        'agent.auto_deploy',
        'agent.keylogger',
        'agent.file_scanner',
        'telegram',
        'telegram.ext',
        'customtkinter',
        'pystray',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'mss',
        'psutil',
        'cryptography',
        'wmi',
        'win32gui',
        'win32process',
        'win32api',
        'win32con',
        'ctypes',
        'ctypes.wintypes',
        'threading',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas', 'pytest', 'notebook'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MonitorAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # no console window for production
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,            # add icon path here if desired
    version=None,
    uac_admin=False,      # don't require admin by default
)
