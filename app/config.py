import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Settings:
    # Адрес push-сервиса
    PUSH_BASE_URL: str = os.environ.get("PUSH_BASE_URL", "http://192.168.20.31:7777").rstrip("/")
    # Способ вызова push-сервиса (ровно один, без перебора вариантов - чтобы не было дублей):
    #   path  -> GET  {base}/push/{login}/{message}
    #   query -> GET  {base}/push?login=..&message=..
    #   json  -> POST {base}/push  {"login": .., "message": ..}
    PUSH_MODE: str = os.environ.get("PUSH_MODE", "path").lower()
    # Сколько запросов отправлять одновременно
    PUSH_CONCURRENCY: int = max(1, _int("PUSH_CONCURRENCY", 5))
    # Таймаут одного запроса, секунд
    PUSH_TIMEOUT: int = max(1, _int("PUSH_TIMEOUT", 15))
    # Повторы только при ошибке соединения (когда запрос гарантированно не дошёл)
    PUSH_CONNECT_RETRIES: int = max(0, _int("PUSH_CONNECT_RETRIES", 3))

    # Допустимая длина логина (только цифры)
    MIN_LOGIN_DIGITS: int = _int("MIN_LOGIN_DIGITS", 9)
    MAX_LOGIN_DIGITS: int = _int("MAX_LOGIN_DIGITS", 15)

    MAX_UPLOAD_MB: int = _int("MAX_UPLOAD_MB", 50)

    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()
    LOG_RETENTION_DAYS: int = _int("LOG_RETENTION_DAYS", 30)

    DATA_DIR: Path = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    HOST: str = os.environ.get("HOST", "0.0.0.0")
    PORT: int = _int("PORT", 5001)


settings = Settings()
