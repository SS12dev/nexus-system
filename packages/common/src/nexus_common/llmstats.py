"""Model-call figures for the Admin page's Models tab, read from LiteLLM's spend logs.

LiteLLM writes one row per model call to `LiteLLM_SpendLogs` in its own database (next to
Nexus's, see compose.yaml): the alias asked for (nexus-mini), tokens, cost, start and end
times, whether its response cache answered, the virtual key's user id (a Nexus user id) and the
tags Nexus sends with the call. Nexus never asks LiteLLM to keep prompts or replies there, and
nothing here reads those columns: only counts, costs and timings.

What a call was for comes from its tags: every model call Nexus makes carries one of PURPOSES
(nexus_orchestrator/llm.py and the callers that name a generation).

LiteLLM adds columns over time. The ones read here have been there for a long while, apart from
`status` (failed calls), which is optional: without it every call counts as a success. A Nexus
whose LiteLLM database can't be reached, or has no spend logs yet, gets `available: False` and
a reason, never an error.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

log = logging.getLogger(__name__)

TABLE = "LiteLLM_SpendLogs"
# Tag -> what the page calls it.
PURPOSES = {
    "chat": "Chat replies",
    "route": "Routing",
    "design": "Team design",
    "system": "Agent teams",
    "skills": "Skill drafting",
    "memory": "Memory",
    "improve": "Self-review",
    "embeddings": "Embeddings",
    "other": "Other",
}
NEEDED = {
    "request_id",
    "call_type",
    "spend",
    "prompt_tokens",
    "completion_tokens",
    "startTime",
    "endTime",
    "model",
    "model_group",
    "user",
    "metadata",
    "cache_hit",
    "request_tags",
}
# Hour buckets up to this range, days beyond.
HOURLY_UP_TO = timedelta(days=2)
FAILURES_SHOWN = 20


class Filters:
    def __init__(
        self,
        since: datetime,
        until: datetime,
        users: list[uuid.UUID] | None = None,
        models: list[str] | None = None,
        purposes: list[str] | None = None,
    ):
        self.since = since
        self.until = until
        self.users = [str(u) for u in users or []]
        self.models = list(models or [])
        self.purposes = list(purposes or [])


def _rows_sql(has_status: bool) -> str:
    """One row per call with the derived columns everything else groups by."""
    failed = "coalesce(status, '') = 'failure'" if has_status else "false"
    return f"""
        select
            "startTime" as at,
            coalesce(nullif(model_group, ''), nullif(model, ''), 'unknown') as model,
            nullif("user", '') as user_id,
            coalesce(
                (select t from jsonb_array_elements_text(
                    case when jsonb_typeof(request_tags::jsonb) = 'array'
                         then request_tags::jsonb else '[]'::jsonb end) t
                 where t = any(:known) limit 1),
                case when position('embedding' in coalesce(call_type, '')) > 0
                     then 'embeddings' else 'other' end
            ) as purpose,
            coalesce(spend, 0) as spend,
            coalesce(prompt_tokens, 0) as prompt_tokens,
            coalesce(completion_tokens, 0) as completion_tokens,
            coalesce(
                nullif(metadata::jsonb -> 'additional_usage_values'
                       ->> 'cache_read_input_tokens', '')::bigint,
                nullif(metadata::jsonb -> 'usage_object' -> 'prompt_tokens_details'
                       ->> 'cached_tokens', '')::bigint,
                0
            ) as cached_tokens,
            lower(coalesce(cache_hit, '')) = 'true' as cache_hit,
            {failed} as failed,
            greatest(0, extract(epoch from ("endTime" - "startTime")) * 1000) as ms,
            case when "completionStartTime" > "startTime" and "completionStartTime" < "endTime"
                 then extract(epoch from ("completionStartTime" - "startTime")) * 1000 end
                as first_token_ms,
            metadata::jsonb -> 'error_information' ->> 'error_class' as error_class,
            left(metadata::jsonb -> 'error_information' ->> 'error_message', 300) as error_message
        from "{TABLE}"
        where "startTime" >= :since and "startTime" < :until
    """


def _where(f: Filters) -> tuple[str, dict]:
    clauses, params = [], {}
    if f.users:
        clauses.append("user_id in :users")
        params["users"] = f.users
    if f.models:
        clauses.append("model in :models")
        params["models"] = f.models
    if f.purposes:
        clauses.append("purpose in :purposes")
        params["purposes"] = f.purposes
    return (" where " + " and ".join(clauses)) if clauses else "", params


AGG = """
    count(*) as calls,
    count(*) filter (where failed) as failed,
    count(*) filter (where cache_hit) as cache_hits,
    coalesce(sum(spend), 0) as spend,
    coalesce(sum(prompt_tokens), 0) as prompt_tokens,
    coalesce(sum(completion_tokens), 0) as completion_tokens,
    coalesce(sum(cached_tokens), 0) as cached_tokens,
    avg(ms) filter (where not cache_hit and not failed) as avg_ms,
    percentile_cont(0.5) within group (order by ms)
        filter (where not cache_hit and not failed) as p50_ms,
    percentile_cont(0.95) within group (order by ms)
        filter (where not cache_hit and not failed) as p95_ms,
    percentile_cont(0.5) within group (order by first_token_ms) as p50_first_token_ms,
    avg(spend) filter (where not cache_hit and not failed and spend > 0) as avg_paid
"""


def _figures(row) -> dict:
    m = row._mapping
    calls = m["calls"] or 0
    prompt = int(m["prompt_tokens"] or 0)

    def num(key):
        v = m[key]
        return None if v is None else round(float(v))

    return {
        "calls": calls,
        "failed": m["failed"] or 0,
        "cache_hits": m["cache_hits"] or 0,
        "cache_hit_rate": round((m["cache_hits"] or 0) / calls, 4) if calls else None,
        "spend_usd": round(float(m["spend"] or 0), 6),
        "input_tokens": prompt,
        "output_tokens": int(m["completion_tokens"] or 0),
        "cached_tokens": int(m["cached_tokens"] or 0),
        "cached_share": round(int(m["cached_tokens"] or 0) / prompt, 4) if prompt else None,
        "avg_ms": num("avg_ms"),
        "p50_ms": num("p50_ms"),
        "p95_ms": num("p95_ms"),
        "p50_first_token_ms": num("p50_first_token_ms"),
        # What a response-cache hit saved: the average paid call to the same group.
        "saved_usd": round((m["cache_hits"] or 0) * float(m["avg_paid"] or 0), 6),
    }


class SpendLogs:
    """Reads LiteLLM's spend logs. The engine is made on first use and kept small: the page
    is for one or two owners."""

    def __init__(self, url: str):
        self.url = url
        self._engine: AsyncEngine | None = None
        self._columns: set[str] | None = None

    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(
                self.url, pool_size=2, max_overflow=2, pool_pre_ping=True, pool_recycle=600
            )
        return self._engine

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    async def columns(self, conn) -> set[str]:
        if self._columns is None:
            found = set(
                (
                    await conn.execute(
                        text(
                            "select column_name from information_schema.columns "
                            "where table_name = :t"
                        ),
                        {"t": TABLE},
                    )
                ).scalars()
            )
            if not found:
                return found  # not created yet: look again next time
            self._columns = found
        return self._columns

    async def report(self, f: Filters) -> dict:
        try:
            async with asyncio.timeout(20):
                async with self.engine().connect() as conn:
                    cols = await self.columns(conn)
                    if not cols:
                        return _unavailable(
                            "LiteLLM hasn't written any spend logs yet. They appear a minute "
                            "or so after the first model call."
                        )
                    missing = NEEDED - cols
                    if missing:
                        return _unavailable(
                            "This LiteLLM version's spend logs lack "
                            + ", ".join(sorted(missing))
                            + "; update the LiteLLM image."
                        )
                    return await self._report(conn, f, "status" in cols)
        except TimeoutError:
            return _unavailable("LiteLLM's database took too long to answer.")
        except (SQLAlchemyError, OSError) as exc:
            log.warning("Spend logs unavailable: %s", exc)
            return _unavailable(
                f"LiteLLM's database can't be reached (LITELLM_DATABASE_URL). {type(exc).__name__}."
            )

    async def _report(self, conn, f: Filters, has_status: bool) -> dict:
        where, params = _where(f)
        params |= {"since": naive(f.since), "until": naive(f.until), "known": list(PURPOSES)}
        base = f"with calls as ({_rows_sql(has_status)}) "
        expanding = [bindparam(k, expanding=True) for k in ("users", "models", "purposes")]
        expanding = [p for p in expanding if p.key in params]

        async def run(sql: str):
            sql = base + sql
            stmt = text(sql).bindparams(*(p for p in expanding if f":{p.key}" in sql))
            return (await conn.execute(stmt, params)).all()

        totals = _figures((await run(f"select {AGG} from calls{where}"))[0])
        groups = {}
        for key in ("model", "user_id", "purpose"):
            rows = await run(
                f"select {key} as k, {AGG} from calls{where} group by {key} order by spend desc"
            )
            groups[key] = [{"key": r.k, **_figures(r)} for r in rows]

        hourly = f.until - f.since <= HOURLY_UP_TO
        unit = "hour" if hourly else "day"
        series = await run(
            f"select date_trunc('{unit}', at) as bucket, "
            "count(*) as calls, count(*) filter (where failed) as failed, "
            "count(*) filter (where cache_hit) as cache_hits, coalesce(sum(spend), 0) as spend, "
            "coalesce(sum(prompt_tokens + completion_tokens), 0) as tokens, "
            "percentile_cont(0.95) within group (order by ms) "
            "  filter (where not cache_hit and not failed) as p95_ms "
            f"from calls{where} group by 1 order by 1"
        )
        failures = await run(
            "select at, model, user_id, purpose, error_class, error_message "
            f"from calls{where + (' and' if where else ' where')} failed "
            f"order by at desc limit {FAILURES_SHOWN}"
        )
        # Every choice in the window, for the filters, whatever is filtered now.
        choices = await run("select distinct model, purpose from calls")
        return {
            "available": True,
            "reason": None,
            "failures_tracked": has_status,
            "unit": unit,
            "totals": totals,
            "by_model": groups["model"],
            "by_user": groups["user_id"],
            "by_purpose": groups["purpose"],
            "series": [
                {
                    "at": aware(r.bucket),
                    "calls": r.calls,
                    "failed": r.failed,
                    "cache_hits": r.cache_hits,
                    "spend_usd": round(float(r.spend), 6),
                    "tokens": int(r.tokens),
                    "p95_ms": None if r.p95_ms is None else round(float(r.p95_ms)),
                }
                for r in series
            ],
            "failures": [
                {
                    "at": aware(r.at),
                    "model": r.model,
                    "user_id": r.user_id,
                    "purpose": r.purpose,
                    "error": " ".join(x for x in (r.error_class, r.error_message) if x)[:300],
                }
                for r in failures
            ],
            "models": sorted({r.model for r in choices}),
            "purposes": sorted({r.purpose for r in choices}),
        }

    async def quick(self, since: datetime, until: datetime) -> dict | None:
        """Calls, failures and spend in a window, for the alert checks. None if unknown."""
        try:
            async with asyncio.timeout(5):
                async with self.engine().connect() as conn:
                    cols = await self.columns(conn)
                    if not cols or NEEDED - cols:
                        return None
                    sql = (
                        f"with calls as ({_rows_sql('status' in cols)}) select count(*) as "
                        "calls, count(*) filter (where failed) as failed, "
                        "coalesce(sum(spend), 0) as spend from calls"
                    )
                    row = (
                        await conn.execute(
                            text(sql),
                            {"since": naive(since), "until": naive(until), "known": list(PURPOSES)},
                        )
                    ).one()
        except (TimeoutError, SQLAlchemyError, OSError):
            return None
        return {"calls": row.calls, "failed": row.failed, "spend_usd": float(row.spend)}


def naive(dt: datetime) -> datetime:
    """LiteLLM keeps times in UTC without a zone (Prisma's DateTime)."""
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}
