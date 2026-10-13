import json

import httpx
import pytest

from app.b24 import B24Client, B24Error, build_query


def make_client(handler) -> B24Client:
    transport = httpx.MockTransport(handler)
    return B24Client("https://example.bitrix24.ru/rest/1/token/", backoff=0, client=httpx.Client(transport=transport))


def test_build_query():
    query = build_query({"id": 5, "filter": {">ID": 10}, "select": ["ID", "TITLE"]})
    assert query == "id=5&filter%5B%3EID%5D=10&select%5B0%5D=ID&select%5B1%5D=TITLE"


def test_retry_on_query_limit():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "QUERY_LIMIT_EXCEEDED", "error_description": "Too many"})
        return httpx.Response(200, json={"result": {"ID": "1"}})

    assert make_client(handler).call("crm.deal.get", {"id": 1})["result"] == {"ID": "1"}
    assert calls == ["/rest/1/token/crm.deal.get.json"] * 2


def test_non_retryable_error_raises_at_once():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": "ERROR_CORE", "error_description": "Not found"})

    with pytest.raises(B24Error) as exc:
        make_client(handler).call("crm.deal.get", {"id": 0})
    assert exc.value.code == "ERROR_CORE" and len(calls) == 1


def test_gives_up_after_max_retries():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(B24Error) as exc:
        make_client(handler).call("profile")
    assert exc.value.code == "NETWORK"


def test_list_all_paginates():
    def handler(request):
        if json.loads(request.content)["start"] == 0:
            return httpx.Response(200, json={"result": [{"ID": "1"}], "next": 50, "total": 2})
        return httpx.Response(200, json={"result": [{"ID": "2"}], "total": 2})

    assert [item["ID"] for item in make_client(handler).list_all("crm.deal.list")] == ["1", "2"]


def test_batch():
    def handler(request):
        assert json.loads(request.content)["cmd"] == {"d": "crm.deal.get?id=7"}
        return httpx.Response(200, json={"result": {"result": {"d": {"ID": "7"}}, "result_error": []}})

    result, errors = make_client(handler).batch({"d": ("crm.deal.get", {"id": 7})})
    assert result == {"d": {"ID": "7"}} and not errors
