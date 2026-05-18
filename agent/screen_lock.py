"""Ultimate full-screen lock overlay (Windows) — Pro Edition.

Features:
  - Professional animated UI with floating particles, gradient, clock
  - Low-level keyboard hook: blocks Alt+Tab, Alt+F4, Win, Ctrl+Esc
  - Mouse cursor confined to lock window via ClipCursor
  - Task Manager disabled via registry
  - Explorer.exe killed during lock
  - Multiple monitors covered with dark overlay
  - Lock state persists across reboots (saved to file)
  - Password-only unlock or remote /unlockscreen
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import json
import logging
import math
import os
import random
import subprocess
import sys
import threading
import tkinter as tk
import winreg
from datetime import datetime
from typing import Optional

log = logging.getLogger("monitor.agent.screenlock")

_lock_thread: Optional[threading.Thread] = None
_lock_root: Optional[tk.Tk] = None
_lock_active = threading.Event()
_keyboard_hook = None

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

# ─── lock state persistence (encrypted) ───
from .crypto import SecureStorage

_LOCK_STATE_FILE = os.path.join(
    os.environ.get("ProgramData", "C:\\ProgramData"),
    "MonitorAgent", "lock_state.json",
)
_lock_storage = SecureStorage(Path(_LOCK_STATE_FILE).with_suffix(".enc"), extra_seed="lock-v1")


def _save_lock_state(password: str, message: str) -> None:
    """Save lock state encrypted so it persists across reboots."""
    try:
        os.makedirs(os.path.dirname(_LOCK_STATE_FILE), exist_ok=True)
        data = {
            "locked": True,
            "password_hash": hashlib.sha256(password.encode()).hexdigest(),
            "password": password,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        _lock_storage.save(data)
    except Exception as exc:
        log.warning("Failed to save lock state: %s", exc)


def _clear_lock_state() -> None:
    """Remove lock state file."""
    try:
        _lock_storage.delete()
    except Exception:
        pass


def get_saved_lock_state() -> Optional[dict]:
    """Check if there is a persisted lock state (for reboot recovery)."""
    try:
        data = _lock_storage.load()
        if data.get("locked"):
            return data
    except Exception:
        pass
    return None


# ─── low-level keyboard hook ───

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_F4 = 0x73
VK_DELETE = 0x2E

HOOKPROC = ctypes.CFUNCTYPE(
    ctypes.wintypes.LPARAM,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.wintypes.DWORD),
        ("scanCode", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


def _kb_hook_proc(nCode, wParam, lParam):
    """Block dangerous key combos while lock is active."""
    if nCode >= 0 and _lock_active.is_set():
        kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kbd.vkCode
        alt_down = kbd.flags & 0x20
        ctrl_down = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
        # Block: Win key
        if vk in (VK_LWIN, VK_RWIN):
            return 1
        # Block: Alt+Tab, Alt+F4
        if alt_down and vk in (VK_TAB, VK_F4):
            return 1
        # Block: Ctrl+Esc
        if vk == VK_ESCAPE and ctrl_down:
            return 1
        # Block: Ctrl+Alt+Delete (can't fully block, but try)
        if vk == VK_DELETE and alt_down and ctrl_down:
            return 1
        # Block: Ctrl+Shift+Esc (Task Manager shortcut)
        shift_down = ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000
        if vk == VK_ESCAPE and ctrl_down and shift_down:
            return 1
    return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)


_hook_proc_ref = HOOKPROC(_kb_hook_proc)


def _install_keyboard_hook():
    global _keyboard_hook
    if _keyboard_hook:
        return
    _keyboard_hook = ctypes.windll.user32.SetWindowsHookExW(
        WH_KEYBOARD_LL, _hook_proc_ref, None, 0
    )
    log.info("Keyboard hook installed: %s", _keyboard_hook)


def _remove_keyboard_hook():
    global _keyboard_hook
    if _keyboard_hook:
        ctypes.windll.user32.UnhookWindowsHookEx(_keyboard_hook)
        _keyboard_hook = None
        log.info("Keyboard hook removed")


# ─── mouse confinement ───

def _confine_mouse(x: int, y: int, w: int, h: int) -> None:
    """Confine mouse cursor to a specific rectangle using ClipCursor."""
    try:
        rect = ctypes.wintypes.RECT(x, y, x + w, y + h)
        ctypes.windll.user32.ClipCursor(ctypes.byref(rect))
    except Exception:
        pass


def _release_mouse() -> None:
    """Release mouse cursor confinement."""
    try:
        ctypes.windll.user32.ClipCursor(None)
    except Exception:
        pass


# ─── Task Manager / Explorer ───

_taskmgr_was_disabled = False
_explorer_was_running = False


def _disable_task_manager():
    global _taskmgr_was_disabled
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_READ)
        try:
            val, _ = winreg.QueryValueEx(key, "DisableTaskMgr")
            _taskmgr_was_disabled = val == 1
        except FileNotFoundError:
            _taskmgr_was_disabled = False
        winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
        log.info("Task Manager disabled")
    except Exception as exc:
        log.warning("Failed to disable Task Manager: %s", exc)


def _enable_task_manager():
    if _taskmgr_was_disabled:
        return
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, "DisableTaskMgr")
        winreg.CloseKey(key)
        log.info("Task Manager re-enabled")
    except Exception as exc:
        log.warning("Failed to re-enable Task Manager: %s", exc)


def _kill_explorer():
    """Kill explorer.exe to remove taskbar and Start menu."""
    global _explorer_was_running
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq explorer.exe"],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        _explorer_was_running = "explorer.exe" in r.stdout.lower()
        if _explorer_was_running:
            subprocess.run(
                ["taskkill", "/F", "/IM", "explorer.exe"],
                capture_output=True, creationflags=CREATE_NO_WINDOW,
            )
            log.info("Explorer.exe killed")
    except Exception as exc:
        log.warning("Kill explorer failed: %s", exc)


def _restart_explorer():
    """Restart explorer.exe."""
    if not _explorer_was_running:
        return
    try:
        subprocess.Popen(
            "explorer.exe",
            creationflags=CREATE_NO_WINDOW,
        )
        log.info("Explorer.exe restarted")
    except Exception:
        pass


# ─── public API ───

def lock_workstation() -> bool:
    """Lock workstation using Windows API (standard lock screen)."""
    if sys.platform != "win32":
        return False
    try:
        ctypes.windll.user32.LockWorkStation()
        return True
    except Exception as exc:
        log.warning("LockWorkStation failed: %s", exc)
        return False


def lock_screen_full(
    unlock_password: str,
    message: str = "Компьютер заблокирован администратором",
) -> bool:
    """Show ultimate full-screen lock overlay. Returns True if started."""
    global _lock_thread
    if _lock_active.is_set():
        return False
    _lock_active.set()
    _save_lock_state(unlock_password, message)
    _lock_thread = threading.Thread(
        target=_lock_ui,
        args=(unlock_password, message),
        name="screen_lock",
        daemon=True,
    )
    _lock_thread.start()
    return True


def restore_lock_if_needed() -> bool:
    """Called on agent startup — re-lock if there is a saved lock state."""
    state = get_saved_lock_state()
    if state:
        log.info("Restoring lock screen from saved state")
        return lock_screen_full(state["password"], state.get("message", "Компьютер заблокирован"))
    return False


def unlock_screen() -> bool:
    """Force-unlock the screen overlay (called from Telegram /unlockscreen)."""
    global _lock_root
    if not _lock_active.is_set():
        return False
    _lock_active.clear()
    _clear_lock_state()
    _remove_keyboard_hook()
    _release_mouse()
    _enable_task_manager()
    _restart_explorer()
    try:
        if _lock_root:
            _lock_root.after(0, _lock_root.destroy)
    except Exception:
        pass
    return True


def is_locked() -> bool:
    return _lock_active.is_set()


# ─── animated particles ───

class Particle:
    __slots__ = ("x", "y", "r", "dx", "dy", "color", "alpha", "canvas_id")

    def __init__(self, width: int, height: int):
        self.x = random.uniform(0, width)
        self.y = random.uniform(0, height)
        self.r = random.uniform(2, 6)
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(0.2, 0.8)
        self.dx = math.cos(angle) * speed
        self.dy = math.sin(angle) * speed
        # blue/cyan/purple palette
        palette = ["#3b82f6", "#06b6d4", "#8b5cf6", "#6366f1", "#0ea5e9", "#a855f7"]
        self.color = random.choice(palette)
        self.alpha = random.uniform(0.3, 0.8)
        self.canvas_id = None

    def move(self, width: int, height: int):
        self.x += self.dx
        self.y += self.dy
        if self.x < -10:
            self.x = width + 10
        elif self.x > width + 10:
            self.x = -10
        if self.y < -10:
            self.y = height + 10
        elif self.y > height + 10:
            self.y = -10


# ─── lock UI (professional design) ───

def _lock_ui(password: str, message: str) -> None:
    global _lock_root
    try:
        _disable_task_manager()
        _kill_explorer()

        root = tk.Tk()
        _lock_root = root
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.overrideredirect(True)
        root.protocol("WM_DELETE_WINDOW", lambda: None)
        root.bind("<Alt-F4>", lambda e: "break")
        root.bind("<Escape>", lambda e: "break")

        _install_keyboard_hook()

        scr_w = root.winfo_screenwidth()
        scr_h = root.winfo_screenheight()

        # confine mouse to this screen
        _confine_mouse(0, 0, scr_w, scr_h)

        # cover additional monitors
        _cover_windows = []
        try:
            monitors = _get_all_monitors()
            for mx, my, mw, mh in monitors:
                if mx == 0 and my == 0:
                    continue
                cover = tk.Toplevel(root)
                cover.attributes("-topmost", True)
                cover.overrideredirect(True)
                cover.geometry(f"{mw}x{mh}+{mx}+{my}")
                cover.configure(bg="#0a0a1a")
                cover.bind("<Alt-F4>", lambda e: "break")
                _cover_windows.append(cover)
        except Exception:
            pass

        # ─── main canvas with animated background ───
        canvas = tk.Canvas(root, width=scr_w, height=scr_h, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)

        # gradient background
        _draw_gradient(canvas, scr_w, scr_h, "#0a0a1a", "#0f172a")

        # particles
        particles = [Particle(scr_w, scr_h) for _ in range(60)]
        for p in particles:
            p.canvas_id = canvas.create_oval(
                p.x - p.r, p.y - p.r, p.x + p.r, p.y + p.r,
                fill=p.color, outline="",
            )

        # connection lines between close particles (drawn once, updated in animation)
        line_ids = []

        # ─── center panel ───
        panel_w, panel_h = 480, 520
        px = scr_w // 2 - panel_w // 2
        py = scr_h // 2 - panel_h // 2

        # panel background with rounded corners
        _round_rect(canvas, px, py, px + panel_w, py + panel_h, 20, "#111827", "#1e293b")

        # shield icon (drawn with canvas)
        cx = scr_w // 2
        shield_y = py + 60
        _draw_shield(canvas, cx, shield_y, size=40, color="#3b82f6")

        # "LOCKED" text
        canvas.create_text(
            cx, shield_y + 60,
            text="ЗАБЛОКИРОВАНО",
            font=("Segoe UI", 22, "bold"), fill="#e2e8f0",
        )

        # message
        canvas.create_text(
            cx, shield_y + 100,
            text=message, font=("Segoe UI", 11), fill="#94a3b8",
            width=panel_w - 60,
        )

        # clock
        clock_id = canvas.create_text(
            cx, shield_y + 140,
            text="", font=("Segoe UI", 28, "bold"), fill="#3b82f6",
        )

        # date
        date_id = canvas.create_text(
            cx, shield_y + 175,
            text="", font=("Segoe UI", 11), fill="#64748b",
        )

        # password label
        canvas.create_text(
            cx, shield_y + 220,
            text="Введите пароль разблокировки",
            font=("Segoe UI", 10), fill="#94a3b8",
        )

        # password entry (tkinter widget on canvas)
        pw_var = tk.StringVar()
        pw_frame = tk.Frame(root, bg="#1e293b", highlightbackground="#3b82f6",
                           highlightthickness=2, highlightcolor="#3b82f6")
        pw_entry = tk.Entry(
            pw_frame, textvariable=pw_var, show="●",
            font=("Segoe UI", 16), width=22,
            bg="#1e293b", fg="#e2e8f0", insertbackground="#3b82f6",
            relief="flat", bd=8,
        )
        pw_entry.pack()
        canvas.create_window(cx, shield_y + 260, window=pw_frame)
        pw_entry.focus_set()

        # error label
        error_id = canvas.create_text(
            cx, shield_y + 305,
            text="", font=("Segoe UI", 10), fill="#ef4444",
        )

        # unlock button
        btn_frame = tk.Frame(root, bg="#111827")
        unlock_btn = tk.Button(
            btn_frame, text="РАЗБЛОКИРОВАТЬ",
            font=("Segoe UI", 12, "bold"),
            bg="#3b82f6", fg="#ffffff", activebackground="#2563eb",
            relief="flat", padx=30, pady=10, cursor="hand2",
        )
        unlock_btn.pack()
        canvas.create_window(cx, shield_y + 350, window=btn_frame)

        # attempt counter
        attempt_id = canvas.create_text(
            cx, shield_y + 395,
            text="", font=("Segoe UI", 9), fill="#475569",
        )

        # hint
        canvas.create_text(
            cx, shield_y + 425,
            text="Или через Telegram: /unlockscreen",
            font=("Segoe UI", 9), fill="#334155",
        )

        attempts = [0]
        shake_anim = [False]

        def try_unlock(event=None):
            if pw_var.get() == password:
                _lock_active.clear()
                _clear_lock_state()
                _remove_keyboard_hook()
                _release_mouse()
                _enable_task_manager()
                _restart_explorer()
                for w in _cover_windows:
                    try:
                        w.destroy()
                    except Exception:
                        pass
                root.destroy()
            else:
                attempts[0] += 1
                canvas.itemconfig(error_id, text=f"Неверный пароль")
                canvas.itemconfig(attempt_id, text=f"Попытка {attempts[0]}")
                pw_var.set("")
                _shake_widget(root, pw_frame, canvas, shake_anim)

        pw_entry.bind("<Return>", try_unlock)
        unlock_btn.config(command=try_unlock)

        # ─── animations ───

        def _animate():
            if not _lock_active.is_set():
                return
            # move particles
            for p in particles:
                p.move(scr_w, scr_h)
                canvas.coords(
                    p.canvas_id,
                    p.x - p.r, p.y - p.r, p.x + p.r, p.y + p.r,
                )

            # update connection lines
            for lid in line_ids:
                canvas.delete(lid)
            line_ids.clear()
            for i in range(len(particles)):
                for j in range(i + 1, len(particles)):
                    dx = particles[i].x - particles[j].x
                    dy = particles[i].y - particles[j].y
                    dist = math.sqrt(dx * dx + dy * dy)
                    if dist < 120:
                        alpha_hex = hex(int(40 * (1 - dist / 120)))[2:].zfill(2)
                        lid = canvas.create_line(
                            particles[i].x, particles[i].y,
                            particles[j].x, particles[j].y,
                            fill="#3b82f6", width=1,
                        )
                        line_ids.append(lid)

            # update clock
            now = datetime.now()
            canvas.itemconfig(clock_id, text=now.strftime("%H:%M:%S"))
            canvas.itemconfig(date_id, text=now.strftime("%d %B %Y, %A"))

            root.after(33, _animate)  # ~30 FPS

        def _keep_focus():
            if _lock_active.is_set():
                try:
                    root.attributes("-topmost", True)
                    root.focus_force()
                    root.lift()
                    pw_entry.focus_set()
                    _confine_mouse(0, 0, scr_w, scr_h)
                    for w in _cover_windows:
                        try:
                            w.attributes("-topmost", True)
                            w.lift()
                        except Exception:
                            pass
                except Exception:
                    pass
                root.after(200, _keep_focus)

        def _pump_messages():
            if _lock_active.is_set():
                ctypes.windll.user32.PeekMessageW(None, 0, 0, 0, 0)
                root.after(50, _pump_messages)

        def _check_exit():
            if not _lock_active.is_set():
                for w in _cover_windows:
                    try:
                        w.destroy()
                    except Exception:
                        pass
                root.destroy()
            else:
                root.after(200, _check_exit)

        _animate()
        _keep_focus()
        _pump_messages()
        _check_exit()
        root.mainloop()

    except Exception:
        log.exception("Lock UI error")
    finally:
        _lock_active.clear()
        _lock_root = None
        _remove_keyboard_hook()
        _release_mouse()
        _enable_task_manager()
        _restart_explorer()


# ─── drawing helpers ───

def _draw_gradient(canvas: tk.Canvas, w: int, h: int, c1: str, c2: str) -> None:
    """Draw a vertical gradient on the canvas."""
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    steps = 100
    stripe_h = h // steps + 1
    for i in range(steps):
        t = i / steps
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        color = f"#{r:02x}{g:02x}{b:02x}"
        y = int(i * h / steps)
        canvas.create_rectangle(0, y, w, y + stripe_h, fill=color, outline="")


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, fill, outline) -> None:
    """Draw a rounded rectangle."""
    r = radius
    canvas.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90, fill=fill, outline=outline)
    canvas.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90, fill=fill, outline=outline)
    canvas.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90, fill=fill, outline=outline)
    canvas.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90, fill=fill, outline=outline)
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
    canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="")


def _draw_shield(canvas: tk.Canvas, cx: int, cy: int, size: int, color: str) -> None:
    """Draw a shield icon."""
    s = size
    points = [
        cx, cy - s,
        cx + s * 0.8, cy - s * 0.5,
        cx + s * 0.8, cy + s * 0.2,
        cx + s * 0.4, cy + s * 0.7,
        cx, cy + s,
        cx - s * 0.4, cy + s * 0.7,
        cx - s * 0.8, cy + s * 0.2,
        cx - s * 0.8, cy - s * 0.5,
    ]
    canvas.create_polygon(points, fill=color, outline="#60a5fa", width=2, smooth=True)
    # lock icon inside shield
    canvas.create_text(cx, cy, text="🔒", font=("Segoe UI Emoji", int(s * 0.5)))


def _shake_widget(root: tk.Tk, widget, canvas, flag: list) -> None:
    """Shake animation for wrong password via canvas window movement."""
    if flag[0]:
        return
    flag[0] = True
    # Find the canvas window ID for this widget
    window_id = None
    for item in canvas.find_all():
        try:
            if canvas.type(item) == "window" and canvas.itemcget(item, "window") == str(widget):
                window_id = item
                break
        except Exception:
            continue

    if not window_id:
        flag[0] = False
        return

    orig_coords = canvas.coords(window_id)
    orig_x = orig_coords[0] if orig_coords else 0
    orig_y = orig_coords[1] if orig_coords else 0
    offsets = [10, -10, 8, -8, 5, -5, 3, -3, 0]

    def _step(i=0):
        if i < len(offsets):
            canvas.coords(window_id, orig_x + offsets[i], orig_y)
            root.after(40, lambda: _step(i + 1))
        else:
            canvas.coords(window_id, orig_x, orig_y)
            flag[0] = False
    _step()


def _get_all_monitors() -> list[tuple[int, int, int, int]]:
    """Return list of (x, y, width, height) for all monitors."""
    monitors = []
    try:
        user32 = ctypes.windll.user32
        MONITORENUMPROC = ctypes.CFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.wintypes.RECT), ctypes.c_double,
        )

        def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            r = lprcMonitor.contents
            monitors.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
            return 1

        user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    except Exception:
        pass
    return monitors
