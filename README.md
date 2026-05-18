# Monitor Suite v2

Корпоративная система мониторинга, защиты данных и удалённого управления ПК.

**Управление через Telegram-бот**, сборка EXE через GitHub Actions, шифрование всех данных.

## Компоненты

| Компонент | Описание |
|---|---|
| **`agent/`** | Windows-агент: трей, трекинг, скриншоты, DLP, Telegram-бот |
| **`server/`** | *(опционально)* FastAPI + SQLite, веб-дашборд для многих агентов |
| **`dashboard/`** | *(опционально)* Веб-интерфейс администратора |
| **`.github/workflows/`** | GitHub Actions — автосборка EXE |
| **`build.spec`** | PyInstaller конфиг → один EXE-файл |

## Возможности агента

### Мониторинг
- **Активные окна** — имя процесса, заголовок, время использования
- **Idle-детект** — 60 сек без активности = пауза
- **Скриншоты** — по таймеру (настраиваемый интервал) + по команде
- **Буфер обмена** — текст (пароли автоматически фильтруются)
- **USB-устройства** — оповещение при подключении/отключении флешек
- **Новое ПО** — оповещение при установке новых программ

### Управление через Telegram
- **Информация**: `/status`, `/screen`, `/apps`, `/processes`, `/config`, `/integrity`
- **Процессы**: `/kill <name>`, `/block <name>`, `/unblock <name>`, `/blocklist`
- **Блокировка**: `/lock` (Win), `/lockscreen <pwd>` (полная), `/unlockscreen`
- **Система**: `/logoff`, `/shutdown [сек]`, `/restart [сек]`, `/message <текст>`
- **DLP**: `/usb`, `/clipboard [N]`, `/software`
- **Настройки**: `/set interval|tracking|block|usb|clipboard on|off|N`
- **Управление**: `/pause`, `/resume`, `/autostart on|off`

### Защита
- **AES-шифрование** конфигурации и логов (привязка к машине)
- **Пароль на выход** — нельзя закрыть агента без пароля
- **Автоперезапуск** — через Windows Task Scheduler
- **Проверка целостности** — SHA-256 хеш бинарника
- **Admin-only бот** — только указанный Telegram ID может управлять
- **Watchdog** — перезапуск при сбое

### UI
- **Красивый визард** при первом запуске (customtkinter, тёмная тема)
- **Системный трей** — иконка всегда видна
- **Полноэкранная блокировка** с паролем (тёмный оверлей)

## ⚠️ Юридическое предупреждение

Иконка в трее **всегда видна**. Это легальное ПО для:
- своих ПК
- корпоративных ПК (с уведомлением сотрудников)
- родительского контроля (с информированием)

**Запрещено**: установка без согласия, скрытие процесса, перехват паролей.
Нарушение: ст. 137-138, 272-273 УК РФ; GDPR; ECPA/CFAA.

## Быстрый старт

### Вариант A: Готовый EXE (рекомендуется)

1. Скачай `MonitorAgent.exe` из [Releases](../../releases)
2. Запусти — появится визард настройки:
   - Введи **токен Telegram-бота** (от @BotFather)
   - Введи **свой Telegram ID** (от @userinfobot)
   - Задай **пароль на выход** (опционально)
   - Выбери **функции** (все включены по умолчанию)
3. Агент запустится, иконка появится в трее
4. Открой бота в Telegram → `/start`

### Вариант B: Из исходников

```powershell
cd C:\Users\iSSGamer\CascadeProjects\monitor-suite
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r agent\requirements.txt
python -m agent.main
```

Для перенастройки: `python -m agent.main --reset`

### Вариант C: Собрать EXE локально

```powershell
pip install -r agent\requirements.txt
pyinstaller build.spec --noconfirm --clean
# EXE → dist\MonitorAgent.exe
```

## Сборка через GitHub Actions

1. Push в `main` → автосборка EXE
2. Создай тег `v1.0.0` → автоматический Release с EXE
3. Артефакт: `MonitorAgent-windows` (хранится 30 дней)

```bash
git tag v1.0.0
git push origin v1.0.0
```

## Архитектура

```
┌────────────────────────────────────────────┐
│              Windows PC (агент)             │
│                                            │
│  ┌─────────┐ ┌──────────┐ ┌────────────┐  │
│  │ Tracker  │ │Screenshot│ │  Blocker   │  │
│  └────┬────┘ └─────┬────┘ └─────┬──────┘  │
│       │            │            │          │
│  ┌────┴────┐ ┌─────┴────┐ ┌────┴───────┐  │
│  │USB Mon. │ │Clipboard │ │Software Mon│  │
│  └────┬────┘ └─────┬────┘ └────┬───────┘  │
│       │            │            │          │
│       └────────────┼────────────┘          │
│                    │                       │
│              ┌─────┴─────┐                 │
│              │   Agent    │  ← tray icon   │
│              │   Core     │                │
│              └─────┬─────┘                 │
│                    │                       │
│         ┌──────────┴──────────┐            │
│         │   Telegram Bot      │            │
│         │  (python-telegram)  │            │
│         └──────────┬──────────┘            │
└────────────────────┼───────────────────────┘
                     │ HTTPS (Telegram API)
                     ▼
              ┌──────────────┐
              │  Telegram    │
              │  Cloud       │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │  Admin       │
              │  (телефон)   │
              └──────────────┘
```

## Конфигурация

Зашифрованный конфиг: `%LOCALAPPDATA%\MonitorAgent\config.enc`
Лог: `%LOCALAPPDATA%\MonitorAgent\agent.log`
Скриншоты: `%LOCALAPPDATA%\MonitorAgent\screenshots\`

### Веб-дашборд (опционально)

Если нужен веб-интерфейс для нескольких агентов:

```powershell
pip install -r server\requirements.txt
python -m uvicorn server.main:app --host 0.0.0.0 --port 8765
```

Откроется на `http://localhost:8765/`, токен в `server/data/admin_token.txt`.

## Структура файлов

```
agent/
├── main.py              # Точка входа, интеграция всех модулей
├── config.py            # Зашифрованный конфиг (AES)
├── crypto.py            # AES-шифрование (Fernet + PBKDF2)
├── telegram_bot.py      # Telegram-бот (25+ команд, admin-only)
├── setup_ui.py          # Визард первого запуска (customtkinter)
├── tracker.py           # Трекинг активных окон (Win32 API)
├── idle.py              # Idle-детекция (GetLastInputInfo)
├── screenshot.py        # Скриншоты (mss + Pillow → JPEG)
├── blocker.py           # Блокировка процессов (psutil)
├── screen_lock.py       # Полноэкранная блокировка с паролем
├── usb_monitor.py       # USB-мониторинг (WMI)
├── clipboard_monitor.py # Буфер обмена (Win32 API)
├── software_monitor.py  # Мониторинг нового ПО (реестр)
├── protection.py        # Автозапуск, watchdog, integrity
├── api.py               # HTTP-клиент (для веб-сервера)
├── commands.py          # Обработчики удалённых команд
├── consent.py           # Диалог согласия (tkinter)
└── requirements.txt
```

## Лицензия

MIT. См. `LICENSE`.
