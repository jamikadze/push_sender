"""Рассылки (jobs): хранение на диске, запуск, пауза, возобновление.

Каждая рассылка лежит в data/jobs/<id>/:
  job.json     - параметры и счётчики
  logins.txt   - список логинов (по одному на строку)
  values.json  - только для текста с подстановками: {логин: {колонка: значение}}
  results.csv  - результат по каждому логину (UTF-8 с BOM, разделитель ";",
                 открывается в Excel без «кракозябр»)

results.csv - источник истины: при возобновлении пропускаются все логины,
которые там уже есть. Строка пишется сразу после ответа сервиса, поэтому
после сбоя/перезапуска повторно могут уйти максимум PUSH_CONCURRENCY сообщений,
которые были «в полёте» в момент падения.
"""
import asyncio
import csv
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from loguru import logger

from . import template
from .config import settings
from .sender import make_client, send_push

ACTIVE = {"running", "pausing", "cancelling"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Job:
    id: str
    name: str
    message: str
    source: str
    total: int
    created_at: str
    status: str = "ready"  # ready|running|pausing|paused|cancelling|cancelled|done
    ok: int = 0
    error: int = 0
    unknown: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    last_error: str | None = None
    personalized: bool = False  # текст с подстановками {колонка}

    def public(self) -> dict:
        d = asdict(self)
        d["processed"] = self.ok + self.error + self.unknown
        return d


class JobConflict(Exception):
    pass


class JobManager:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self._task: asyncio.Task | None = None
        self._stop: str | None = None  # None | "pause" | "cancel"

    # ---------- хранение ----------
    def _dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _save(self, job: Job) -> None:
        path = self._dir(job.id) / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _read_logins(self, job: Job) -> list[str]:
        text = (self._dir(job.id) / "logins.txt").read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line]

    def read_values(self, job: Job) -> dict[str, dict[str, str]]:
        if not job.personalized:
            return {}
        return json.loads((self._dir(job.id) / "values.json").read_text(encoding="utf-8"))

    def _read_results(self, job: Job) -> dict[str, str]:
        path = self._dir(job.id) / "results.csv"
        done: dict[str, str] = {}
        if not path.exists():
            return done
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, delimiter=";")
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    done[row[0]] = row[1]
        return done

    def _recount(self, job: Job) -> None:
        statuses = list(self._read_results(job).values())
        job.ok = statuses.count("ok")
        job.error = statuses.count("error")
        job.unknown = statuses.count("unknown")

    def load(self) -> None:
        known = {f.name for f in fields(Job)}
        for d in self.root.iterdir():
            meta = d / "job.json"
            if not meta.exists():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                job = Job(**{k: v for k, v in data.items() if k in known})
            except Exception:
                logger.exception("Не удалось прочитать {}", meta)
                continue
            if job.status in ACTIVE:
                # Сервер был остановлен во время рассылки - ставим на паузу,
                # продолжить можно кнопкой.
                self._recount(job)
                job.status = "paused"
                job.last_error = "Сервер был перезапущен во время рассылки"
                self._save(job)
            self.jobs[job.id] = job

    # ---------- операции ----------
    def list_jobs(self) -> list[dict]:
        return [j.public() for j in sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)]

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return job

    def create(self, name: str, message: str, source: str, logins: list[str],
               values: dict[str, dict[str, str]] | None = None) -> Job:
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        d = self._dir(job_id)
        d.mkdir(parents=True)
        (d / "logins.txt").write_text("\n".join(logins) + "\n", encoding="utf-8")
        if values:
            (d / "values.json").write_text(
                json.dumps({l: values[l] for l in logins}, ensure_ascii=False), encoding="utf-8"
            )
        with open(d / "results.csv", "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f, delimiter=";").writerow(["login", "status", "detail", "time"])
        job = Job(
            id=job_id, name=name or source, message=message, source=source,
            total=len(logins), created_at=_now(), personalized=bool(values),
        )
        self._save(job)
        self.jobs[job.id] = job
        return job

    def failed_logins(self, job: Job, include_unknown: bool) -> list[str]:
        wanted = {"error", "unknown"} if include_unknown else {"error"}
        return [login for login, st in self._read_results(job).items() if st in wanted]

    def results_path(self, job: Job) -> Path:
        return self._dir(job.id) / "results.csv"

    def running_job(self) -> Job | None:
        if self._task and not self._task.done():
            return next((j for j in self.jobs.values() if j.status in ACTIVE), None)
        return None

    def start(self, job_id: str) -> Job:
        job = self.get(job_id)
        running = self.running_job()
        if running:
            raise JobConflict(f"Уже идёт рассылка «{running.name}». Дождитесь окончания или поставьте её на паузу.")
        if job.status not in ("ready", "paused"):
            raise JobConflict(f"Рассылку в статусе «{job.status}» запустить нельзя.")
        self._stop = None
        job.status = "running"
        job.last_error = None
        job.started_at = job.started_at or _now()
        self._save(job)
        self._task = asyncio.create_task(self._run(job))
        logger.info("Запуск рассылки {} «{}» ({} логинов)", job.id, job.name, job.total)
        return job

    def pause(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status == "running":
            self._stop = "pause"
            job.status = "pausing"
            self._save(job)
            logger.info("Пауза рассылки {}", job.id)
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status in ("running", "pausing"):
            self._stop = "cancel"
            job.status = "cancelling"
        elif job.status in ("ready", "paused"):
            job.status = "cancelled"
            job.finished_at = _now()
        self._save(job)
        logger.info("Отмена рассылки {}", job.id)
        return job

    async def shutdown(self) -> None:
        if self._task and not self._task.done():
            self._stop = "pause"
            try:
                await asyncio.wait_for(self._task, timeout=settings.PUSH_TIMEOUT + 5)
            except asyncio.TimeoutError:
                self._task.cancel()

    # ---------- сама рассылка ----------
    async def _run(self, job: Job) -> None:
        f = None
        try:
            logins = await asyncio.to_thread(self._read_logins, job)
            done = await asyncio.to_thread(self._read_results, job)
            values = await asyncio.to_thread(self.read_values, job)
            pending = iter([l for l in logins if l not in done])
            logger.info(
                "Рассылка {} «{}»: всего {}, осталось {}",
                job.id, job.name, len(logins), len(logins) - len(done),
            )

            f = open(self.results_path(job), "a", encoding="utf-8", newline="")
            writer = csv.writer(f, delimiter=";")
            last_save = last_progress = started = time.monotonic()
            sent_now = 0

            async with make_client() as client:
                async def worker() -> None:
                    nonlocal last_save, last_progress, sent_now
                    while self._stop is None:
                        login = next(pending, None)
                        if login is None:
                            return
                        text = job.message
                        if values:
                            text, _ = template.render(job.message, values.get(login, {}))
                        status, detail = await send_push(client, login, text)
                        writer.writerow([login, status, detail, _now()])
                        f.flush()
                        setattr(job, status, getattr(job, status) + 1)
                        sent_now += 1
                        if status != "ok":
                            logger.warning("[{}] {} {}: {}", job.id, login, status, detail)
                        if time.monotonic() - last_progress > 30:
                            last_progress = time.monotonic()
                            rate = sent_now / (last_progress - started)
                            processed = job.ok + job.error + job.unknown
                            logger.info(
                                "[{}] {}/{} ({:.1f}%), {:.1f} сообщ/с, ok={} error={} unknown={}",
                                job.id, processed, job.total, processed * 100 / max(job.total, 1),
                                rate, job.ok, job.error, job.unknown,
                            )
                        if time.monotonic() - last_save > 2:
                            self._save(job)
                            last_save = time.monotonic()

                await asyncio.gather(*(worker() for _ in range(settings.PUSH_CONCURRENCY)))

            if self._stop == "cancel":
                job.status = "cancelled"
                job.finished_at = _now()
            elif self._stop == "pause":
                job.status = "paused"
            else:
                job.status = "done"
                job.finished_at = _now()
            logger.info(
                "Рассылка {} остановлена со статусом {}: ok={} error={} unknown={}",
                job.id, job.status, job.ok, job.error, job.unknown,
            )
        except Exception as e:
            logger.exception("Рассылка {} упала", job.id)
            job.status = "paused"
            job.last_error = f"{type(e).__name__}: {e}"
        finally:
            if f:
                f.close()
            self._save(job)
