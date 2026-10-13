import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import orders
from app.main import app

SAMPLE = json.loads((Path(__file__).parent / "orders_sample.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("raw, expected", [
    ("+7 999 123-45-67", "+79991234567"),
    ("8 (999) 123 45 67", "+79991234567"),
    ("79991234567", "+79991234567"),
    ("9005551212", "+79005551212"),
    ("123", None),
    ("", None),
    (None, None),
    ("не указан", None),
    ("+7 999", None),
])
def test_normalize_phone(raw, expected):
    assert orders.normalize_phone(raw) == expected


def test_build_result_on_sample():
    result = orders.build_result(SAMPLE)
    assert result["stats"] == {"received": 17, "unique": 8, "duplicates_merged": 5, "invalid_dropped": 4}
    by_phone = {c["phone"]: c for c in result["clients"]}
    assert by_phone["+79991234567"]["amount"] == 5000  # максимальный amount из трёх дублей
    assert by_phone["+79160000001"]["amount"] == 200  # равные amount -> более поздняя запись
    assert by_phone["+79160000001"]["name"] == SAMPLE[4]["name"]
    assert by_phone["+79219998877"]["amount"] == 1500
    assert set(result["clients"][0]) == {"name", "phone", "email", "amount"}


def test_result_endpoint_idempotent(monkeypatch):
    monkeypatch.setattr(orders, "fetch_orders", lambda: SAMPLE)
    with TestClient(app) as client:
        first = client.get("/result").json()
        second = client.get("/result").json()
    assert first == second
    assert first["stats"]["unique"] == 8


def test_result_falls_back_to_last_sync(monkeypatch):
    monkeypatch.setattr(orders, "fetch_orders", lambda: SAMPLE)
    with TestClient(app) as client:
        good = client.get("/result").json()

        def broken():
            raise httpx.ConnectError("нет связи")

        monkeypatch.setattr(orders, "fetch_orders", broken)
        assert client.get("/result").json() == good
        assert client.get("/errors").json()[0]["context"] == "orders_sync"
