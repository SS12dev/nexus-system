"""The Admin Models tab: model calls from LiteLLM's spend logs, by model, person and purpose."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from nexus_api import alerts
from nexus_common.models import ApiKey

# The columns Nexus reads, as LiteLLM's Prisma schema makes them.
SPEND_LOGS = """
create table "LiteLLM_SpendLogs" (
    request_id text primary key,
    call_type text not null,
    api_key text not null default '',
    spend double precision not null default 0,
    total_tokens integer not null default 0,
    prompt_tokens integer not null default 0,
    completion_tokens integer not null default 0,
    "startTime" timestamp(3) not null,
    "endTime" timestamp(3) not null,
    "completionStartTime" timestamp(3),
    model text not null default '',
    model_group text default '',
    "user" text default '',
    metadata jsonb default '{}',
    cache_hit text default '',
    cache_key text default '',
    request_tags jsonb default '[]',
    status text
)
"""


@pytest.fixture
async def spend_logs(engine, app):
    async with engine.begin() as conn:
        await conn.execute(text('drop table if exists "LiteLLM_SpendLogs"'))
        await conn.execute(text(SPEND_LOGS))
    app.state.spend_logs._columns = None

    async def add(**row):
        start = row.pop("at", datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5))
        ms = row.pop("ms", 800)
        values = {
            "request_id": uuid.uuid4().hex,
            "call_type": "acompletion",
            "spend": 0.001,
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "model": "gpt-5.4-mini",
            "model_group": "nexus-mini",
            "user": "",
            "metadata": "{}",
            "cache_hit": "False",
            "request_tags": json.dumps(["nexus", "chat", "test"]),
            "status": "success",
            "startTime": start,
            "endTime": start + timedelta(milliseconds=ms),
            **row,
        }
        cols = ", ".join(f'"{k}"' for k in values)
        marks = ", ".join(
            f"cast(:{k} as jsonb)" if k in ("metadata", "request_tags") else f":{k}" for k in values
        )
        async with engine.begin() as conn:
            await conn.execute(
                text(f'insert into "LiteLLM_SpendLogs" ({cols}) values ({marks})'), values
            )

    yield add
    async with engine.begin() as conn:
        await conn.execute(text('drop table "LiteLLM_SpendLogs"'))
    app.state.spend_logs._columns = None


async def test_without_spend_logs_the_page_says_why(owner):
    data = (await owner.get("/api/admin/llm")).json()
    assert data["available"] is False
    assert "spend logs" in data["reason"]


async def test_calls_are_grouped_by_model_person_and_purpose(owner, spend_logs):
    me = (await owner.get("/api/me")).json()
    await spend_logs(user=me["id"], spend=0.01)
    await spend_logs(user=me["id"], spend=0.0, cache_hit="True", ms=5)
    await spend_logs(
        user=me["id"],
        model_group="nexus-nano",
        request_tags=json.dumps(["nexus", "route", "test"]),
        metadata=json.dumps({"additional_usage_values": {"cache_read_input_tokens": 60}}),
    )
    await spend_logs(
        model_group="nexus-pro",
        status="failure",
        metadata=json.dumps(
            {"error_information": {"error_class": "RateLimitError", "error_message": "slow down"}}
        ),
    )
    await spend_logs(call_type="aembedding", model_group="nexus-embed", spend=0, request_tags="[]")

    data = (await owner.get("/api/admin/llm")).json()
    assert data["available"] is True
    t = data["totals"]
    assert t["calls"] == 5 and t["failed"] == 1 and t["cache_hits"] == 1
    assert t["cached_tokens"] == 60
    # A cache hit saves what an average paid call to that model costs.
    assert t["saved_usd"] > 0

    models = {m["key"]: m for m in data["by_model"]}
    assert models["nexus-mini"]["calls"] == 2 and models["nexus-mini"]["cache_hit_rate"] == 0.5
    purposes = {p["key"]: p["calls"] for p in data["by_purpose"]}
    assert purposes == {"chat": 3, "route": 1, "embeddings": 1}
    people = {p["key"]: p for p in data["by_user"]}
    assert people[me["id"]]["email"] == "owner@example.com"
    assert people[me["id"]]["calls"] == 3

    assert data["failures"][0]["error"] == "RateLimitError slow down"
    assert data["unit"] == "day" and sum(s["calls"] for s in data["series"]) == 5
    assert "nexus-pro" in data["models"] and "route" in data["purposes"]
    assert data["purpose_labels"]["route"] == "Routing"


async def test_filters_narrow_every_figure(owner, spend_logs):
    me = (await owner.get("/api/me")).json()
    other = str(uuid.uuid4())
    await spend_logs(user=me["id"])
    await spend_logs(user=other, model_group="nexus-pro")
    await spend_logs(user=other, at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=20))

    mine = (await owner.get(f"/api/admin/llm?user_id={me['id']}")).json()
    assert mine["totals"]["calls"] == 1
    pro = (await owner.get("/api/admin/llm?model=nexus-pro")).json()
    assert pro["totals"]["calls"] == 1
    # The model list for the filter still offers every model in the window.
    assert set(pro["models"]) == {"nexus-mini", "nexus-pro"}

    since = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    month = (await owner.get("/api/admin/llm", params={"since": since})).json()
    assert month["totals"]["calls"] == 3
    day = await owner.get("/api/admin/llm", params={"since": since[:10] + "T00:00:00Z"})
    assert day.status_code == 200


async def test_a_short_window_is_hourly_and_a_bad_one_is_refused(owner, spend_logs):
    await spend_logs()
    since = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
    data = (await owner.get("/api/admin/llm", params={"since": since})).json()
    assert data["unit"] == "hour"
    resp = await owner.get(
        "/api/admin/llm",
        params={"since": since, "until": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
    )
    assert resp.status_code == 422


async def test_only_owners_see_model_usage(client_for, owner, invite):
    await invite(owner, "admin@example.com", role="admin")
    admin = await client_for("admin@example.com")
    assert (await admin.get("/api/admin/llm")).status_code == 403


async def test_model_failures_spend_and_budgets_raise_alerts(
    app, owner, spend_logs, gateway, sessionmaker
):
    me = (await owner.get("/api/me")).json()
    async with sessionmaker() as db:
        db.add(ApiKey(user_id=uuid.UUID(me["id"]), provider="litellm", secret="sk-owner"))
        await db.commit()
    gateway.keys["sk-owner"] = {"spend": 48.0}  # of the owner's $50
    for _ in range(2):
        await spend_logs(status="failure")
    await spend_logs(spend=3.0)
    app.state.settings = app.state.settings.model_copy(
        update={
            "alert_llm_failures_15m": 2,
            "alert_daily_spend_usd": 2.5,
            "alert_budget_percent": 90,
        }
    )
    keys = {p.key for p in await alerts.model_problems(app)}
    assert keys == {"llm-failures", "daily-spend", f"budget:{me['id']}"}

    app.state.settings = app.state.settings.model_copy(
        update={"alert_llm_failures_15m": 0, "alert_daily_spend_usd": 0, "alert_budget_percent": 0}
    )
    assert await alerts.model_problems(app) == []
    rules = (await owner.get("/api/admin/alerts")).json()["rules"]
    assert {r["name"]: r["on"] for r in rules}["Daily spend"] is False
