import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import app, parse_form

B24_FORM = {
    "event": "ONCRMDEALUPDATE",
    "data[FIELDS][ID]": "42",
    "ts": "1700000000",
    "auth[domain]": "example.bitrix24.ru",
    "auth[application_token]": "test-app-token",
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_json_webhook_is_deduplicated(client):
    first = client.post("/webhook", json={"order": 1, "sum": 100}).json()
    second = client.post("/webhook", json={"sum": 100, "order": 1}).json()
    assert first["duplicate"] is False and second["duplicate"] is True
    assert first["id"] == second["id"]
    assert client.get(f"/events/{first['id']}").json()["status"] == "done"


def test_idempotency_key_header(client):
    a = client.post("/webhook", json={"v": 1}, headers={"Idempotency-Key": "abc"}).json()
    b = client.post("/webhook", json={"v": 2}, headers={"Idempotency-Key": "abc"}).json()
    assert b["duplicate"] and a["id"] == b["id"]


def test_b24_event_parsed_and_auth_not_stored(client):
    resp = client.post("/webhook/b24", data=B24_FORM)
    assert resp.status_code == 200
    event = client.get(f"/events/{resp.json()['id']}").json()
    assert event["event"] == "ONCRMDEALUPDATE"
    assert event["payload"]["data"]["FIELDS"]["ID"] == "42"
    assert "auth" not in event["payload"]


def test_b24_wrong_token_rejected(client):
    resp = client.post("/webhook/b24", data={**B24_FORM, "auth[application_token]": "bad"})
    assert resp.status_code == 403


def test_bad_json_rejected(client):
    resp = client.post("/webhook", content=b"{oops", headers={"content-type": "application/json"})
    assert resp.status_code == 400


def test_failed_event_logged_and_retried(client, monkeypatch):
    def boom(event):
        raise RuntimeError("сбой")

    monkeypatch.setattr(main, "handle_event", boom)
    event_id = client.post("/webhook", json={"fail": True}).json()["id"]
    assert client.get(f"/events/{event_id}").json()["status"] == "failed"
    assert any(e["event_id"] == event_id for e in client.get("/errors").json())

    monkeypatch.setattr(main, "handle_event", lambda event: None)
    assert client.post(f"/events/{event_id}/retry").json()["status"] == "done"


def test_parse_form_nested():
    pairs = [("data[FIELDS][ID]", "1"), ("event", "X")]
    assert parse_form(pairs) == {"data": {"FIELDS": {"ID": "1"}}, "event": "X"}
