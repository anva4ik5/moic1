@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo =========================================
echo   Автосборка MonitorAgent.exe
echo =========================================
echo.

set "PY_VER=3.11.9"
set "PY_INSTALLER=python-%PY_VER%-amd64.exe"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/%PY_INSTALLER%"
set "PY_TARGET=%LOCALAPPDATA%\Programs\Python\Python311"

:: === Найти рабочий Python (не Windows Store заглушку) ===
set "PYTHON="
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do (
    set "FOUND=%%i"
    if not "!FOUND:WindowsApps=!"=="!FOUND!" (
        echo [!] Windows Store placeholder detected, skipping...
    ) else (
        set "PYTHON=!FOUND!"
    )
)

:: Проверяем стандартные пути
if not defined PYTHON (
    if exist "%PY_TARGET%\python.exe" set "PYTHON=%PY_TARGET%\python.exe"
)
if not defined PYTHON (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYTHON (
    if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
)

:: === Если Python не найден — скачиваем и устанавливаем ===
if not defined PYTHON (
    echo [1/5] Python not found. Downloading installer (~25 MB)...
    powershell -Command "Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%TEMP%\%PY_INSTALLER%'" 2>nul
    if errorlevel 1 (
        echo [ERROR] Failed to download Python. Check your internet connection.
        echo         Manual install: https://python.org/downloads/
        pause
        exit /b 1
    )

    echo [2/5] Installing Python (silent, current user)...
    "%TEMP%\%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_doc=0 Shortcuts=0 TargetDir="%PY_TARGET%"
    timeout /t 3 /nobreak >nul

    if not exist "%PY_TARGET%\python.exe" (
        echo [ERROR] Python installation failed.
        pause
        exit /b 1
    )
    set "PYTHON=%PY_TARGET%\python.exe"
    echo [OK] Python installed: %PYTHON%
) else (
    echo [OK] Python found: %PYTHON%
)

:: === Проверка файлов проекта ===
if not exist "build.spec" (
    echo [ERROR] build.spec not found. Run this batch from the project folder.
    pause
    exit /b 1
)
if not exist "agent\requirements.txt" (
    echo [ERROR] agent\requirements.txt not found.
    pause
    exit /b 1
)

:: === Установка зависимостей ===
echo [3/5] Installing dependencies...
"%PYTHON%" -m pip install --upgrade pip --quiet 2>nul
"%PYTHON%" -m pip install -r agent\requirements.txt --quiet
if errorlevel 1 (
    echo [WARN] Some packages may have failed, continuing...
)

:: === Сборка ===
echo [4/5] Building EXE via PyInstaller...
if exist "dist\MonitorAgent.exe" del /F /Q "dist\MonitorAgent.exe" 2>nul
if exist "build" rmdir /S /Q "build" 2>nul

"%PYTHON%" -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed. Check the logs above.
    pause
    exit /b 1
)

:: === Копирование в Downloads ===
echo [5/5] Copying to Downloads...
set "DEST=%USERPROFILE%\Downloads\MonitorAgent.exe"
copy /Y "dist\MonitorAgent.exe" "%DEST%" >nul
if errorlevel 1 (
    echo [WARN] Could not copy to Downloads.
    echo       EXE is here: %CD%\dist\MonitorAgent.exe
) else (
    echo [OK] Copied to: %DEST%
)

echo.
echo =========================================
echo   Done! MonitorAgent.exe is ready.
echo =========================================
pause
