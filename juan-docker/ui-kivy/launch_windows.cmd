@echo off
SETLOCAL EnableDelayedExpansion

echo ╔══════════════════════════════════════╗
echo ║       JUAN Desktop Client            ║
echo ╚══════════════════════════════════════╝
echo.

:: ── Check Python ─────────────────────────────────────────────────────────────
where python >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found. Install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% found

:: ── Virtual environment ───────────────────────────────────────────────────────
SET VENV_DIR=%~dp0.venv

IF NOT EXIST "%VENV_DIR%\Scripts\activate.bat" (
    echo [SETUP] Creating virtual environment...
    python -m venv "%VENV_DIR%"
    IF %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"
echo [OK] Virtual environment activated

:: ── Install / update dependencies ────────────────────────────────────────────
echo [SETUP] Checking dependencies...
pip show kivy >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [SETUP] Installing Kivy and dependencies (first run only)...
    pip install --upgrade pip --quiet
    pip install -r "%~dp0requirements.txt" --quiet
    IF %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
) ELSE (
    echo [OK] Dependencies already installed
)

:: ── Optional: apply environment overrides ────────────────────────────────────
IF NOT "%JUAN_WS_URL%"=="" (
    echo [CONFIG] WS URL: %JUAN_WS_URL%
)
IF NOT "%JUAN_HTTP_URL%"=="" (
    echo [CONFIG] HTTP URL: %JUAN_HTTP_URL%
)

:: ── Launch ────────────────────────────────────────────────────────────────────
echo.
echo [LAUNCH] Starting Juan Desktop...
echo          Press Ctrl+C or close the window to exit.
echo.

cd /d "%~dp0"
python main.py

IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Juan exited with code %ERRORLEVEL%
    pause
)
