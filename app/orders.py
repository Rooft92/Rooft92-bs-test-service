"""Мини-синхронизатор заявок: забрать сырые заявки, нормализовать телефоны, схлопнуть дубли по телефону."""
import logging
import math
import re
import time
from datetime import datetime, timezone

import httpx

from . import db
from .config import settings

log = logging.getLogger("app.orders")
OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def normalize_phone(raw) -> str | None:
    """'8 (999) 123-45-67', '+7 999 123-45-67', '9991234567' -> '+79991234567'. Не 11 цифр -> None."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 10:  # номер без кода страны
        digits = "7" + digits
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    return None


def parse_amount(value) -> int | float:
    try:
        num = float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(num):
        return 0
    return int(num) if num.is_integer() else num


def parse_time(value) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return OLDEST
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def build_result(orders: list) -> dict:
    """При дубле оставляем запись с максимальным amount, при равных amount — более позднюю по created_at."""
    best: dict[str, tuple] = {}
    valid = invalid = 0
    for order in orders:
        phone = normalize_phone(order.get("phone")) if isinstance(order, dict) else None
        if phone is None:
            invalid += 1
            continue
        valid += 1
        rank = (parse_amount(order.get("amount")), parse_time(order.get("created_at")))
        if phone not in best or rank > best[phone][0]:
            best[phone] = (rank, order)
    clients = [
        {"name": order.get("name"), "phone": phone, "email": order.get("email"), "amount": rank[0]}
        for phone, (rank, order) in best.items()
    ]
    return {
        "clients": clients,
        "stats": {
            "received": len(orders),
            "unique": len(clients),
            "duplicates_merged": valid - len(clients),
            "invalid_dropped": invalid,
        },
    }


def fetch_orders(attempts: int = 3) -> list:
    """Повтор при сбое сети/5xx с паузой 1 с, 2 с."""
    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.get(settings.orders_url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError("ожидался JSON-массив заявок")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("заявки: попытка %s/%s не удалась: %r", attempt, attempts, exc)
            if attempt == attempts:
                raise
            time.sleep(attempt)
    return []


def sync() -> dict:
    """Синхронизация идемпотентна: тот же вход -> тот же результат, клиенты в БД уникальны по телефону.
    Если источник недоступен — пишем в журнал ошибок и отдаём последний успешный результат."""
    try:
        orders = fetch_orders()
    except Exception as exc:
        db.log_error(None, "orders_sync", repr(exc))
        saved = db.last_sync()
        if saved is None:
            raise
        return saved
    result = build_result(orders)
    db.save_sync(result)
    log.info("синхронизация: %s", result["stats"])
    return result
