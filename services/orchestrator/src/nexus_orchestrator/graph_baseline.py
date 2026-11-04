"""A quality baseline for the knowledge graph: the real two-pass extraction (graph_local.extract,
the same prompts and schemas the local model gets every ten minutes) run on a sample of the
user's imported conversations by the local model and by cloud models, side by side. Nothing is
written to the graph.

The strongest cloud model is the reference; the others are scored as agreement with it (the
bake-off's matching: same name or nearest meaning, relation ends and family). Each model also
gets numbers that need no reference: relations per entity, how many entities have no link at
all, and how many links are only "USER relates to X". Cloud calls report their cost, so the
report can say what a full re-extraction of every imported conversation would cost.

    ./scripts/graph-baseline.sh someone@example.com --reference nexus-max \
        --models qwen3.5:9b,nexus-pro,nexus-mini --chats 40
"""

import argparse
import asyncio
import json
import logging
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select

from nexus_common import graph
from nexus_common.db import make_engine, make_sessionmaker
from nexus_common.models import ImportedChat, User
from nexus_common.settings import Settings
from nexus_orchestrator import graph_local
from nexus_orchestrator.graph_bakeoff import Models, Result, Tally, parse, score_chat
from nexus_orchestrator.llm import parse_json


class CloudModel:
    """graph_local's model interface (one structured-output call) answered by LiteLLM."""

    def __init__(self, settings: Settings, model: str, http: httpx.AsyncClient):
        self.model, self.http = model, http
        self.url = f"{settings.litellm_base_url.rstrip('/')}/v1/chat/completions"
        self.key = settings.litellm_master_key or settings.litellm_api_key
        self.cost = 0.0
        self.tokens = Counter()

    async def ask(self, system: str, prompt: str, schema: dict) -> dict:
        r = await self.http.post(
            self.url,
            headers={"Authorization": f"Bearer {self.key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "graph", "schema": schema},
                },
                "metadata": {"tags": ["nexus", "graph-baseline"]},
            },
        )
        if r.status_code != 200:
            raise graph_local.LocalModelError(f"{self.model}: {r.status_code} {r.text[:200]}")
        try:
            self.cost += float(r.headers.get("x-litellm-response-cost") or 0)
        except ValueError:
            pass
        usage = r.json().get("usage") or {}
        self.tokens["in"] += usage.get("prompt_tokens") or 0
        self.tokens["out"] += usage.get("completion_tokens") or 0
        return parse_json(r.json()["choices"][0]["message"]["content"] or "")


class OllamaModel(graph_local.LocalModel):
    """The local model, by tag (not only the one in settings)."""

    def __init__(self, settings: Settings, model: str, http: httpx.AsyncClient):
        super().__init__(settings, http)
        self.model = model
        self.cost = 0.0
        self.tokens = Counter()


@dataclass
class Shape:
    """Numbers about one model's graph that need no reference."""

    entities: int = 0
    relations: int = 0
    linked: int = 0  # entities with at least one relation
    to_user: int = 0  # relations with USER at one end
    between: int = 0  # relations between two entities
    predicates: Counter = field(default_factory=Counter)
    seconds: float = 0.0
    errors: int = 0

    def add(self, r: Result) -> None:
        if r.error:
            self.errors += 1
            return
        self.seconds += r.seconds
        self.entities += len(r.entities)
        self.relations += len(r.relations)
        ends = {x for rel in r.relations for x in (rel["from"], rel["to"])}
        self.linked += sum(1 for e in r.entities if e["key"] in ends)
        for rel in r.relations:
            user = graph.SELF in (rel["from"], rel["to"])
            self.to_user += user
            self.between += not user
            self.predicates[rel["predicate"]] += 1


def to_result(found: graph_local.Found, seconds: float) -> Result:
    """The pipeline's answer in the bake-off's shape, so it is scored the same way."""
    raw = {
        "entities": [
            {"name": e["name"], "type": e["type"], "domain": e["domain"] or ""}
            for e in found.found.entities
        ],
        "relations": [
            {"from": r["from"], "to": r["to"], "predicate": r["predicate"]}
            for r in found.found.relations
        ],
    }
    res = parse(json.dumps(raw))
    res.seconds = seconds
    return res


async def run_one(model, text: str) -> tuple[Result, dict]:
    item = graph_local.Item({}, text, None)  # type: ignore[arg-type]
    start = time.perf_counter()
    try:
        found = await graph_local.extract(model, [item])
    except (graph_local.LocalModelError, httpx.HTTPError, KeyError, ValueError) as exc:
        return Result(valid=False, error=str(exc)[:200], seconds=time.perf_counter() - start), {}
    seconds = time.perf_counter() - start
    detail = {
        "entities": [
            {k: e[k] for k in ("name", "type", "domain", "summary")} for e in found.found.entities
        ],
        "relations": [
            {k: r[k] for k in ("from", "predicate", "label", "to", "weight", "ended")}
            for r in found.found.relations
        ],
    }
    return to_result(found, seconds), detail


async def load_imported(database_url: str, email: str, count: int, seed: int) -> tuple[list, int]:
    """A spread of the user's imported conversations (not only the newest), and how many there
    are in all, for the cost estimate."""
    engine = make_engine(database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            user = await db.scalar(select(User).where(func.lower(User.email) == email.lower()))
            if user is None:
                raise SystemExit(f"No account for {email}")
            rows = (
                await db.scalars(
                    select(ImportedChat)
                    .where(
                        ImportedChat.user_id == user.id,
                        ImportedChat.messages >= 2,
                        func.length(ImportedChat.text) >= 400,
                    )
                    .order_by(ImportedChat.started_at)
                )
            ).all()
            total = await db.scalar(
                select(func.count())
                .select_from(ImportedChat)
                .where(ImportedChat.user_id == user.id)
            )
            picked = random.Random(seed).sample(list(rows), min(count, len(rows)))
            picked.sort(key=lambda c: c.started_at or datetime.min.replace(tzinfo=UTC))
            return [
                (c.title or c.external_id, graph_local._imported_text(c)) for c in picked
            ], total
    finally:
        await engine.dispose()


def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def report(args, chats, total, models, shapes, tallies, details) -> str:
    ref = args.reference
    lines = [
        "# Knowledge graph baseline",
        "",
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC · {len(chats)} imported conversations, picked "
        f"across the whole history · the real two-pass extraction (entities, then relations) "
        f"· reference: {ref}",
        "",
        "## What each model builds",
        "",
        "| Model | Entities / chat | Relations / chat | Relations per entity | Entities with no "
        "link | Links between two things (not USER) | Errors | Time / chat | Cost of this run |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    n = max(1, len(chats))
    for m, s in shapes.items():
        ok = max(1, n - s.errors)
        lines.append(
            f"| {m} | {s.entities / ok:.1f} | {s.relations / ok:.1f} | "
            f"{s.relations / max(1, s.entities):.2f} | {pct(1 - s.linked / max(1, s.entities))} | "
            f"{pct(s.between / max(1, s.relations))} | {s.errors} | {s.seconds / ok:.0f}s | "
            f"${models[m].cost:.2f} |"
        )
    lines += [
        "",
        f"## Agreement with {ref}",
        "",
        "Not ground truth: how much of what the reference found each model also found "
        "(recall), and how much of what it found the reference agrees with (precision).",
        "",
        "| Model | Entity P | Entity R | Entity F1 | Type agrees | Relation P | Relation R | "
        "Relation F1 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m, t in tallies.items():
        typed = t.typed / max(1, t.entities.tp)
        lines.append(
            f"| {m} | {pct(t.entities.precision)} | {pct(t.entities.recall)} | "
            f"{pct(t.entities.f1)} | {pct(typed)} | {pct(t.relations.precision)} | "
            f"{pct(t.relations.recall)} | {pct(t.relations.f1)} |"
        )
    lines += ["", "## Cost of re-reading every imported conversation", ""]
    for m in shapes:
        if models[m].cost:
            each = models[m].cost / n
            lines.append(
                f"- {m}: ${each:.3f} a conversation, about **${each * total:.2f}** for all "
                f"{total} ({models[m].tokens['in']:,} tokens in, {models[m].tokens['out']:,} out "
                "in this run)"
            )
    lines += ["", "## Most used predicates", ""]
    for m, s in shapes.items():
        top = ", ".join(f"{p} {c}" for p, c in s.predicates.most_common(8)) or "none"
        lines.append(f"- {m}: {top}")
    lines += ["", "## Samples", ""]
    picks = range(0, len(chats), max(1, len(chats) // args.samples))[: args.samples]
    for i in picks:
        title, text = chats[i]
        lines += [f"### {title}", "", "```text", text[:1000], "```", ""]
        for m in shapes:
            d = details[m][i]
            if not d:
                lines.append(f"- **{m}**: error")
                continue
            ents = ", ".join(f"{e['name']} ({e['type']})" for e in d["entities"]) or "none"
            rels = "; ".join(
                f"{r['from']} —{r['label'] or r['predicate']}→ {r['to']}" for r in d["relations"]
            )
            lines.append(f"- **{m}**\n  - entities: {ents}\n  - relations: {rels or 'none'}")
        lines.append("")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> Path:
    settings = Settings()
    if args.ollama:
        settings.ollama_base_url = args.ollama
    chats, total = await load_imported(
        args.database_url or settings.database_url, args.email, args.chats, args.seed
    )
    print(f"{len(chats)} of {total} imported conversations picked", flush=True)
    out = Path(args.out) / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))
    names = [args.reference, *[m.strip() for m in args.models.split(",") if m.strip()]]
    models = {
        m: CloudModel(settings, m, http)
        if m.startswith("nexus-")
        else OllamaModel(settings, m, http)
        for m in names
    }
    results: dict[str, list[Result]] = {}
    details: dict[str, list[dict]] = {}
    for m, model in models.items():
        print(f"{m}", flush=True)
        gate = asyncio.Semaphore(4 if isinstance(model, CloudModel) else 1)
        done = 0

        async def one(text: str, model=model, gate=gate) -> tuple[Result, dict]:
            nonlocal done
            async with gate:
                got = await run_one(model, text)
            done += 1
            print(f"  {done}/{len(chats)} {got[0].seconds:.0f}s {got[0].error[:80]}", flush=True)
            return got

        pairs = await asyncio.gather(*(one(t) for _, t in chats))
        results[m], details[m] = [p[0] for p in pairs], [p[1] for p in pairs]
        if isinstance(model, OllamaModel):
            await http.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/generate",
                json={"model": m, "keep_alive": 0},
            )
    shapes = {m: Shape() for m in models}
    for m, rs in results.items():
        for r in rs:
            shapes[m].add(r)
    embed = Models(settings, settings.ollama_base_url)
    keys = sorted({e["key"] for rs in results.values() for r in rs for e in r.entities})
    vectors = await embed.embed(keys)
    await embed.http.aclose()
    tallies = {m: Tally() for m in names[1:]}
    for m in names[1:]:
        for r0, r1 in zip(results[args.reference], results[m], strict=True):
            tallies[m].add(score_chat(r0, r1, vectors), r1)
    (out / "chats.json").write_text(
        json.dumps(
            [
                {"title": t, "text": x, "answers": {m: details[m][i] for m in models}}
                for i, (t, x) in enumerate(chats)
            ],
            indent=1,
            ensure_ascii=False,
        )
    )
    (out / "report.md").write_text(report(args, chats, total, models, shapes, tallies, details))
    await http.aclose()
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("email")
    p.add_argument("--reference", default="nexus-max")
    p.add_argument("--models", default="qwen3.5:9b,nexus-pro")
    p.add_argument("--chats", type=int, default=40)
    p.add_argument("--samples", type=int, default=8)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--ollama", default="")
    p.add_argument("--database-url", default="")
    p.add_argument("--out", default="/tmp/graph-baseline")
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    print(f"Report: {asyncio.run(run(args)) / 'report.md'}")


if __name__ == "__main__":
    main()
