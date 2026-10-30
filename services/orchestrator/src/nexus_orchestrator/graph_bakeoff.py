"""Which model should build the knowledge graph? A one-off test on the owner's own chats.

Samples recent chats, has a strong cloud model (the reference) extract entities and relations
with the planned ontology, then asks each candidate the same thing and scores it against the
reference: entity and relation precision, recall and F1, type and family agreement, valid
JSON, seconds per chat and memory used. Local candidates run on the Mac's Ollama one at a time
(unloaded after each, so a 24 GB Mac never holds two); cloud candidates go through LiteLLM.

The reference is a strong model, not a person, so the scores are agreement with it. The report
puts a few chats side by side so a person can check who was actually right.

    ./scripts/graph-bakeoff.sh someone@example.com --models qwen3.5:9b,gemma4:12b

It writes report.md and one JSON file per chat, which the script copies to ./graph-bakeoff/.
"""

import argparse
import asyncio
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select

from nexus_common.db import make_engine, make_sessionmaker
from nexus_common.graph import clean_name, norm
from nexus_common.models import Chat, Message, MessageRole, User
from nexus_common.settings import Settings
from nexus_orchestrator.graph_cases import CASES, Case

log = logging.getLogger(__name__)

# The planned ontology (docs: "Nexus Mind" plan): shells of entity types and edge families.
TYPES = {
    "personality": [
        "trait",
        "value",
        "belief",
        "preference",
        "goal",
        "skill",
        "interest",
        "habit",
        "state",
        "communication_style",
    ],
    "life": ["person", "pet", "group", "organisation", "place", "role", "life_event"],
    "activity": ["project", "app", "routine", "task", "decision"],
    "knowledge": ["topic", "tool", "document", "source"],
}
ALL_TYPES = [t for ts in TYPES.values() for t in ts]
DOMAINS = ["work", "learning", "home", "health", "social", "hobbies", "finance"]
FAMILIES = {
    "identity": [
        "has trait",
        "values",
        "prefers",
        "dislikes",
        "aims for",
        "good at",
        "interested in",
    ],
    "social": ["knows", "family of", "friend of", "works with", "reports to"],
    "belonging": ["part of", "member of", "is a", "located in", "works at", "lives in"],
    "activity": ["works on", "owns", "built", "uses", "runs", "produced", "plans"],
    "causal": ["led to", "depends on", "blocked by", "motivated by", "before"],
    "knowledge": ["about", "learned from", "references", "explains"],
}
PREDICATES = [p for ps in FAMILIES.values() for p in ps]
FAMILY_OF = {p: f for f, ps in FAMILIES.items() for p in ps}

PROMPT = f"""You build the knowledge graph of a user's mind for their personal assistant, \
from one chat between the USER and the ASSISTANT. Capture who the user is (personality: \
traits, values, beliefs, preferences, goals, skills, interests, habits, states, how they like \
to communicate), the people, groups, organisations, places and roles in their life, what they \
do (projects, apps, routines, tasks, decisions), and the topics, tools, documents and sources \
that matter to them beyond this one chat.

Rules:
- Only what the USER said or clearly confirmed. The ASSISTANT's lines are context, never a \
source. Nothing guessed.
- A request is not a fact: "build me a 2048 game" says nothing durable unless the user reveals \
something about themselves.
- Write "USER" for the user. Name each entity as the user would say it, without its type. \
Files and file names are not entities. Generic concepts ("code", "help") are not entities.
- Every entity has a type from: {", ".join(ALL_TYPES)}; and a domain from: \
{", ".join(DOMAINS)}.
- Every relation has a predicate from: {", ".join(PREDICATES)}. Pick the closest one; put the \
user's own wording in "label".
- weight: 0.9 stated outright and central, 0.6 stated in passing, 0.3 implied.
- Facts with more than two parties ("built Solitaire with React for Alex") get a life_event or \
task entity with a relation to each party.
- Usually a chat holds only a few entities. An empty answer is fine."""

SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ALL_TYPES},
                    "domain": {"type": "string", "enum": DOMAINS},
                    "summary": {"type": "string"},
                },
                "required": ["name", "type", "domain", "summary"],
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "predicate": {"type": "string", "enum": PREDICATES},
                    "to": {"type": "string"},
                    "label": {"type": "string"},
                    "weight": {"type": "number"},
                },
                "required": ["from", "predicate", "to", "label", "weight"],
            },
        },
    },
    "required": ["entities", "relations"],
}
# Two entity names this close (cosine distance of their embeddings) are the same thing.
SAME_NAME = 0.15
MAX_CHARS = 24_000


@dataclass
class Result:
    """One model's answer for one chat."""

    entities: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    valid: bool = True
    seconds: float = 0.0
    error: str = ""


def transcript(messages: list[tuple[str, str]], limit: int = MAX_CHARS) -> str:
    """The chat as USER:/ASSISTANT: lines, keeping the latest part when it is too long."""
    lines = [
        f"{'USER' if role == 'user' else 'ASSISTANT'}: {text.strip()}"
        for role, text in messages
        if text.strip()
    ]
    out = "\n\n".join(lines)
    return out if len(out) <= limit else "…" + out[-limit:]


def parse(raw: str) -> Result:
    """A model's JSON reply, cleaned the way ingestion would clean it; invalid parts dropped."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return Result(valid=False, error=f"not JSON: {exc}")
    if not isinstance(data, dict):
        return Result(valid=False, error="not an object")
    res = Result()
    seen: set[str] = set()
    for e in data.get("entities") or []:
        if not isinstance(e, dict):
            res.valid = False
            continue
        name = clean_name(str(e.get("name") or ""))
        key = norm(name)
        if not key or key == "user" or key in seen:
            continue
        seen.add(key)
        kind = str(e.get("type") or "")
        if kind not in ALL_TYPES:
            res.valid = False
        res.entities.append(
            {"name": name, "key": key, "type": kind, "domain": str(e.get("domain") or "")}
        )
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            res.valid = False
            continue
        src, dst = norm(str(r.get("from") or "")), norm(str(r.get("to") or ""))
        pred = str(r.get("predicate") or "").lower().strip()
        if not src or not dst or src == dst:
            continue
        if pred not in FAMILY_OF:
            res.valid = False
        res.relations.append(
            {"from": src, "to": dst, "predicate": pred, "family": FAMILY_OF.get(pred, "other")}
        )
    return res


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 1.0 if not na or not nb else 1.0 - dot / (na * nb)


def match_entities(
    ref: list[dict], got: list[dict], vectors: dict[str, list[float]]
) -> dict[str, str]:
    """Pairs each candidate entity key with at most one reference key: same name first, then
    the nearest meaning under SAME_NAME."""
    pairs: dict[str, str] = {}
    free = {e["key"] for e in ref}
    for e in got:
        if e["key"] in free:
            pairs[e["key"]] = e["key"]
            free.discard(e["key"])
    for e in got:
        if e["key"] in pairs or e["key"] not in vectors:
            continue
        best = min(
            ((cosine_distance(vectors[e["key"]], vectors[k]), k) for k in free if k in vectors),
            default=None,
        )
        if best and best[0] <= SAME_NAME:
            pairs[e["key"]] = best[1]
            free.discard(best[1])
    return pairs


@dataclass
class Score:
    tp: int = 0
    got: int = 0
    ref: int = 0

    def add(self, tp: int, got: int, ref: int) -> None:
        self.tp, self.got, self.ref = self.tp + tp, self.got + got, self.ref + ref

    @property
    def precision(self) -> float:
        return self.tp / self.got if self.got else 1.0

    @property
    def recall(self) -> float:
        return self.tp / self.ref if self.ref else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def score_chat(ref: Result, got: Result, vectors: dict[str, list[float]]) -> dict:
    """Entity and relation agreement for one chat. A relation agrees when both ends match
    (USER matches USER) and the family is the same; the exact predicate is counted apart."""
    if got.error:  # no usable answer: everything in the reference is missed, nothing is right
        return {
            "entities": (0, 1, len(ref.entities)),
            "typed": 0,
            "relations": (0, 1, len(ref.relations)),
            "predicate_same": 0,
        }
    pairs = match_entities(ref.entities, got.entities, vectors)
    ref_type = {e["key"]: e["type"] for e in ref.entities}
    typed = sum(
        1 for e in got.entities if e["key"] in pairs and ref_type[pairs[e["key"]]] == e["type"]
    )

    def end(k: str) -> str | None:
        return "user" if k == "user" else pairs.get(k)

    ref_rel = {(r["from"], r["to"], r["family"]): r["predicate"] for r in ref.relations}
    rel_tp = pred_same = 0
    used: set[tuple] = set()
    for r in got.relations:
        a, b = end(r["from"]), end(r["to"])
        for key in ((a, b, r["family"]), (b, a, r["family"])):
            if key in ref_rel and key not in used:
                used.add(key)
                rel_tp += 1
                pred_same += ref_rel[key] == r["predicate"]
                break
    return {
        "entities": (len(pairs), len(got.entities), len(ref.entities)),
        "typed": typed,
        "relations": (rel_tp, len(got.relations), len(ref.relations)),
        "predicate_same": pred_same,
    }


def gold_keys(e: dict) -> list[str]:
    return [norm(e["name"]), *(norm(a) for a in e["aliases"])]


def score_gold(case: Case, got: Result, vectors: dict[str, list[float]]) -> dict:
    """Agreement with a hand-labelled case: an entity matches a gold one by name or alias, then
    by meaning; its type is right when it is one of the gold types; a relation matches when
    both ends match (in either direction) and its family is one of the gold families. A name
    on the case's forbidden list is a trap hit."""
    if got.error:
        return {
            "entities": (0, 1, len(case.entities)),
            "typed": 0,
            "relations": (0, 1, len(case.relations)),
            "predicate_same": 0,
            "traps": 0,
        }
    gold = case.entities
    free = set(range(len(gold)))
    pairs: dict[str, int] = {}
    for e in got.entities:
        for i in sorted(free):
            if e["key"] in gold_keys(gold[i]):
                pairs[e["key"]] = i
                free.discard(i)
                break
    for e in got.entities:
        if e["key"] in pairs or e["key"] not in vectors:
            continue
        best = min(
            (
                (cosine_distance(vectors[e["key"]], vectors[k]), i)
                for i in free
                for k in gold_keys(gold[i])
                if k in vectors
            ),
            default=None,
        )
        if best and best[0] <= SAME_NAME:
            pairs[e["key"]] = best[1]
            free.discard(best[1])
    typed = sum(
        1 for e in got.entities if e["key"] in pairs and e["type"] in gold[pairs[e["key"]]]["types"]
    )

    index = {norm(g["name"]): i for i, g in enumerate(gold)}

    def gold_end(name: str) -> int | str | None:
        return "user" if name == "USER" else index.get(norm(name))

    def got_end(key: str) -> int | str | None:
        return "user" if key == "user" else pairs.get(key)

    rel_tp, used = 0, set()
    for r in got.relations:
        ends = {got_end(r["from"]), got_end(r["to"])}
        for j, g in enumerate(case.relations):
            if j in used or r["family"] not in g["families"]:
                continue
            if ends == {gold_end(g["from"]), gold_end(g["to"])}:
                used.add(j)
                rel_tp += 1
                break

    def trapped(key: str) -> bool:
        for f in case.forbidden:
            k = norm(f)
            if key == k or (
                key in vectors
                and k in vectors
                and cosine_distance(vectors[key], vectors[k]) <= SAME_NAME
            ):
                return True
        return False

    return {
        "entities": (len(pairs), len(got.entities), len(gold)),
        "typed": typed,
        "relations": (rel_tp, len(got.relations), len(case.relations)),
        "predicate_same": 0,
        "traps": sum(trapped(e["key"]) for e in got.entities),
    }


@dataclass
class Tally:
    """A model's totals over all chats."""

    entities: Score = field(default_factory=Score)
    relations: Score = field(default_factory=Score)
    typed: int = 0
    predicate_same: int = 0
    valid: int = 0
    chats: int = 0
    seconds: list[float] = field(default_factory=list)
    memory_gb: float = 0.0
    errors: int = 0
    traps: int = 0
    by_category: dict[str, tuple[Score, Score]] = field(default_factory=dict)

    def add(self, s: dict, res: Result, category: str = "") -> None:
        self.traps += s.get("traps", 0)
        if category:
            ents, rels = self.by_category.setdefault(category, (Score(), Score()))
            ents.add(*s["entities"])
            rels.add(*s["relations"])
        self.entities.add(*s["entities"])
        self.relations.add(*s["relations"])
        self.typed += s["typed"]
        self.predicate_same += s["predicate_same"]
        self.valid += res.valid and not res.error
        self.errors += bool(res.error)
        self.chats += 1
        self.seconds.append(res.seconds)


def median(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def report(reference: str, tallies: dict[str, Tally], chats: int, samples: str) -> str:
    rows = [
        "| Model | Entity F1 | Entity P / R | Type agrees | Relation F1 | Relation P / R | "
        "Same predicate | Valid JSON | Median s/chat | Max s/chat | Memory GB |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, t in sorted(tallies.items(), key=lambda kv: -kv[1].entities.f1 - kv[1].relations.f1):
        e, r = t.entities, t.relations
        rows.append(
            f"| {name} | {e.f1:.2f} | {e.precision:.2f} / {e.recall:.2f} | "
            f"{t.typed / e.tp if e.tp else 0:.0%} | {r.f1:.2f} | {r.precision:.2f} / "
            f"{r.recall:.2f} | {t.predicate_same / r.tp if r.tp else 0:.0%} | "
            f"{t.valid}/{t.chats} | {median(t.seconds):.1f} | {max(t.seconds, default=0):.1f} | "
            f"{t.memory_gb or '-'} |"
        )
    return (
        f"# Graph extraction bake-off\n\n{datetime.now(UTC):%Y-%m-%d %H:%M} UTC · {chats} chats · "
        f"reference: {reference}\n\nScores are agreement with the reference model, not ground "
        "truth. Check the samples below to see who was right where they differ.\n\n"
        + "\n".join(rows)
        + "\n\n"
        + samples
    )


def gold_report(tallies: dict[str, Tally], cases: int, samples: str) -> str:
    rows = [
        "| Model | Entity F1 | Entity P / R | Right type | Relation F1 | Relation P / R | "
        "Trap hits | Valid JSON | Median s/chat | Max s/chat | Memory GB |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    ranked = sorted(tallies.items(), key=lambda kv: -kv[1].entities.f1 - kv[1].relations.f1)
    for name, t in ranked:
        e, r = t.entities, t.relations
        rows.append(
            f"| {name} | {e.f1:.2f} | {e.precision:.2f} / {e.recall:.2f} | "
            f"{t.typed / e.tp if e.tp else 0:.0%} | {r.f1:.2f} | {r.precision:.2f} / "
            f"{r.recall:.2f} | {t.traps} | {t.valid}/{t.chats} | {median(t.seconds):.1f} | "
            f"{max(t.seconds, default=0):.1f} | {t.memory_gb or '-'} |"
        )
    cats = sorted({c for t in tallies.values() for c in t.by_category})
    by_cat = [
        "| Model | " + " | ".join(cats) + " |",
        "| --- |" + " --- |" * len(cats),
    ]
    for name, t in ranked:
        cells = [
            f"{t.by_category[c][0].f1:.2f} / {t.by_category[c][1].f1:.2f}"
            if c in t.by_category
            else "-"
            for c in cats
        ]
        by_cat.append(f"| {name} | " + " | ".join(cells) + " |")
    return (
        f"# Graph extraction test set\n\n{datetime.now(UTC):%Y-%m-%d %H:%M} UTC · {cases} "
        "hand-labelled cases (nexus_orchestrator/graph_cases.py)\n\nScored against the "
        "written answers. Trap hits are names that must not be extracted (hypotheticals, things "
        "only the assistant said, public figures, file names, injected text).\n\n"
        + "\n".join(rows)
        + "\n\n## Entity F1 / relation F1 by category\n\n"
        + "\n".join(by_cat)
        + "\n\n"
        + samples
    )


def sample_block(title: str, text: str, answers: dict[str, Result]) -> str:
    def fmt(r: Result) -> str:
        if r.error:
            return f"error: {r.error}"
        ents = ", ".join(f"{e['name']} ({e['type']})" for e in r.entities) or "none"
        rels = "; ".join(f"{x['from']} {x['predicate']} {x['to']}" for x in r.relations) or "none"
        return f"entities: {ents}\n  relations: {rels}"

    body = "\n".join(f"- **{m}**\n  {fmt(r)}" for m, r in answers.items())
    excerpt = text[:1200].replace("\n\n", "\n")
    return f"### {title}\n\n```text\n{excerpt}\n```\n\n{body}\n"


class Models:
    """Talks to LiteLLM (cloud models) and Ollama (local ones)."""

    def __init__(self, settings: Settings, ollama: str):
        self.settings = settings
        self.ollama = ollama.rstrip("/")
        self.http = httpx.AsyncClient(timeout=600)

    @staticmethod
    def is_local(model: str) -> bool:
        return not model.startswith("nexus-")

    async def extract(self, model: str, chat: str) -> Result:
        start = time.perf_counter()
        try:
            raw = await (
                self._ollama(model, chat) if self.is_local(model) else self._cloud(model, chat)
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return Result(valid=False, error=str(exc)[:200], seconds=time.perf_counter() - start)
        res = parse(raw)
        res.seconds = time.perf_counter() - start
        return res

    async def _cloud(self, model: str, chat: str) -> str:
        key = self.settings.litellm_master_key or self.settings.litellm_api_key
        r = await self.http.post(
            f"{self.settings.litellm_base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": chat},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "graph", "schema": SCHEMA},
                },
                "metadata": {"tags": ["nexus", "graph-bakeoff"]},
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def _ollama(self, model: str, chat: str) -> str:
        body = {
            "model": model,
            "stream": False,
            "format": SCHEMA,
            # gpt-oss can't switch reasoning off and returns nothing when asked to; low is fast.
            "think": "low" if model.startswith("gpt-oss") else False,
            "options": {"temperature": 0, "num_ctx": 16384},
            "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": chat}],
        }
        r = await self.http.post(f"{self.ollama}/api/chat", json=body)
        if r.status_code == 400 and "think" in r.text:  # a model without a thinking switch
            del body["think"]
            r = await self.http.post(f"{self.ollama}/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"]

    async def memory_gb(self, model: str) -> float:
        r = await self.http.get(f"{self.ollama}/api/ps")
        for m in r.json().get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return round(m.get("size", 0) / 1e9, 1)
        return 0.0

    async def unload(self, model: str) -> None:
        await self.http.post(f"{self.ollama}/api/generate", json={"model": model, "keep_alive": 0})

    async def embed(self, texts: list[str]) -> dict[str, list[float]]:
        if not texts:
            return {}
        r = await self.http.post(
            f"{self.ollama}/api/embed", json={"model": "bge-m3", "input": texts}
        )
        r.raise_for_status()
        return dict(zip(texts, r.json()["embeddings"], strict=True))


async def load_chats(
    database_url: str, email: str, count: int, min_user_messages: int = 2
) -> list[tuple[str, str]]:
    """The user's most recent chats with at least two messages of their own, as transcripts."""
    engine = make_engine(database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            user = await db.scalar(select(User).where(func.lower(User.email) == email.lower()))
            if user is None:
                raise SystemExit(f"No account for {email}")
            mine = (
                select(Message.chat_id)
                .where(Message.role == MessageRole.user)
                .group_by(Message.chat_id)
                .having(func.count() >= min_user_messages)
            )
            chats = (
                await db.scalars(
                    select(Chat)
                    .where(Chat.user_id == user.id, Chat.kind == "chat", Chat.id.in_(mine))
                    .order_by(Chat.updated_at.desc())
                    .limit(count)
                )
            ).all()
            out = []
            for c in chats:
                rows = await db.execute(
                    select(Message.role, Message.content)
                    .where(
                        Message.chat_id == c.id,
                        Message.role.in_([MessageRole.user, MessageRole.assistant]),
                    )
                    .order_by(Message.created_at)
                )
                text = transcript([(r.role.value, r.content or "") for r in rows])
                out.append((c.title or str(c.id), text))
            return out
    finally:
        await engine.dispose()


async def run(args: argparse.Namespace) -> Path:
    settings = Settings()
    ollama = args.ollama or os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    if args.dataset:
        return await run_dataset(args, settings, ollama)
    if not args.email:
        raise SystemExit("Give an account email, or --dataset to use the hand-labelled cases")
    chats = await load_chats(
        args.database_url or settings.database_url, args.email, args.chats, args.min_user_messages
    )
    if len(chats) < 10:
        print(f"Only {len(chats)} chats qualify: scores will not mean much.", flush=True)
    print(f"{len(chats)} chats loaded", flush=True)
    models = Models(settings, ollama)
    out = Path(args.out) / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)

    answers: dict[str, list[Result]] = {}
    print(f"Reference: {args.reference}", flush=True)
    gate = asyncio.Semaphore(4)

    async def reference(text: str) -> Result:
        async with gate:
            return await models.extract(args.reference, text)

    answers[args.reference] = list(await asyncio.gather(*(reference(t) for _, t in chats)))
    candidates = [m.strip() for m in args.models.split(",") if m.strip()]
    tallies: dict[str, Tally] = {}
    for model in candidates:
        print(f"Candidate: {model}", flush=True)
        results, mem = [], 0.0
        for i, (_, text) in enumerate(chats):
            results.append(await models.extract(model, text))
            if i == 0 and models.is_local(model):
                mem = await models.memory_gb(model)
            print(f"  {i + 1}/{len(chats)} {results[-1].seconds:.1f}s", flush=True)
        if models.is_local(model):
            await models.unload(model)
        answers[model] = results
        tallies[model] = Tally(memory_gb=mem)

    names = sorted({e["key"] for rs in answers.values() for r in rs for e in r.entities})
    vectors = await models.embed(names)
    ref = answers[args.reference]
    for model in candidates:
        for r0, r1 in zip(ref, answers[model], strict=True):
            tallies[model].add(score_chat(r0, r1, vectors), r1)

    for i, (title, text) in enumerate(chats):
        (out / f"chat-{i:03}.json").write_text(
            json.dumps(
                {
                    "title": title,
                    "transcript": text,
                    "answers": {
                        m: {
                            "entities": rs[i].entities,
                            "relations": rs[i].relations,
                            "error": rs[i].error,
                            "seconds": rs[i].seconds,
                        }
                        for m, rs in answers.items()
                    },
                },
                indent=1,
            )
        )
    picks = range(0, len(chats), max(1, len(chats) // args.samples))[: args.samples]
    samples = "## Samples\n\n" + "\n".join(
        sample_block(chats[i][0], chats[i][1], {m: rs[i] for m, rs in answers.items()})
        for i in picks
    )
    (out / "report.md").write_text(report(args.reference, tallies, len(chats), samples))
    await models.http.aclose()
    return out


async def run_dataset(args: argparse.Namespace, settings: Settings, ollama: str) -> Path:
    """Every model in --models against the hand-labelled cases (no reference model)."""
    cases = [c for c in CASES if not args.only or c.category in args.only.split(",")]
    texts = [transcript(c.messages) for c in cases]
    print(f"{len(cases)} cases", flush=True)
    models = Models(settings, ollama)
    out = Path(args.out) / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    answers: dict[str, list[Result]] = {}
    tallies: dict[str, Tally] = {}
    # Each model's answers are saved as soon as it finishes, so a rerun after a crash or a
    # redeploy picks up where it stopped.
    cache = Path(args.out) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        saved = cache / f"{re.sub(r'[^A-Za-z0-9._-]', '_', model)}.json"
        if saved.exists():
            data = json.loads(saved.read_text())
            answers[model] = [Result(**r) for r in data["results"]]
            tallies[model] = Tally(memory_gb=data["memory_gb"])
            print(f"Model: {model} (from {saved.name})", flush=True)
            continue
        print(f"Model: {model}", flush=True)
        results, mem = [], 0.0
        for i, text in enumerate(texts):
            results.append(await models.extract(model, text))
            if i == 0 and models.is_local(model):
                mem = await models.memory_gb(model)
            print(f"  {i + 1}/{len(texts)} {cases[i].id} {results[-1].seconds:.1f}s", flush=True)
        if models.is_local(model):
            await models.unload(model)
        answers[model], tallies[model] = results, Tally(memory_gb=mem)
        saved.write_text(json.dumps({"memory_gb": mem, "results": [r.__dict__ for r in results]}))

    names = {e["key"] for rs in answers.values() for r in rs for e in r.entities}
    for c in cases:
        names.update(k for g in c.entities for k in gold_keys(g))
        names.update(norm(f) for f in c.forbidden)
    vectors = await models.embed(sorted(n for n in names if n))
    for model, results in answers.items():
        for c, res in zip(cases, results, strict=True):
            tallies[model].add(score_gold(c, res, vectors), res, c.category)

    for i, c in enumerate(cases):
        (out / f"{c.id}.json").write_text(
            json.dumps(
                {
                    "id": c.id,
                    "note": c.note,
                    "gold": {"entities": c.entities, "relations": c.relations},
                    "forbidden": c.forbidden,
                    "answers": {
                        m: {
                            "entities": rs[i].entities,
                            "relations": rs[i].relations,
                            "error": rs[i].error,
                            "seconds": rs[i].seconds,
                            "score": score_gold(c, rs[i], vectors),
                        }
                        for m, rs in answers.items()
                    },
                },
                indent=1,
            )
        )
    edge = [i for i, c in enumerate(cases) if c.category == "edge"][: args.samples]
    samples = "## Edge-case answers\n\n" + "\n".join(
        sample_block(
            f"{cases[i].id}: {cases[i].note}", texts[i], {m: rs[i] for m, rs in answers.items()}
        )
        for i in edge
    )
    (out / "report.md").write_text(gold_report(tallies, len(cases), samples))
    await models.http.aclose()
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("email", nargs="?", default="")
    p.add_argument(
        "--dataset",
        action="store_true",
        help="score against the hand-labelled cases in graph_cases.py instead of real chats",
    )
    p.add_argument("--only", default="", help="with --dataset: comma-separated categories")
    p.add_argument(
        "--models",
        default="nexus-nano",
        help="comma-separated: Ollama tags (local) and/or nexus-* LiteLLM aliases",
    )
    p.add_argument("--reference", default="nexus-pro")
    p.add_argument("--chats", type=int, default=50)
    p.add_argument("--samples", type=int, default=8)
    p.add_argument("--ollama", default="")
    p.add_argument("--min-user-messages", type=int, default=2)
    p.add_argument(
        "--database-url",
        default="",
        help="read chats from another database, such as a restored backup copy",
    )
    p.add_argument("--out", default="/tmp/graph-bakeoff")
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    print(f"Report: {asyncio.run(run(args)) / 'report.md'}")


if __name__ == "__main__":
    main()
