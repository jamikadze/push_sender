@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist venv (
    echo Создаю виртуальное окружение...
    python -m venv venv || exit /b 1
    venv\Scripts\python -m pip install -r requirements.txt || exit /b 1
)
if not exist .env copy .env.example .env >nul

venv\Scripts\python -m app.main
