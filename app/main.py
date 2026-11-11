import mimetypes

# На Windows реестр иногда отдаёт .js как text/plain - фиксируем типы явно
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from . import text_check
from .config import BASE_DIR, settings
from .jobs import JobConflict, JobManager
from .log import setup_logging
from .parser import ParseError, parse_file
from .sender import make_client, send_push

settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR = settings.DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

setup_logging()

manager = JobManager(settings.DATA_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Старт: push-сервис {} (режим {}, потоков {}, таймаут {} c)",
        settings.PUSH_BASE_URL, settings.PUSH_MODE, settings.PUSH_CONCURRENCY, settings.PUSH_TIMEOUT,
    )
    manager.load()
    logger.info("Загружено рассылок: {}", len(manager.jobs))
    yield
    logger.info("Остановка сервера")
    await manager.shutdown()


app = FastAPI(title="Push-рассылка", lifespan=lifespan)
STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------- модели ----------
class TextIn(BaseModel):
    message: str


class TestSendIn(BaseModel):
    login: str
    message: str


class JobIn(BaseModel):
    upload_id: str
    message: str
    name: str = ""
    start: bool = True


class RetryIn(BaseModel):
    include_unknown: bool = False


# ---------- helpers ----------
def _check_message(message: str) -> str:
    info = text_check.analyze(message)
    if info["errors"]:
        raise HTTPException(400, " ".join(info["errors"]))
    return info["normalized"]


def _job_or_404(job_id: str):
    try:
        return manager.get(job_id)
    except KeyError:
        raise HTTPException(404, "Рассылка не найдена")


def _upload_path(upload_id: str) -> Path:
    if not upload_id.isalnum():
        raise HTTPException(400, "Некорректный upload_id")
    return UPLOADS_DIR / f"{upload_id}.json"


# ---------- страницы ----------
@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")


@app.get("/api/config")
async def get_config():
    return {
        "push_mode": settings.PUSH_MODE,
        "push_base_url": settings.PUSH_BASE_URL,
        "concurrency": settings.PUSH_CONCURRENCY,
        "min_login_digits": settings.MIN_LOGIN_DIGITS,
        "max_login_digits": settings.MAX_LOGIN_DIGITS,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
    }


# ---------- текст ----------
@app.post("/api/text/check")
async def check_text(body: TextIn):
    return text_check.analyze(body.message)


@app.post("/api/text/fix")
async def fix_text(body: TextIn):
    fixed, changes = text_check.fix(body.message)
    return {"message": fixed, "changes": changes}


# ---------- файл получателей ----------
@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    data = await file.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Файл больше {settings.MAX_UPLOAD_MB} МБ")
    try:
        res = await run_in_threadpool(parse_file, file.filename or "", data)
    except ParseError as e:
        logger.warning("Файл «{}» не принят: {}", file.filename, e)
        raise HTTPException(400, str(e))
    logger.info(
        "Загружен файл «{}»: логинов {}, дублей {}, некорректных {}",
        file.filename, len(res.logins), res.duplicates, res.invalid,
    )

    upload_id = uuid.uuid4().hex
    payload = {"source": file.filename, "logins": res.logins}
    _upload_path(upload_id).write_text(json.dumps(payload), encoding="utf-8")
    return {
        "upload_id": upload_id,
        "source": file.filename,
        "count": len(res.logins),
        "rows": res.rows,
        "duplicates": res.duplicates,
        "invalid": res.invalid,
        "invalid_samples": res.invalid_samples,
        "column": res.column,
        "encoding": res.encoding,
        "sheets": res.sheets,
        "sample": res.logins[:5],
    }


# ---------- тестовая отправка ----------
@app.post("/api/test-send")
async def test_send(body: TestSendIn):
    message = _check_message(body.message)
    login = "".join(c for c in body.login if c.isdigit())
    if not (settings.MIN_LOGIN_DIGITS <= len(login) <= settings.MAX_LOGIN_DIGITS):
        raise HTTPException(400, "Некорректный логин")
    async with make_client() as client:
        status, detail = await send_push(client, login, message)
    logger.info("Тестовая отправка на {}: {} {}", login, status, detail)
    return {"login": login, "status": status, "detail": detail}


# ---------- рассылки ----------
@app.get("/api/jobs")
async def list_jobs():
    return manager.list_jobs()


@app.post("/api/jobs")
async def create_job(body: JobIn):
    message = _check_message(body.message)
    path = _upload_path(body.upload_id)
    if not path.exists():
        raise HTTPException(404, "Файл получателей не найден, загрузите его заново")
    payload = json.loads(path.read_text(encoding="utf-8"))
    job = manager.create(body.name.strip(), message, payload["source"], payload["logins"])
    path.unlink(missing_ok=True)
    if body.start:
        try:
            manager.start(job.id)
        except JobConflict as e:
            job.last_error = str(e)
    return job.public()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    return _job_or_404(job_id).public()


@app.post("/api/jobs/{job_id}/start")
async def start_job(job_id: str):
    _job_or_404(job_id)
    try:
        return manager.start(job_id).public()
    except JobConflict as e:
        raise HTTPException(409, str(e))


@app.post("/api/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    _job_or_404(job_id)
    return manager.pause(job_id).public()


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    _job_or_404(job_id)
    return manager.cancel(job_id).public()


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, body: RetryIn):
    job = _job_or_404(job_id)
    if job.status in ("running", "pausing", "cancelling"):
        raise HTTPException(409, "Сначала дождитесь окончания рассылки")
    logins = manager.failed_logins(job, body.include_unknown)
    if not logins:
        raise HTTPException(400, "Нет логинов для повтора")
    new = manager.create(f"{job.name} — повтор", job.message, job.source, logins)
    return new.public()


@app.get("/api/jobs/{job_id}/report")
async def report(job_id: str):
    job = _job_or_404(job_id)
    return FileResponse(
        manager.results_path(job),
        media_type="text/csv; charset=utf-8",
        filename=f"report_{job.id}.csv",
    )


if __name__ == "__main__":
    # log_config=None - uvicorn не настраивает свой logging, всё идёт через loguru
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, log_config=None)
