"""Search across everything one person has in Nexus: chats (every message, and each chat's
summary), files, memories, people and other things in their knowledge graph, saved links,
routines, apps, agent teams and skills. It backs the Cmd-K bar and the search page.

Each source answers in two ways and the answers are merged:

- **Words**: Postgres full-text search with prefix matching, so "invo" already finds
  "invoice" while typing, plus a plain substring match on titles and names.
- **Meaning**: pgvector similarity on the embeddings Nexus already keeps (memories, chat
  summaries, file chunks, entities, agent teams), when the embedder answers quickly. A slow
  or missing embedder never holds the bar up: the search falls back to words alone and says so.

Every hit gets a score in 0..~1.6 from simple, explainable parts (title match, word rank,
meaning, a small recency nudge), so the order is stable and easy to reason about. Everything
is scoped to one user and skips what is in Trash.

Nothing here logs the query text: search terms are personal.
"""

import asyncio
import logging
import math
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.embeddings import CachedEmbedder, EmbeddingError
from nexus_common.models import utcnow
from nexus_common.search import vector_literal

log = logging.getLogger(__name__)

Kind = Literal[
    "chat", "file", "memory", "person", "thing", "link", "routine", "app", "system", "skill"
]
KINDS: tuple[str, ...] = (
    "chat",
    "file",
    "memory",
    "person",
    "thing",
    "link",
    "routine",
    "app",
    "system",
    "skill",
)

# Highlight markers ts_headline puts around matched words; the web app turns them into <mark>.
# Control characters, so they can never clash with text someone wrote.
MARK_START = "\x02"
MARK_END = "\x03"
_HEADLINE = (
    f"StartSel={MARK_START}, StopSel={MARK_END}, MaxWords=24, MinWords=10, "
    "ShortWord=2, MaxFragments=1, FragmentDelimiter= … "
)
# Short texts (a memory) are highlighted whole.
_HEADLINE_ALL = f"StartSel={MARK_START}, StopSel={MARK_END}, HighlightAll=true"
MAX_QUERY_CHARS = 200
MAX_WORDS = 8
# How long the bar waits for the embedder before answering with words alone.
EMBED_TIMEOUT_S = 1.2
# Cosine distance beyond which a "meaning" match is noise. bge-m3 puts related passages at
# roughly 0.3-0.5 and unrelated ones above 0.6.
MAX_DISTANCE = 0.55
# Long texts are cut before highlighting: ts_headline re-parses the whole string.
HEADLINE_CHARS = 20_000


@dataclass
class Result:
    kind: str
    id: str
    title: str
    url: str
    snippet: str = ""
    # A short line under the title: "You · 3 Nov", "PDF · 2 pages", "Person".
    detail: str = ""
    at: datetime | None = None
    score: float = 0.0
    # How it matched: "title", "words", "meaning" (one or more).
    matched: list[str] = field(default_factory=list)

    def merge(self, other: "Result") -> None:
        """Fold another hit for the same thing into this one, keeping the best of each."""
        if other.score > self.score:
            self.score, self.snippet = other.score, other.snippet or self.snippet
            self.detail = other.detail or self.detail
            if other.url != self.url and "#" in other.url:
                self.url = other.url
        elif not self.snippet:
            self.snippet = other.snippet
        self.matched = sorted(set(self.matched) | set(other.matched))
        # A thing found both ways is more likely what they meant.
        if len(self.matched) > 1:
            self.score = max(self.score, other.score) + 0.05


@dataclass
class Query:
    raw: str
    words: list[str]
    # Prefix tsquery: "invo:* & march:*". Empty when nothing searchable is left.
    tsquery: str
    # ILIKE pattern for titles: "%invo march%".
    like: str


def parse(raw: str) -> Query:
    raw = " ".join((raw or "").split())[:MAX_QUERY_CHARS]
    # Only letters and digits reach to_tsquery, so its syntax (&, |, !, :, quotes) can't be
    # injected; to_tsquery stems each word, so "invoices:*" still finds "invoice".
    words = [w for w in re.findall(r"[^\W_]+", raw.lower()) if w][:MAX_WORDS]
    tsquery = " & ".join(f"{w}:*" for w in words)
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return Query(raw=raw, words=words, tsquery=tsquery, like=f"%{escaped}%")


def title_score(title: str, q: Query) -> float:
    """1.0 for an exact title, 0.9 when it starts with the query, 0.75 when it contains it,
    0.6 when it has every word, else 0."""
    t, needle = title.lower(), q.raw.lower()
    if not needle:
        return 0.0
    if t == needle:
        return 1.0
    if t.startswith(needle):
        return 0.9
    if needle in t:
        return 0.75
    if q.words and all(w in t for w in q.words):
        return 0.6
    return 0.0


def recency(at: datetime | None, now: datetime) -> float:
    """Up to 0.1 for something touched today, fading over about a month."""
    if at is None:
        return 0.0
    days = max(0.0, (now - at).total_seconds() / 86400)
    return 0.1 * math.exp(-days / 30)


def words_score(rank: float) -> float:
    """ts_rank_cd is unbounded; squash it into 0.45..0.7."""
    return 0.45 + 0.25 * (rank / (rank + 0.1))


def meaning_score(distance: float) -> float:
    """0.7 at distance 0, down to 0.35 at MAX_DISTANCE."""
    return 0.7 - 0.35 * min(distance, MAX_DISTANCE) / MAX_DISTANCE


def clean_snippet(snippet: str | None, limit: int = 240) -> str:
    if not snippet:
        return ""
    snippet = " ".join(snippet.split())
    if len(snippet) > limit:
        snippet = snippet[: limit - 1].rstrip() + "…"
        # Don't leave a highlight open.
        if snippet.count(MARK_START) > snippet.count(MARK_END):
            snippet += MARK_END
    return snippet


# --- Trash filters, mirroring nexus_common.trash ------------------------------------------------


def _not_trashed(kind: str, column: str) -> str:
    return (
        f"not exists (select 1 from trash_items tr where tr.kind = '{kind}' "
        f"and tr.entity_id = {column})"
    )


_FILE_VISIBLE = (
    _not_trashed("file", "f.id")
    + " and (f.folder_id is null or ("
    + _not_trashed("folder", "f.folder_id")
    + " and not exists (select 1 from trash_items tr join folders fo on fo.project_id = "
    "tr.entity_id where tr.kind = 'project' and fo.id = f.folder_id)))"
)
_MEMORY_VISIBLE = (
    _not_trashed("memory", "m.id")
    + " and not exists (select 1 from trash_items tr where tr.kind = 'memories_all' "
    "and tr.user_id = m.user_id)"
)
_CHAT_VISIBLE = "c.kind = 'chat' and " + _not_trashed("chat", "c.id")


# --- Sources -----------------------------------------------------------------------------------


@dataclass
class Ctx:
    db: AsyncSession
    user_id: uuid.UUID
    q: Query
    vec: str | None
    limit: int
    assistant: str
    now: datetime

    @property
    def params(self) -> dict:
        p = {
            "uid": self.user_id,
            "tsq": self.q.tsquery,
            "like": self.q.like,
            "n": self.limit * 3,
            "hl": _HEADLINE,
            "hlall": _HEADLINE_ALL,
            "hlchars": HEADLINE_CHARS,
        }
        if self.vec is not None:
            p["vec"] = self.vec
            p["maxd"] = MAX_DISTANCE
        return p


def _short_date(at: datetime | None) -> str:
    return f"{at.day} {at:%b %Y}" if at else ""


async def _chats(c: Ctx) -> list[Result]:
    out: list[Result] = []
    params = c.params
    if c.q.tsquery:
        # The best-matching message in each chat, then highlight only those.
        rows = await c.db.execute(
            text(f"""
            with q as (select to_tsquery('english', :tsq) as query),
            hits as (
                select distinct on (m.chat_id) m.id, m.chat_id, m.role, m.created_at,
                       m.content, ts_rank_cd(m.tsv, q.query) as rank
                from messages m join chats c on c.id = m.chat_id, q
                where c.user_id = :uid and {_CHAT_VISIBLE} and m.tsv @@ q.query
                order by m.chat_id, ts_rank_cd(m.tsv, q.query) desc
            ),
            top as (select * from hits order by rank desc limit :n)
            select t.id, t.chat_id, t.role, t.created_at, t.rank, c.title,
                   ts_headline('english', left(t.content, :hlchars), q.query, :hl) as snippet
            from top t join chats c on c.id = t.chat_id, q
            order by t.rank desc"""),
            params,
        )
        for r in rows:
            who = "You" if r.role == "user" else c.assistant
            title = r.title or "Untitled chat"
            out.append(
                Result(
                    kind="chat",
                    id=str(r.chat_id),
                    title=title,
                    url=f"/c/{r.chat_id}?m={r.id}",
                    snippet=clean_snippet(r.snippet),
                    detail=f"{who} · {_short_date(r.created_at)}",
                    at=r.created_at,
                    score=max(words_score(r.rank), title_score(title, c.q)),
                    matched=["words"],
                )
            )
    rows = await c.db.execute(
        text(f"""
        select c.id, c.title, c.updated_at from chats c
        where c.user_id = :uid and {_CHAT_VISIBLE} and c.title ilike :like
        order by c.updated_at desc limit :n"""),
        params,
    )
    for r in rows:
        out.append(
            Result(
                kind="chat",
                id=str(r.id),
                title=r.title,
                url=f"/c/{r.id}",
                detail=f"Chat · {_short_date(r.updated_at)}",
                at=r.updated_at,
                score=title_score(r.title, c.q),
                matched=["title"],
            )
        )
    if c.vec is not None:
        # Chat summaries carry what a chat was about even when no word matches.
        rows = await c.db.execute(
            text(f"""
            select e.chat_id, c.title, c.updated_at, left(e.content, 400) as content,
                   e.embedding <=> cast(:vec as vector) as d
            from episodes e join chats c on c.id = e.chat_id
            where e.user_id = :uid and {_CHAT_VISIBLE} and e.embedding is not null
              and {_not_trashed("episode", "e.id")}
            order by e.embedding <=> cast(:vec as vector) limit :n"""),
            params,
        )
        for r in rows:
            if r.d is None or r.d > MAX_DISTANCE:
                continue
            out.append(
                Result(
                    kind="chat",
                    id=str(r.chat_id),
                    title=r.title or "Untitled chat",
                    url=f"/c/{r.chat_id}",
                    snippet=clean_snippet(r.content),
                    detail=f"Chat summary · {_short_date(r.updated_at)}",
                    at=r.updated_at,
                    score=meaning_score(r.d),
                    matched=["meaning"],
                )
            )
    return out


async def _files(c: Ctx) -> list[Result]:
    out: list[Result] = []
    params = c.params
    rows = await c.db.execute(
        text(f"""
        select f.id, f.name, f.content_type, f.updated_at from files f
        where f.user_id = :uid and f.status <> 'failed' and f.name ilike :like
          and {_FILE_VISIBLE}
        order by f.updated_at desc limit :n"""),
        params,
    )
    for r in rows:
        out.append(
            Result(
                kind="file",
                id=str(r.id),
                title=r.name,
                url=f"/library?file={r.id}",
                detail=f"File · {_short_date(r.updated_at)}",
                at=r.updated_at,
                score=title_score(r.name, c.q),
                matched=["title"],
            )
        )
    if c.q.tsquery:
        rows = await c.db.execute(
            text(f"""
            with q as (select to_tsquery('english', :tsq) as query),
            hits as (
                select distinct on (ch.file_id) ch.file_id, ch.heading, ch.content,
                       ts_rank_cd(ch.tsv, q.query) as rank
                from file_chunks ch join files f on f.id = ch.file_id, q
                where ch.user_id = :uid and ch.tsv @@ q.query and {_FILE_VISIBLE}
                order by ch.file_id, ts_rank_cd(ch.tsv, q.query) desc
            ),
            top as (select * from hits order by rank desc limit :n)
            select t.file_id, t.heading, t.rank, f.name, f.updated_at,
                   ts_headline('english', left(t.content, :hlchars), q.query, :hl) as snippet
            from top t join files f on f.id = t.file_id, q
            order by t.rank desc"""),
            params,
        )
        for r in rows:
            out.append(
                Result(
                    kind="file",
                    id=str(r.file_id),
                    title=r.name,
                    url=f"/library?file={r.file_id}",
                    snippet=clean_snippet(r.snippet),
                    detail=(r.heading or "File")[:80] + f" · {_short_date(r.updated_at)}",
                    at=r.updated_at,
                    score=words_score(r.rank),
                    matched=["words"],
                )
            )
    if c.vec is not None:
        rows = await c.db.execute(
            text(f"""
            with near as (
                select ch.file_id, ch.heading, left(ch.content, 400) as content,
                       ch.embedding <=> cast(:vec as vector) as d
                from file_chunks ch
                where ch.user_id = :uid and ch.embedding is not null
                order by ch.embedding <=> cast(:vec as vector) limit :n
            )
            select distinct on (n.file_id) n.*, f.name, f.updated_at
            from near n join files f on f.id = n.file_id
            where {_FILE_VISIBLE}
            order by n.file_id, n.d"""),
            params,
        )
        for r in rows:
            if r.d > MAX_DISTANCE:
                continue
            out.append(
                Result(
                    kind="file",
                    id=str(r.file_id),
                    title=r.name,
                    url=f"/library?file={r.file_id}",
                    snippet=clean_snippet(r.content),
                    detail=(r.heading or "File")[:80] + f" · {_short_date(r.updated_at)}",
                    at=r.updated_at,
                    score=meaning_score(r.d),
                    matched=["meaning"],
                )
            )
    return out


async def _memories(c: Ctx) -> list[Result]:
    out: list[Result] = []
    params = c.params
    if c.q.tsquery:
        rows = await c.db.execute(
            text(f"""
            with q as (select to_tsquery('english', :tsq) as query)
            select m.id, m.kind, m.content, m.updated_at, ts_rank_cd(m.tsv, q.query) as rank,
                   ts_headline('english', m.content, q.query, :hlall) as snippet
            from memories m, q
            where m.user_id = :uid and m.tsv @@ q.query and {_MEMORY_VISIBLE}
            order by rank desc limit :n"""),
            params,
        )
        for r in rows:
            out.append(_memory(r, words_score(r.rank), "words", r.snippet))
    if c.vec is not None:
        rows = await c.db.execute(
            text(f"""
            select m.id, m.kind, m.content, m.updated_at,
                   m.embedding <=> cast(:vec as vector) as d
            from memories m
            where m.user_id = :uid and m.embedding is not null and {_MEMORY_VISIBLE}
            order by m.embedding <=> cast(:vec as vector) limit :n"""),
            params,
        )
        for r in rows:
            if r.d <= MAX_DISTANCE:
                out.append(_memory(r, meaning_score(r.d), "meaning", r.content))
    return out


def _memory(r, score: float, how: str, marked: str) -> Result:
    kind = str(getattr(r.kind, "value", r.kind))
    return Result(
        kind="memory",
        id=str(r.id),
        # A memory is one sentence: it is its own title, with the matched words marked.
        title=clean_snippet(marked if how == "words" else r.content, 200),
        url=f"/mind?tab=memories&memory={r.id}",
        detail=f"{kind.capitalize()} · {_short_date(r.updated_at)}",
        at=r.updated_at,
        score=score,
        matched=[how],
    )


async def _entities(c: Ctx) -> list[Result]:
    out: list[Result] = []
    params = c.params
    visible = _not_trashed("entity", "e.id")
    clauses = [f"select e.id, 'title' as how, 0.0 as rank, null::float as d from entities e "
               f"where e.user_id = :uid and e.name ilike :like and {visible}"]  # fmt: skip
    if c.q.tsquery:
        clauses.append(
            "select e.id, 'words', ts_rank_cd(e.tsv, to_tsquery('english', :tsq)), null "
            f"from entities e where e.user_id = :uid and {visible} "
            "and e.tsv @@ to_tsquery('english', :tsq)"
        )
    if c.vec is not None:
        clauses.append(
            "(select e.id, 'meaning', 0.0, e.embedding <=> cast(:vec as vector) "
            f"from entities e where e.user_id = :uid and e.embedding is not null and {visible} "
            "order by e.embedding <=> cast(:vec as vector) limit :n)"
        )
    union = " union all ".join(f"({x})" for x in clauses)
    rows = await c.db.execute(
        text(f"""
        with h as ({union})
        select h.how, h.rank, h.d, e.id, e.name, e.type, e.summary, e.seen_at, e.is_self
        from h join entities e on e.id = h.id
        limit :n"""),
        params,
    )
    for r in rows:
        if r.how == "meaning" and (r.d is None or r.d > MAX_DISTANCE):
            continue
        score = (
            title_score(r.name, c.q)
            if r.how == "title"
            else words_score(r.rank)
            if r.how == "words"
            else meaning_score(r.d)
        )
        person = r.type == "person"
        out.append(
            Result(
                kind="person" if person else "thing",
                id=str(r.id),
                title=r.name,
                url=f"/mind?focus={r.id}",
                snippet=clean_snippet(r.summary, 180),
                detail=("You" if r.is_self else r.type.replace("_", " ").capitalize()),
                at=r.seen_at,
                score=score,
                matched=[r.how],
            )
        )
    return out


async def _named(
    c: Ctx,
    kind: str,
    sql: str,
    url: Callable[[object], str],
    detail: Callable[[object], str],
) -> list[Result]:
    """Things found by name (and a second text column): links, routines, apps, teams, skills."""
    rows = await c.db.execute(text(sql), c.params)
    out = []
    for r in rows:
        by_title = title_score(r.title or "", c.q)
        out.append(
            Result(
                kind=kind,
                id=str(r.id),
                title=r.title,
                url=url(r),
                snippet=clean_snippet(r.extra, 180),
                detail=detail(r),
                at=r.at,
                # Matched only in the second column (a prompt, a goal, a note): a fair hit.
                score=by_title or 0.5,
                matched=["title" if by_title else "words"],
            )
        )
    return out


async def _links(c: Ctx) -> list[Result]:
    return await _named(
        c,
        "link",
        f"""select l.id, l.title, coalesce(l.note, l.url) as extra, l.updated_at as at, l.kind
        from links l where l.user_id = :uid and {_not_trashed("link", "l.id")}
          and (l.title ilike :like or l.url ilike :like or coalesce(l.note, '') ilike :like)
        order by l.updated_at desc limit :n""",
        lambda r: f"/library?link={r.id}",
        lambda r: "GitHub" if str(r.kind).startswith("github") else "Link",
    )


async def _routines(c: Ctx) -> list[Result]:
    return await _named(
        c,
        "routine",
        f"""select r.id, r.name as title, r.prompt as extra, r.updated_at as at, r.enabled
        from routines r where r.user_id = :uid and {_not_trashed("routine", "r.id")}
          and (r.name ilike :like or r.prompt ilike :like)
        order by r.updated_at desc limit :n""",
        lambda r: f"/routines?open={r.id}",
        lambda r: "Routine" + ("" if r.enabled else " · paused"),
    )


async def _apps(c: Ctx) -> list[Result]:
    return await _named(
        c,
        "app",
        f"""select p.id, p.name as title, coalesce(p.live_url, p.repo_url, '') as extra,
               p.updated_at as at, p.app_state
        from projects p where p.user_id = :uid and {_not_trashed("project", "p.id")}
          and {_not_trashed("app", "p.id")}
          and (p.name ilike :like or coalesce(p.repo_name, '') ilike :like)
        order by p.updated_at desc limit :n""",
        lambda r: "/apps",
        lambda r: "App" + (" · live" if r.app_state == "running" else ""),
    )


async def _systems(c: Ctx) -> list[Result]:
    return await _named(
        c,
        "system",
        f"""select s.id, s.name as title, s.goal as extra, s.updated_at as at
        from agent_systems s where s.user_id = :uid and {_not_trashed("system", "s.id")}
          and (s.name ilike :like or s.goal ilike :like)
        order by s.updated_at desc limit :n""",
        lambda r: f"/agents/{r.id}",
        lambda r: "Agent team",
    )


async def _skills(c: Ctx) -> list[Result]:
    return await _named(
        c,
        "skill",
        f"""select s.id, s.name as title, s.description as extra, s.updated_at as at, s.scope
        from skills s
        where (s.owner_id is null or s.owner_id = :uid) and s.status = 'active'
          and {_not_trashed("skill", "s.id")}
          and (s.name ilike :like or s.description ilike :like)
        order by s.updated_at desc limit :n""",
        lambda r: "/skills",
        lambda r: "Skill",
    )


Source = Callable[[Ctx], Awaitable[list[Result]]]
SOURCES: dict[str, tuple[Source, tuple[str, ...]]] = {
    # name: (function, kinds it can return)
    "chats": (_chats, ("chat",)),
    "files": (_files, ("file",)),
    "memories": (_memories, ("memory",)),
    "entities": (_entities, ("person", "thing")),
    "links": (_links, ("link",)),
    "routines": (_routines, ("routine",)),
    "apps": (_apps, ("app",)),
    "systems": (_systems, ("system",)),
    "skills": (_skills, ("skill",)),
}


@dataclass
class Found:
    query: str
    results: list[Result]
    # Whether meaning (embeddings) took part, so the UI can say "words only" when it didn't.
    semantic: bool
    took_ms: int
    # Kinds that had more hits than were returned.
    more: list[str]

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "results": [asdict(r) for r in self.results],
            "semantic": self.semantic,
            "took_ms": self.took_ms,
            "more": self.more,
        }


async def _query_vector(
    db: AsyncSession, embedder: CachedEmbedder | None, q: Query
) -> list[float] | None:
    # One-letter queries mean nothing to an embedding model.
    if embedder is None or len(q.raw) < 3:
        return None
    try:
        vec, _model = await asyncio.wait_for(embedder.embed_one(db, q.raw), EMBED_TIMEOUT_S)
        return vec
    except (TimeoutError, EmbeddingError) as exc:
        log.info("search.embed_skipped", extra={"reason": type(exc).__name__})
        return None


async def search(
    db: AsyncSession,
    user_id: uuid.UUID,
    raw: str,
    *,
    embedder: CachedEmbedder | None = None,
    kinds: list[str] | None = None,
    limit: int = 5,
    assistant: str = "Nexus",
) -> Found:
    """Search everything a user has. `limit` is per kind; `kinds` narrows to some kinds."""
    started = time.monotonic()
    q = parse(raw)
    if not q.raw:
        return Found(query="", results=[], semantic=False, took_ms=0, more=[])
    wanted = set(kinds or KINDS)
    vec = await _query_vector(db, embedder, q)
    ctx = Ctx(
        db=db,
        user_id=user_id,
        q=q,
        vec=vector_literal(vec) if vec is not None else None,
        limit=limit,
        assistant=assistant,
        now=utcnow(),
    )
    merged: dict[tuple[str, str], Result] = {}
    # One session, so the sources run one after another; each is a couple of indexed queries.
    for name, (fn, source_kinds) in SOURCES.items():
        if not wanted.intersection(source_kinds):
            continue
        try:
            hits = await fn(ctx)
        except Exception:
            # One broken source must not blank the whole bar.
            log.exception("search.source_failed", extra={"source": name})
            await db.rollback()
            continue
        for hit in hits:
            if hit.kind not in wanted or hit.score <= 0:
                continue
            hit.score = round(hit.score + recency(hit.at, ctx.now), 4)
            key = (hit.kind, hit.id)
            if key in merged:
                merged[key].merge(hit)
            else:
                merged[key] = hit

    by_kind: dict[str, list[Result]] = {}
    for hit in sorted(merged.values(), key=lambda h: -h.score):
        by_kind.setdefault(hit.kind, []).append(hit)
    results: list[Result] = []
    more: list[str] = []
    for kind, hits in by_kind.items():
        results.extend(hits[:limit])
        if len(hits) > limit:
            more.append(kind)
    results.sort(key=lambda h: -h.score)
    took = int((time.monotonic() - started) * 1000)
    log.info(
        "search.done",
        extra={
            "words": len(q.words),
            "results": len(results),
            "semantic": vec is not None,
            "kinds": len(wanted),
            "took_ms": took,
        },
    )
    return Found(query=q.raw, results=results, semantic=vec is not None, took_ms=took, more=more)
