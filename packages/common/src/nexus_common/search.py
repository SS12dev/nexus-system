"""Hybrid search over a user's memories, past chats, document chunks, agent systems and skills:
vector similarity and Postgres full-text search, fused by reciprocal rank. Every query is
scoped to one user (for skills: the shared ones plus the user's own, active only)."""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Table = Literal["memories", "file_chunks", "episodes", "agent_systems", "skills"]
TABLES: tuple[str, ...] = ("memories", "file_chunks", "episodes", "agent_systems", "skills")
# Standard RRF constant: damps the weight of the very top ranks.
RRF_K = 60
CANDIDATES = 40


@dataclass
class Hit:
    id: uuid.UUID
    score: float
    # Cosine distance to the query (0 = same direction), when the vector search found it.
    distance: float | None


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


async def hybrid_search(
    db: AsyncSession,
    table: Table,
    user_id: uuid.UUID,
    query: str,
    query_vec: list[float] | None,
    k: int,
    *,
    file_ids: list[uuid.UUID] | None = None,
    exclude_chat_id: uuid.UUID | None = None,
    lifetime: str | None = None,
) -> list[Hit]:
    if table not in TABLES:
        raise ValueError(table)
    if table == "skills":
        where = ["(t.owner_id is null or t.owner_id = :user_id)", "t.status = 'active'"]
    else:
        where = ["t.user_id = :user_id"]
    params: dict = {"user_id": user_id, "q": query, "n": CANDIDATES, "k": k}
    if file_ids is not None:
        where.append("t.file_id = any(:file_ids)")
        params["file_ids"] = list(file_ids)
    if exclude_chat_id is not None:
        where.append("t.chat_id <> :exclude_chat_id")
        params["exclude_chat_id"] = exclude_chat_id
    if lifetime is not None:
        where.append("t.lifetime = :lifetime")
        params["lifetime"] = lifetime
    cond = " and ".join(where)

    parts = []
    if query_vec is not None:
        params["vec"] = vector_literal(query_vec)
        parts.append(f"""
            select t.id, row_number() over (order by t.embedding <=> cast(:vec as vector)) as r,
                   t.embedding <=> cast(:vec as vector) as d
            from {table} t
            where {cond} and t.embedding is not null
            order by t.embedding <=> cast(:vec as vector)
            limit :n""")
    # OR the query's terms together: questions rarely contain every word of the answer.
    parts.append(f"""
        select t.id, row_number() over (order by ts_rank_cd(t.tsv, q.query) desc) as r,
               cast(null as float) as d
        from {table} t,
             to_tsquery('english', replace(plainto_tsquery('english', :q)::text, '&', '|'))
               as q(query)
        where {cond} and t.tsv @@ q.query
        order by ts_rank_cd(t.tsv, q.query) desc
        limit :n""")
    union = " union all ".join(f"({p})" for p in parts)
    sql = f"""
        select id, sum(1.0 / ({RRF_K} + r)) as score, min(d) as distance
        from ({union}) ranked
        group by id
        order by score desc
        limit :k"""
    rows = await db.execute(text(sql), params)
    return [Hit(id=r.id, score=float(r.score), distance=r.distance) for r in rows]
