"""Отправка одного push-уведомления.

Статусы результата:
  ok      - сервис ответил {"message": "ok"}
  error   - сервис ответил ошибкой (точно не доставлено), можно повторить
  unknown - ответ не получен (таймаут/обрыв): сообщение МОГЛО дойти,
            поэтому автоматически НЕ повторяем, чтобы не было дублей
"""
import asyncio
from urllib.parse import quote

import httpx

from .config import settings


def _one_line(s: str, limit: int = 300) -> str:
    return " ".join(str(s).split())[:limit]


def _build_request(login: str, message: str) -> tuple[str, str, dict]:
    base = settings.PUSH_BASE_URL
    mode = settings.PUSH_MODE
    if mode == "query":
        return "GET", f"{base}/push", {"params": {"login": login, "message": message}}
    if mode == "json":
        return "POST", f"{base}/push", {"json": {"login": login, "message": message}}
    # path: текст кодируется в UTF-8 и percent-encoding (ң -> %D2%A3)
    return "GET", f"{base}/push/{quote(login, safe='')}/{quote(message, safe='')}", {}


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.PUSH_TIMEOUT, connect=10),
        limits=httpx.Limits(
            max_connections=settings.PUSH_CONCURRENCY,
            max_keepalive_connections=settings.PUSH_CONCURRENCY,
        ),
    )


async def send_push(client: httpx.AsyncClient, login: str, message: str) -> tuple[str, str]:
    method, url, kwargs = _build_request(login, message)
    last_error = ""
    for attempt in range(settings.PUSH_CONNECT_RETRIES + 1):
        try:
            resp = await client.request(method, url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            # Соединение не установлено - запрос точно не ушёл, повторять безопасно
            last_error = f"нет соединения: {type(e).__name__} {e}"
            await asyncio.sleep(2 * (attempt + 1))
            continue
        except httpx.TimeoutException as e:
            return "unknown", _one_line(f"таймаут ответа: {type(e).__name__}")
        except httpx.HTTPError as e:
            return "unknown", _one_line(f"{type(e).__name__}: {e}")

        body = _one_line(resp.text)
        if resp.status_code != 200:
            return "error", f"HTTP {resp.status_code}: {body}"
        try:
            data = resp.json()
        except ValueError:
            return "unknown", f"ответ не JSON: {body}"
        if isinstance(data, dict) and data.get("message") == "ok":
            return "ok", ""
        return "error", f"ответ API: {body}"

    return "error", _one_line(last_error)
