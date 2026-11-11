"""Фейковый push-сервис для проверки без реальной отправки.

Запуск:  venv\\Scripts\\python tools\\mock_push_server.py
Затем в .env:  PUSH_BASE_URL=http://127.0.0.1:7777

Логирует полученные логины и текст в tools/mock_received.txt (UTF-8),
чтобы можно было убедиться, что кыргызские буквы дошли без искажений.
"""
import asyncio
import random
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request

OUT = Path(__file__).with_name("mock_received.txt")
app = FastAPI()


def _record(login: str, message: str) -> None:
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(f"{login}\t{message}\n")


async def _answer(login: str, message: str):
    await asyncio.sleep(random.uniform(0.01, 0.05))
    if login.endswith("13"):
        return {"message": "user not found"}
    _record(login, message)
    return {"message": "ok"}


@app.get("/push/{login}/{message:path}")
async def push_path(login: str, message: str):
    return await _answer(login, message)


@app.get("/push")
async def push_query(login: str, message: str):
    return await _answer(login, message)


@app.post("/push")
async def push_json(request: Request):
    data = await request.json()
    return await _answer(data["login"], data["message"])


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7777)
