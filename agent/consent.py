"""First-run consent dialog. The user must explicitly agree before any tracking."""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import scrolledtext

log = logging.getLogger("monitor.agent.consent")

CONSENT_TEXT = (
    "Monitor Agent — уведомление о мониторинге\n"
    "============================================\n\n"
    "На этом компьютере устанавливается программа мониторинга, которая может "
    "собирать следующие данные и передавать их администратору:\n\n"
    "  •  Имя активного приложения и заголовок окна\n"
    "  •  Время использования приложений\n"
    "  •  Периодические скриншоты экрана (если включено администратором)\n"
    "  •  Список запущенных процессов (для блокировки правилами)\n"
    "  •  Состояние «активен / неактивен» (по движениям мыши/клавиатуры)\n\n"
    "Программа НЕ перехватывает нажатия клавиш и НЕ читает содержимое файлов.\n"
    "Иконка в системном трее всегда видима — ты можешь приостановить мониторинг "
    "или закрыть программу в любой момент.\n\n"
    "Нажимая «Согласен», вы подтверждаете, что:\n"
    "  1) вы являетесь владельцем этого ПК или сотрудником с уведомлением,\n"
    "  2) согласны на сбор перечисленных выше данных.\n\n"
    "Без согласия программа не будет собирать или передавать данные."
)


def ask_consent() -> bool:
    """Show modal dialog. Returns True if user agreed."""
    result = {"agreed": False}

    root = tk.Tk()
    root.title("Monitor Agent — согласие")
    root.geometry("640x520")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    header = tk.Label(
        root,
        text="Установлена программа мониторинга",
        font=("Segoe UI", 14, "bold"),
        pady=10,
    )
    header.pack(fill="x")

    txt = scrolledtext.ScrolledText(root, wrap="word", font=("Segoe UI", 10))
    txt.insert("1.0", CONSENT_TEXT)
    txt.configure(state="disabled")
    txt.pack(fill="both", expand=True, padx=12, pady=6)

    btn_frame = tk.Frame(root)
    btn_frame.pack(fill="x", pady=10, padx=12)

    def on_agree() -> None:
        result["agreed"] = True
        root.destroy()

    def on_decline() -> None:
        result["agreed"] = False
        root.destroy()

    decline = tk.Button(btn_frame, text="Отказаться и выйти", width=20, command=on_decline)
    decline.pack(side="left")

    agree = tk.Button(
        btn_frame,
        text="Согласен — включить мониторинг",
        width=30,
        command=on_agree,
        default="active",
    )
    agree.pack(side="right")

    root.protocol("WM_DELETE_WINDOW", on_decline)
    root.mainloop()
    return result["agreed"]
