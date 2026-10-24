"""Request ids, the access line and the JSON 500 (RequestContextMiddleware)."""

import json
import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nexus_api.middleware import RequestContextMiddleware
from nexus_common import logs


def make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/ok/{item}")
    async def ok(item: str):
        logging.getLogger("test").info("inside")
        return {"request_id": logs.get("request_id"), "item": item}

    @app.get("/api/boom")
    async def boom():
        raise RuntimeError("kaboom")

    app.add_middleware(RequestContextMiddleware)
    return app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        yield c


async def test_each_response_carries_a_request_id_bound_while_it_ran(client):
    r = await client.get("/api/ok/1")
    rid = r.headers["x-request-id"]
    assert len(rid) == 32
    assert r.json()["request_id"] == rid
    assert (await client.get("/api/ok/1")).headers["x-request-id"] != rid


async def test_a_plain_caller_id_is_kept_and_an_odd_one_replaced(client):
    r = await client.get("/api/ok/1", headers={"x-request-id": "trace-1234abcd"})
    assert r.headers["x-request-id"] == "trace-1234abcd"
    r = await client.get("/api/ok/1", headers={"x-request-id": "bad id\nwith newline"})
    assert r.headers["x-request-id"] != "bad id\nwith newline"


async def test_an_unhandled_error_is_a_json_500_naming_the_request(client, caplog):
    with caplog.at_level(logging.INFO):
        r = await client.get("/api/boom")
    assert r.status_code == 500
    assert r.json()["request_id"] == r.headers["x-request-id"]
    assert "kaboom" not in r.text
    failed = [rec for rec in caplog.records if rec.exc_info]
    assert failed and "/api/boom" in failed[0].getMessage()


async def test_the_access_line_names_the_route_template(client, caplog):
    with caplog.at_level(logging.INFO, logger="nexus.access"):
        await client.get("/api/ok/42")
    line = next(r for r in caplog.records if r.name == "nexus.access")
    assert line.route == "/api/ok/{item}"
    assert line.status == 200


def test_json_lines_carry_the_bound_ids():
    formatter = logs.JsonFormatter("api")
    record = logging.makeLogRecord({"name": "x", "msg": "hello %s", "args": ("you",)})
    with logs.scope(request_id="abc12345"):
        logs.bind(user_id="u1", chat_id=None)
        out = json.loads(formatter.format(record))
    assert out["msg"] == "hello you"
    assert out["service"] == "api"
    assert out["request_id"] == "abc12345"
    assert out["user_id"] == "u1"
    assert "chat_id" not in out
    assert logs.get("request_id") is None


async def test_jobs_get_the_request_id_their_enqueuer_had():
    queued = {}

    async def enqueue(function, *args, **kwargs):
        queued.update(kwargs, function=function)

    with logs.scope(request_id="req12345"):
        await logs.with_request_id(enqueue)("run_turn", "c", "m", _job_id="turn:m")
    assert queued["nexus_request_id"] == "req12345"

    seen = {}

    @logs.traced_job
    async def run_turn(ctx, chat_id: str, assistant_message_id: str):
        seen.update(logs.current())

    await run_turn({"job_id": "turn:m"}, "c", "m", nexus_request_id=queued["nexus_request_id"])
    assert seen["request_id"] == "req12345"
    assert seen["chat_id"] == "c"
    assert seen["assistant_message_id"] == "m"
    assert seen["job"] == "run_turn"
    assert run_turn.__qualname__.endswith("run_turn")


async def test_a_job_argument_called_request_id_is_not_taken_for_the_log_id():
    seen = {}

    @logs.traced_job
    async def notify_access(ctx, kind: str, request_id: str):
        seen.update(logs.current(), arg=request_id)

    await notify_access({}, "requested", "access-1", nexus_request_id="req12345")
    assert seen["arg"] == "access-1"
    assert seen["request_id"] == "req12345"
