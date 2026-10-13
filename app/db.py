"""SQLite-хранилище: события (уникальный ключ — защита от дублей) и журнал ошибок."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key  TEXT    NOT NULL UNIQUE,
    source     TEXT    NOT NULL,
    event      TEXT,
    payload    TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'new',
    attempts   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS events_status ON events (status);
CREATE TABLE IF NOT EXISTS errors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER,
    context    TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clients (
    phone      TEXT PRIMARY KEY,
    name       TEXT,
    email      TEXT,
    amount     REAL NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_result (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    body      TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:  # commit при успехе, rollback при исключении
            yield conn
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


def insert_event(dedup_key: str, source: str, event: str | None, payload) -> tuple[int, bool]:
    """Возвращает (id, создано ли). Повтор с тем же ключом отдаёт id уже принятого события."""
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO events (dedup_key, source, event, payload, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (dedup_key, source, event, json.dumps(payload, ensure_ascii=False), ts, ts),
        )
        if cur.rowcount:
            return cur.lastrowid, True
        row = conn.execute("SELECT id FROM events WHERE dedup_key = ?", (dedup_key,)).fetchone()
        return row["id"], False


def _event(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["payload"] = json.loads(item["payload"])
    return item


def get_event(event_id: int) -> dict | None:
    with connect() as conn:
        return _event(conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())


def list_events(limit: int, status: str | None = None) -> list[dict]:
    query, args = "SELECT * FROM events", []
    if status:
        query, args = query + " WHERE status = ?", [status]
    with connect() as conn:
        rows = conn.execute(query + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return [_event(row) for row in rows]


def set_status(event_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE events SET status = ?, attempts = attempts + 1, updated_at = ? WHERE id = ?",
            (status, now(), event_id),
        )


def log_error(event_id: int | None, context: str, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO errors (event_id, context, message, created_at) VALUES (?, ?, ?, ?)",
            (event_id, context, message[:2000], now()),
        )


def save_sync(result: dict) -> None:
    """Клиенты — upsert по телефону (повторная синхронизация не плодит дубли), плюс снимок результата."""
    ts = now()
    phones = [c["phone"] for c in result["clients"]]
    with connect() as conn:
        conn.executemany(
            "INSERT INTO clients (phone, name, email, amount, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(phone) DO UPDATE SET name = excluded.name, email = excluded.email,"
            " amount = excluded.amount, updated_at = excluded.updated_at",
            [(c["phone"], c["name"], c["email"], c["amount"], ts) for c in result["clients"]],
        )
        conn.execute(
            "DELETE FROM clients WHERE phone NOT IN (SELECT value FROM json_each(?))", (json.dumps(phones),)
        )
        conn.execute(
            "INSERT INTO sync_result (id, body, synced_at) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET body = excluded.body, synced_at = excluded.synced_at",
            (json.dumps(result, ensure_ascii=False), ts),
        )


def last_sync() -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT body FROM sync_result WHERE id = 1").fetchone()
    return json.loads(row["body"]) if row else None


def list_errors(limit: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM errors ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]
