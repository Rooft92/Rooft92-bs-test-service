"""Клиент REST API Битрикс24 через входящий вебхук: повторы при сбоях и лимитах, batch, постраничная выборка."""
import logging
import time
from urllib.parse import quote

import httpx

from .config import settings

log = logging.getLogger("b24")

RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_ERRORS = {"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT", "INTERNAL_SERVER_ERROR"}


class B24Error(Exception):
    def __init__(self, code: str, description: str = ""):
        super().__init__(f"{code}: {description}" if description else code)
        self.code = code
        self.description = description


def build_query(params, prefix: str = "") -> str:
    """Аналог PHP http_build_query: {"filter": {">ID": 5}} -> "filter%5B%3EID%5D=5" (нужно для batch)."""
    parts = []
    items = params.items() if isinstance(params, dict) else enumerate(params)
    for key, value in items:
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, (dict, list, tuple)):
            parts.append(build_query(value, name))
        else:
            if isinstance(value, bool):
                value = int(value)
            parts.append(f"{quote(name, safe='')}={quote('' if value is None else str(value), safe='')}")
    return "&".join(part for part in parts if part)


class B24Client:
    def __init__(self, webhook_url: str, *, timeout: float = 20.0, max_retries: int = 4,
                 backoff: float = 1.0, client: httpx.Client | None = None):
        if not webhook_url:
            raise ValueError("B24_WEBHOOK_URL не задан")
        self.base = webhook_url.rstrip("/") + "/"
        self.max_retries = max_retries
        self.backoff = backoff
        self.http = client or httpx.Client(timeout=timeout)

    def call(self, method: str, params: dict | None = None) -> dict:
        """Вызов метода. Возвращает ответ целиком: result, next, total, time."""
        url = f"{self.base}{method}.json"
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.http.post(url, json=params or {})
            except httpx.TransportError as exc:
                error = B24Error("NETWORK", str(exc))
            else:
                try:
                    data = resp.json()
                except ValueError:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                if not resp.is_error and "error" not in data and "result" in data:
                    return data
                error = B24Error(str(data.get("error") or f"HTTP_{resp.status_code}"),
                                 str(data.get("error_description") or resp.text[:200]))
                if error.code not in RETRY_ERRORS and resp.status_code not in RETRY_STATUSES:
                    raise error
            if attempt == self.max_retries:
                raise error
            delay = self.backoff * 2 ** attempt
            log.warning("%s: %s — повтор через %.1f с (%d/%d)", method, error, delay, attempt + 1, self.max_retries)
            time.sleep(delay)
        raise AssertionError("недостижимо")

    def list_all(self, method: str, params: dict | None = None, *, limit: int | None = None) -> list:
        """Забирает все страницы списочного метода (Битрикс отдаёт по 50 записей)."""
        items, start = [], 0
        while True:
            data = self.call(method, {**(params or {}), "start": start})
            chunk = data.get("result") or []
            if isinstance(chunk, dict):  # crm.item.list -> {"items": [...]}, tasks.task.list -> {"tasks": [...]}
                chunk = next((v for v in chunk.values() if isinstance(v, list)), [])
            items.extend(chunk)
            if limit and len(items) >= limit:
                return items[:limit]
            if "next" not in data:
                return items
            start = data["next"]

    def batch(self, commands: dict[str, tuple[str, dict | None]], halt: bool = False) -> tuple[dict, dict]:
        """До 50 команд за один запрос: {"d": ("crm.deal.get", {"id": 1})}. Возвращает (результаты, ошибки)."""
        if len(commands) > 50:
            raise ValueError("batch принимает не больше 50 команд")
        cmd = {key: f"{method}?{build_query(params)}" if params else method
               for key, (method, params) in commands.items()}
        result = self.call("batch", {"halt": int(halt), "cmd": cmd})["result"]
        return result.get("result") or {}, result.get("result_error") or {}


_client: B24Client | None = None


def client() -> B24Client:
    global _client
    if _client is None:
        _client = B24Client(settings.b24_webhook_url)
    return _client
