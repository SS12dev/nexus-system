"""The Admin page's Graph tab: who builds the knowledge graph, whether the local model in
Ollama answers, what is waiting for it, what the last runs did, and how the graph is made up
(by shell, relation family and origin). Owners only; counts, never content.

The work itself runs in the orchestrator: graph_local every ten minutes, graph_nightly each
night. "Run now" queues either of them.
"""

import json

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from nexus_api.deps import DbDep, OwnerUser, QueueDep, RedisDep, SettingsDep
from nexus_common import ontology
from nexus_common.models import AuditLog, Entity, Episode, File, FileStatus, Memory, Relation

router = APIRouter(prefix="/api/admin/graph", tags=["admin"])

# The orchestrator writes these (graph_local.STATUS_KEY, graph_nightly.STATUS_KEY).
EXTRACT_KEY = "graph:local:last"
NIGHTLY_KEY = "graph:nightly:last"
JOBS = {"extract": "graph_extract", "rearrange": "graph_rearrange"}


async def _ollama(settings) -> dict:
    """Whether Ollama answers, has the graph model, and has it in memory now."""
    base = settings.ollama_base_url.rstrip("/")
    model = settings.graph_local_model
    out = {"url": base, "model": model, "reachable": False, "installed": False, "loaded_gb": 0}
    try:
        async with httpx.AsyncClient(timeout=4) as http:
            tags = (await http.get(f"{base}/api/tags")).json()
            ps = (await http.get(f"{base}/api/ps")).json()
    except (httpx.HTTPError, ValueError) as exc:
        out["error"] = str(exc) or type(exc).__name__
        return out
    names = {n for m in tags.get("models", []) for n in (m.get("name"), m.get("model"))}
    out["reachable"] = True
    out["installed"] = model in names or f"{model}:latest" in names
    loaded = [m for m in ps.get("models", []) if model in (m.get("name"), m.get("model"))]
    out["loaded_gb"] = round(loaded[0].get("size", 0) / 1e9, 1) if loaded else 0
    return out


async def _last(redis, key: str) -> dict | None:
    raw = await redis.get(key)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


async def _counts(db, column) -> dict:
    rows = await db.execute(select(column, func.count()).group_by(column))
    return {str(k) if k is not None else "none": n for k, n in rows.all()}


@router.get("")
async def graph_status(actor: OwnerUser, db: DbDep, redis: RedisDep, settings: SettingsDep):
    local = settings.graph_extractor == "local"
    return {
        "enabled": settings.graph_enabled,
        "extractor": settings.graph_extractor,
        "ollama": await _ollama(settings) if local else None,
        "backlog": {
            "memories": await db.scalar(
                select(func.count()).select_from(Memory).where(Memory.graph_at.is_(None))
            ),
            "chats": await db.scalar(
                select(func.count()).select_from(Episode).where(Episode.graph_at.is_(None))
            ),
            "files": await db.scalar(
                select(func.count())
                .select_from(File)
                .where(File.graph_at.is_(None), File.status == FileStatus.ready)
            ),
        },
        "last_extract": await _last(redis, EXTRACT_KEY),
        "last_nightly": await _last(redis, NIGHTLY_KEY),
        "nightly_utc": f"{settings.memory_nightly_hour_utc:02d}:"
        f"{settings.memory_nightly_minute_utc:02d}",
        "entities": {
            "total": await db.scalar(select(func.count()).select_from(Entity)),
            "by_shell": await _counts(db, Entity.shell),
            "by_origin": await _counts(db, Entity.origin),
        },
        "relations": {
            "total": await db.scalar(
                select(func.count()).select_from(Relation).where(Relation.valid_to.is_(None))
            ),
            "by_family": await _counts(db, Relation.family),
            "by_origin": await _counts(db, Relation.origin),
        },
        "shells": ontology.SHELLS,
        "families": list(ontology.FAMILIES),
    }


class GraphRunIn(BaseModel):
    what: str  # extract or rearrange


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_now(body: GraphRunIn, actor: OwnerUser, db: DbDep, queue: QueueDep):
    job = JOBS.get(body.what)
    if job is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "what must be extract or rearrange")
    await queue.enqueue_graph(job)
    db.add(AuditLog(actor_id=actor.id, action=f"graph.{body.what}", target=job))
    await db.commit()
    return {"queued": job}
