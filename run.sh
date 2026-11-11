#!/usr/bin/env bash
# Запуск на Linux - аналог run.bat
set -e
export PYTHONUTF8=1
cd "$(dirname "$0")"

if [ ! -x venv/bin/python ]; then
    echo "Создаю виртуальное окружение..."
    python3 -m venv venv
    venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; fi

exec venv/bin/python -m app.main
