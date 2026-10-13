"""Сервис приёма вебхуков (в т.ч. Битрикс24): без дублей, с журналом ошибок и повторной обработкой."""
import hashlib
import hmac
import html
import json
import logging
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from . import db, orders
from .config import settings

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    log.info("старт %s, версия %s", settings.app_name, settings.git_sha)
    yield


app = FastAPI(title=settings.app_name, version=settings.git_sha, lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Лог запросов без IP клиентов: персональные данные в логи не пишем."""
    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path != "/health":  # healthcheck Docker не шумит в логе
        log.info("%s %s -> %s за %.0f мс", request.method, request.url.path, response.status_code,
                 (time.perf_counter() - started) * 1000)
    return response


def parse_form(pairs) -> dict:
    """data[FIELDS][ID]=42 -> {"data": {"FIELDS": {"ID": "42"}}} — так присылает события Битрикс24."""
    result: dict = {}
    for key, value in pairs:
        *path, last = key.replace("]", "").split("[")
        node = result
        for part in path:
            child = node.get(part)
            if not isinstance(child, dict):
                child = node[part] = {}
            node = child
        node[last] = value
    return result


async def read_payload(request: Request):
    body = await request.body()
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, "невалидный JSON") from None
        if not isinstance(payload, (dict, list)):
            raise HTTPException(400, "ожидается JSON-объект или массив")
        return payload
    text = body.decode("utf-8", "replace")
    if "x-www-form-urlencoded" in ctype:
        return parse_form(parse_qsl(text, keep_blank_values=True))
    return {"raw": text}


def make_dedup_key(request: Request, source: str, payload) -> str:
    """Ключ идемпотентности: заголовок Idempotency-Key или хеш тела (без auth — токены меняются)."""
    if key := request.headers.get("idempotency-key"):
        return f"{source}:key:{key}"
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return f"{source}:sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def check_b24_token(auth) -> None:
    if not settings.b24_app_token:
        return
    token = str(auth.get("application_token", "")) if isinstance(auth, dict) else ""
    if not hmac.compare_digest(token, settings.b24_app_token):
        raise HTTPException(403, "неверный application_token")


def handle_event(event: dict) -> None:
    """Бизнес-логика под конкретную задачу (например: забрать сделку из Битрикс24 и отправить дальше)."""
    log.info("обработка #%s (%s/%s)", event["id"], event["source"], event["event"])


def process_event(event_id: int) -> None:
    event = db.get_event(event_id)
    try:
        handle_event(event)
    except Exception as exc:  # любую ошибку пишем в журнал, событие остаётся для повтора
        log.exception("ошибка обработки #%s", event_id)
        db.log_error(event_id, "process_event", repr(exc))
        db.set_status(event_id, "failed")
    else:
        db.set_status(event_id, "done")


@app.post("/webhook")
@app.post("/webhook/{source}")
async def webhook(request: Request, background: BackgroundTasks, source: str = "generic"):
    payload = await read_payload(request)
    auth = payload.pop("auth", {}) if isinstance(payload, dict) else {}  # токены не храним
    if source == "b24":
        check_b24_token(auth)
    event = payload.get("event") if isinstance(payload, dict) else None
    key = make_dedup_key(request, source, payload)
    event_id, created = await run_in_threadpool(
        db.insert_event, key, source, str(event) if event is not None else None, payload
    )
    if created:
        background.add_task(process_event, event_id)
    else:
        log.info("дубль: событие #%s уже принято", event_id)
    return {"ok": True, "id": event_id, "duplicate": not created}


@app.get("/events")
def list_events(limit: int = Query(50, ge=1, le=500), status: str | None = None):
    return db.list_events(limit, status)


@app.get("/events/{event_id}")
def get_event(event_id: int):
    event = db.get_event(event_id)
    if event is None:
        raise HTTPException(404, "нет такого события")
    return event


@app.post("/events/{event_id}/retry")
def retry_event(event_id: int):
    if db.get_event(event_id) is None:
        raise HTTPException(404, "нет такого события")
    process_event(event_id)
    return db.get_event(event_id)


@app.get("/errors")
def list_errors(limit: int = Query(50, ge=1, le=500)):
    return db.list_errors(limit)


@app.get("/result")
def result():
    """Мини-синхронизатор: забирает заявки, нормализует телефоны, схлопывает дубли, отдаёт clients + stats."""
    try:
        return orders.sync()
    except Exception as exc:
        raise HTTPException(502, f"источник заявок недоступен: {exc!r}") from exc


@app.get("/health")
def health():
    return {"status": "ok", "version": settings.git_sha}


@app.get("/", response_class=HTMLResponse)
def index():
    name = html.escape(settings.app_name)
    links = "".join(f'<li><a href="{p}">{p}</a></li>' for p in ("/result", "/docs", "/events", "/errors", "/health"))
    return (
        f"<!doctype html><meta charset='utf-8'><title>{name}</title>"
        f"<body style='font-family:system-ui;max-width:640px;margin:40px auto;padding:0 16px'>"
        f"<h1>{name}</h1><p>Версия: <code>{html.escape(settings.git_sha)}</code></p><ul>{links}</ul>"
    )
