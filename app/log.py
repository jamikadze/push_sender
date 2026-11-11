"""Логирование через loguru.

Куда пишется:
  консоль                          - уровень LOG_LEVEL, цветной вывод
  data/logs/app_YYYY-MM-DD.log     - всё, новый файл каждый день, хранится LOG_RETENTION_DAYS
  data/logs/errors.log             - только WARNING и выше, ротация по 10 МБ

Стандартный logging (uvicorn, fastapi, httpx) перенаправляется в loguru,
поэтому формат у всех логов одинаковый. Файлы пишутся в UTF-8, так что
кириллица и ң ө ү в логах не ломаются.
"""
import inspect
import logging
import sys

from loguru import logger

from .config import settings

LOG_DIR = settings.DATA_DIR / "logs"

CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <7}</level> | "
    "<cyan>{name}</cyan> - <level>{message}</level>"
)
FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} - {message}"


class InterceptHandler(logging.Handler):
    """Пересылает записи стандартного logging в loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()

    if sys.stderr is not None:  # при запуске как служба/без консоли stderr может отсутствовать
        logger.add(sys.stderr, level=settings.LOG_LEVEL, format=CONSOLE_FORMAT, colorize=True)

    logger.add(
        LOG_DIR / "app_{time:YYYY-MM-DD}.log",
        level=settings.LOG_LEVEL,
        format=FILE_FORMAT,
        rotation="00:00",
        retention=f"{settings.LOG_RETENTION_DAYS} days",
        encoding="utf-8",
        enqueue=True,
    )
    logger.add(
        LOG_DIR / "errors.log",
        level="WARNING",
        format=FILE_FORMAT,
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,  # не выводить значения переменных в трейсбэках
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        std = logging.getLogger(name)
        std.handlers = [InterceptHandler()]
        std.propagate = False
    # httpx на уровне INFO пишет строку на каждый запрос - при рассылке это шум
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
