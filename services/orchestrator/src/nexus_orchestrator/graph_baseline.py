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
import math
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

# A first pass that casts the widest net: everyday life counts as much as work. Tried as
# "<model>+wide" next to the production prompt, to see what the graph misses today.
WIDE_ENTITY_PROMPT = graph_local.ENTITY_PROMPT.replace(
    "- Usually there are only a few. An empty list is fine.",
    "- Cast a wide net. Everyday life counts as much as work: food and cooking, recipes, "
    "sports and fitness, games, music, films and shows, books, travel, shopping, cars, "
    "gadgets, family events, pets, chores, money habits. A thing the user does, plans, asks "
    "about for themselves or cares about belongs, even if mentioned once. Prefer the specific "
    'thing ("biryani", "Raspberry Pi 5") over a vague one, and add the broader interest too '
    '("cooking"). An empty list only when the item says nothing about the user.',
)
assert WIDE_ENTITY_PROMPT != graph_local.ENTITY_PROMPT


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
    domains: Counter = field(default_factory=Counter)
    seconds: float = 0.0
    errors: int = 0

    def add(self, r: Result) -> None:
        if r.error:
            self.errors += 1
            return
        self.seconds += r.seconds
        self.entities += len(r.entities)
        self.domains.update(e["domain"] or "none" for e in r.entities)
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


async def run_one(model, text: str, prompt: str = graph_local.ENTITY_PROMPT) -> tuple[Result, dict]:
    item = graph_local.Item({}, text, None)  # type: ignore[arg-type]
    start = time.perf_counter()
    try:
        found = await graph_local.extract(model, [item], prompt)
    except (
        graph_local.LocalModelError,
        httpx.HTTPError,
        LookupError,
        ValueError,
        TypeError,
        AttributeError,
    ) as exc:
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


# --- Embedding models -------------------------------------------------------------------------

JUDGE_PROMPT = """Each numbered line is a pair of names from one person's knowledge graph. \
Say whether both name the same thing: the same person, place, project, tool or topic, \
including spelling variants, abbreviations, translations and a name with or without a \
qualifier ("Raspberry Pi" and "my Raspberry Pi"). Related but different things are not the \
same ("Python" and "Pandas", "cooking" and "biryani")."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "same": {"type": "boolean"}},
                "required": ["i", "same"],
            },
        }
    },
    "required": ["answers"],
}


def unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def distance(a: list[float], b: list[float]) -> float:
    return 1.0 - sum(x * y for x, y in zip(a, b, strict=True))


def auc(same: list[float], other: list[float]) -> float:
    """How often a same pair is closer than a different pair (ties count half)."""
    if not same or not other:
        return 0.0
    wins = sum((d < o) + 0.5 * (d == o) for d in same for o in other)
    return wins / (len(same) * len(other))


def best_f1(same: list[float], other: list[float]) -> tuple[float, float]:
    best = (0.0, 0.0)
    for cut in sorted(set(same + other)):
        tp = sum(d <= cut for d in same)
        fp = sum(d <= cut for d in other)
        p, r = tp / max(1, tp + fp), tp / max(1, len(same))
        f = 2 * p * r / (p + r) if p + r else 0.0
        best = max(best, (f, cut))
    return best


async def ollama_embed(http: httpx.AsyncClient, base: str, model: str, texts: list[str]):
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        r = await http.post(f"{base}/api/embed", json={"model": model, "input": texts[i : i + 64]})
        if r.status_code != 200:
            raise ValueError(f"{r.status_code} {r.text[:160]}")
        out += [unit(v) for v in r.json()["embeddings"]]
    return out


async def test_embedders(
    http: httpx.AsyncClient, base: str, judge, names: list[str], embedders: list[str], seed: int
) -> dict:
    """Each embedder proposes its closest pairs; the reference model judges every proposed pair
    (plus random ones); each embedder is then scored on all judged pairs."""
    vectors: dict[str, list[list[float]]] = {}
    scores: dict[str, dict] = {}
    pairs: set[tuple[int, int]] = set()
    for e in embedders:
        start = time.perf_counter()
        try:
            vecs = await ollama_embed(http, base, e, names)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            scores[e] = {"error": str(exc)[:120]}
            continue
        vectors[e] = vecs
        scores[e] = {"seconds": time.perf_counter() - start}
        for i, v in enumerate(vecs):
            near = sorted((distance(v, w), j) for j, w in enumerate(vecs) if j != i)[:2]
            pairs.update((min(i, j), max(i, j)) for _, j in near)
        await http.post(f"{base}/api/generate", json={"model": e, "keep_alive": 0})
    rng = random.Random(seed)
    want = len(pairs) // 3
    while want > 0 and len(names) > 1:
        i, j = rng.sample(range(len(names)), 2)
        pairs.add((min(i, j), max(i, j)))
        want -= 1
    judged = sorted(pairs)
    rng.shuffle(judged)
    judged = judged[:420]
    verdict: dict[tuple[int, int], bool] = {}
    for k in range(0, len(judged), 35):
        chunk = judged[k : k + 35]
        listing = "\n".join(f"{n}: {names[i]!r} | {names[j]!r}" for n, (i, j) in enumerate(chunk))
        try:
            got = await judge.ask(JUDGE_PROMPT, listing, JUDGE_SCHEMA)
        except (graph_local.LocalModelError, httpx.HTTPError, ValueError):
            continue
        for a in got.get("answers") or []:
            if isinstance(a, dict) and isinstance(a.get("i"), int) and 0 <= a["i"] < len(chunk):
                verdict[chunk[a["i"]]] = bool(a.get("same"))
    for e, vecs in vectors.items():
        same = [distance(vecs[i], vecs[j]) for (i, j), v in verdict.items() if v]
        other = [distance(vecs[i], vecs[j]) for (i, j), v in verdict.items() if not v]
        f, cut = best_f1(same, other)
        scores[e].update(auc=auc(same, other), f1=f, cut=cut)
    return {
        "pairs": len(verdict),
        "same": sum(verdict.values()),
        "scores": scores,
        "examples": [
            {"a": names[i], "b": names[j], "same": v} for (i, j), v in list(verdict.items())[:60]
        ],
    }


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


def report(args, chats, total, models, shapes, tallies, details, embeds=None) -> str:
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
    lines += ["", "## Parts of life covered (entities per domain, all chats)", ""]
    doms = sorted({d for s in shapes.values() for d in s.domains})
    lines += ["| Model | " + " | ".join(doms) + " |", "|---|" + "---|" * len(doms)]
    for m, s in shapes.items():
        lines.append(f"| {m} | " + " | ".join(str(s.domains[d]) for d in doms) + " |")
    if embeds:
        lines += [
            "",
            "## Embedding models: telling the same thing from different things",
            "",
            f"{embeds['pairs']} pairs of names the reference model found (each name's nearest "
            f"neighbours under every embedder, plus random pairs), judged same or different by "
            f"{ref} ({embeds['same']} same). Ranking: how often a same pair is closer than a "
            "different pair (1.0 is perfect). Best F1: at the best distance cut-off. Judging "
            f"cost ${embeds.get('judge_cost', 0):.2f}.",
            "",
            "| Embedder | Ranking (AUC) | Best F1 | At distance | Time |",
            "|---|---|---|---|---|",
        ]
        for name, e in embeds["scores"].items():
            if e.get("error"):
                lines.append(f"| {name} | error: {e['error']} | | | |")
                continue
            lines.append(
                f"| {name} | {e['auc']:.3f} | {pct(e['f1'])} | {e['cut']:.2f} | "
                f"{e['seconds']:.0f}s |"
            )
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


def save_chats(out: Path, chats: list, details: dict[str, list[dict]]) -> None:
    """Every answer so far, in the shape --reuse reads back."""
    (out / "chats.json").write_text(
        json.dumps(
            [
                {"title": t, "text": x, "answers": {m: d[i] for m, d in details.items()}}
                for i, (t, x) in enumerate(chats)
            ],
            indent=1,
            ensure_ascii=False,
        )
    )


async def run(args: argparse.Namespace) -> Path:
    settings = Settings()
    if args.ollama:
        settings.ollama_base_url = args.ollama
    chats, total = await load_imported(
        args.database_url or settings.database_url, args.email, args.chats, args.seed
    )
    # --reuse: the same chats as an earlier run, and that run's answers from models that
    # finished, so a run cut short (credits, a crash) only redoes what is missing.
    earlier: dict[str, list[dict]] = {}
    if args.reuse:
        saved = json.loads(await asyncio.to_thread(Path(args.reuse).read_text))
        chats = [(c["title"], c["text"]) for c in saved]
        for m in saved[0]["answers"] if saved else []:
            got = [c["answers"].get(m) or {} for c in saved]
            if sum(1 for g in got if g) >= len(got) * 0.9:  # finished, bar a stray timeout
                earlier[m] = got
        print(f"Reusing {', '.join(earlier) or 'nothing'} from {args.reuse}", flush=True)
    print(f"{len(chats)} of {total} imported conversations picked", flush=True)
    out = Path(args.out) / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))
    names = [args.reference, *[m.strip() for m in args.models.split(",") if m.strip()]]

    def build(name: str):
        base = name.removesuffix("+wide")
        if base.startswith("nexus-"):
            return CloudModel(settings, base, http)
        return OllamaModel(settings, base, http)

    models = {m: build(m) for m in names}
    results: dict[str, list[Result]] = {}
    details: dict[str, list[dict]] = {}
    for m, model in models.items():
        if m in earlier:
            details[m] = earlier[m]
            results[m] = [
                parse(json.dumps(d)) if d else Result(valid=False, error="no answer earlier")
                for d in earlier[m]
            ]
            print(f"{m}: reused", flush=True)
            continue
        print(f"{m}", flush=True)
        gate = asyncio.Semaphore(4 if isinstance(model, CloudModel) else 1)
        done = 0

        prompt = WIDE_ENTITY_PROMPT if m.endswith("+wide") else graph_local.ENTITY_PROMPT

        async def one(text: str, model=model, gate=gate, prompt=prompt) -> tuple[Result, dict]:
            nonlocal done
            async with gate:
                got = await run_one(model, text, prompt)
            done += 1
            print(f"  {done}/{len(chats)} {got[0].seconds:.0f}s {got[0].error[:80]}", flush=True)
            return got

        pairs = await asyncio.gather(*(one(t) for _, t in chats))
        results[m], details[m] = [p[0] for p in pairs], [p[1] for p in pairs]
        save_chats(out, chats, details)  # after each model: a crash later keeps this one
        if isinstance(model, OllamaModel) and not any(
            n != m and n.removesuffix("+wide") == model.model for n in names[names.index(m) + 1 :]
        ):
            await http.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/generate",
                json={"model": model.model, "keep_alive": 0},
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
    embeds = None
    if args.embedders:
        seen: dict[str, str] = {}
        for d in details[args.reference]:
            for e in (d or {}).get("entities", []):
                seen.setdefault(graph.norm(e["name"]), e["name"])
        ents = list(seen.values())
        random.Random(args.seed).shuffle(ents)
        ents = ents[: args.embed_names]
        print(f"Embedders on {len(ents)} names", flush=True)
        judge = build(args.reference)  # its own meter: judging is not part of a re-read
        embeds = await test_embedders(
            http,
            settings.ollama_base_url.rstrip("/"),
            judge,
            ents,
            [e.strip() for e in args.embedders.split(",") if e.strip()],
            args.seed,
        )
        embeds["judge_cost"] = judge.cost
        (out / "embeddings.json").write_text(json.dumps(embeds, indent=1, ensure_ascii=False))
    save_chats(out, chats, details)
    (out / "report.md").write_text(
        report(args, chats, total, models, shapes, tallies, details, embeds)
    )
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
    p.add_argument(
        "--embedders",
        default="",
        help="comma-separated Ollama embedding models to compare, e.g. bge-m3,qwen3-embedding:4b",
    )
    p.add_argument("--embed-names", type=int, default=250)
    p.add_argument(
        "--reuse",
        default="",
        help="chats.json of an earlier run: same chats, and its finished models' answers are kept",
    )
    p.add_argument("--ollama", default="")
    p.add_argument("--database-url", default="")
    p.add_argument("--out", default="/tmp/graph-baseline")
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    print(f"Report: {asyncio.run(run(args)) / 'report.md'}")


if __name__ == "__main__":
    main()
