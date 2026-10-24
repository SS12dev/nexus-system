"""The Admin page's Improvements tab: what Nexus's self-reviews found and the proposals they
drafted, which the owner approves or denies. Owners only.

A review (nexus_orchestrator/improve.py) runs on a schedule or when asked here. Approving a
skill, instruction or setting proposal applies it at once and records how to undo it; a code or
tool proposal is handed to the orchestrator, which opens a pull request on the Nexus repository
(IMPROVE_REPO) for the owner to review and merge. Nothing merges by itself.
"""

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from nexus_api import alerts
from nexus_api.deps import DbDep, OwnerUser, QueueDep, RedisDep, SettingsDep
from nexus_api.schemas import ProposalDecisionIn
from nexus_common import config, improve
from nexus_common.models import (
    AuditLog,
    ImprovementReview,
    Proposal,
    ProposalKind,
    ProposalStatus,
    ReviewStatus,
    utcnow,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# A review still "running" after this long died with its worker.
STALE_REVIEW = timedelta(minutes=30)


def _review_out(r: ImprovementReview) -> dict:
    return {
        "id": r.id,
        "status": r.status,
        "trigger": r.trigger,
        "window_start": r.window_start,
        "window_end": r.window_end,
        "findings": r.findings,
        "proposals": r.proposals,
        "error": r.error,
        "created_at": r.created_at,
        "finished_at": r.finished_at,
    }


@router.get("/summary")
async def summary(actor: OwnerUser, db: DbDep, redis: RedisDep):
    """Counts for the Admin page's tab badges."""
    pending = await db.scalar(select(func.count()).where(Proposal.status == ProposalStatus.pending))
    active = (await alerts.active_and_recent(redis))["active"]
    return {"alerts": len(active), "proposals": pending or 0}


@router.get("/improvements")
async def list_improvements(
    actor: OwnerUser,
    db: DbDep,
    settings: SettingsDep,
    status_: ProposalStatus | None = Query(default=None, alias="status"),
    kind: ProposalKind | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    q = select(Proposal)
    if status_ is not None:
        q = q.where(Proposal.status == status_)
    if kind is not None:
        q = q.where(Proposal.kind == kind)
    rows = (await db.scalars(q.order_by(Proposal.created_at.desc()).limit(limit))).all()
    counts = dict(
        (await db.execute(select(Proposal.status, func.count()).group_by(Proposal.status))).all()
    )
    reviews = (
        await db.scalars(
            select(ImprovementReview).order_by(ImprovementReview.created_at.desc()).limit(10)
        )
    ).all()
    last_done = next((r for r in reviews if r.status == ReviewStatus.done), None)
    next_at = None
    if settings.improve_every_days > 0:
        base = last_done.created_at if last_done else utcnow()
        next_at = base + timedelta(days=settings.improve_every_days)
    return {
        "proposals": [improve.payload(p) for p in rows],
        "counts": {s.value: counts.get(s, 0) for s in ProposalStatus},
        "reviews": [_review_out(r) for r in reviews],
        "schedule": {
            "every_days": settings.improve_every_days,
            "next_at": next_at,
            "read_requests": settings.improve_read_requests,
            "min_count": settings.improve_min_count,
            "model": settings.improve_model,
            "topic_model": settings.improve_topic_model,
        },
        "pull_requests": {
            "repo": settings.improve_repo or None,
            "ready": bool(settings.improve_repo and settings.github_enabled),
        },
    }


@router.post("/improvements/review", status_code=status.HTTP_202_ACCEPTED)
async def review_now(actor: OwnerUser, db: DbDep, queue: QueueDep, settings: SettingsDep):
    """Start a review of the time since the last one (at most 30 days back)."""
    running = await db.scalar(
        select(ImprovementReview).where(
            ImprovementReview.status == ReviewStatus.running,
            ImprovementReview.created_at > utcnow() - STALE_REVIEW,
        )
    )
    if running is not None:
        return _review_out(running)
    now = utcnow()
    last = await db.scalar(
        select(ImprovementReview.window_end)
        .where(ImprovementReview.status == ReviewStatus.done)
        .order_by(ImprovementReview.created_at.desc())
        .limit(1)
    )
    start = max(last or now - timedelta(days=30), now - timedelta(days=30))
    if now - start < timedelta(days=1):
        # Reviewed moments ago: look at the last week again rather than at nothing.
        start = now - timedelta(days=7)
    r = ImprovementReview(
        status=ReviewStatus.running,
        trigger="manual",
        requested_by=actor.id,
        window_start=start,
        window_end=now,
        findings={},
        proposals=0,
    )
    db.add(r)
    db.add(AuditLog(actor_id=actor.id, action="improve.review", target=str(r.id)))
    await db.commit()
    await queue.enqueue_review(r.id)
    return _review_out(r)


async def _proposal(db, proposal_id: uuid.UUID) -> Proposal:
    p = await db.get(Proposal, proposal_id, with_for_update=True)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposal not found")
    return p


async def _apply_config(request: Request, redis, change: dict | None) -> None:
    if change is None:
        return
    settings = request.app.state.settings
    config.apply(settings, change["values"], request.app.state.service, version=change["version"])
    await config.announce(redis, change["version"])
    await config.report(redis, settings, request.app.state.service)


@router.post("/improvements/{proposal_id}/approve")
async def approve(
    proposal_id: uuid.UUID,
    body: ProposalDecisionIn,
    request: Request,
    actor: OwnerUser,
    db: DbDep,
    redis: RedisDep,
    queue: QueueDep,
    settings: SettingsDep,
):
    """Approve a pending proposal, or try a failed one again."""
    p = await _proposal(db, proposal_id)
    if p.status not in (ProposalStatus.pending, ProposalStatus.failed):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This proposal is already {p.status}")
    p.decided_by, p.decided_at, p.error = actor.id, utcnow(), None
    if body.note:
        p.note = body.note.strip()
    change = None
    if p.kind in improve.LOCAL:
        try:
            result, change = await improve.apply(db, p, actor.id)
        except (improve.ProposalError, config.ConfigError) as exc:
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        p.status, p.result = ProposalStatus.applied, result
    elif settings.improve_repo and settings.github_enabled:
        p.status = ProposalStatus.applying
    else:
        # No repository to open a pull request on: the approved spec is the outcome.
        p.status, p.result = ProposalStatus.applied, {"spec_only": True}
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="improve.approved",
            target=str(p.id),
            data={"kind": p.kind, "title": p.title},
        )
    )
    await db.commit()
    await _apply_config(request, redis, change)
    if p.status == ProposalStatus.applying:
        await queue.enqueue_proposal_pr(p.id)
    return improve.payload(p)


@router.post("/improvements/{proposal_id}/deny")
async def deny(proposal_id: uuid.UUID, body: ProposalDecisionIn, actor: OwnerUser, db: DbDep):
    """Turn a proposal down. The note, if any, tells later reviews why."""
    p = await _proposal(db, proposal_id)
    if p.status not in (ProposalStatus.pending, ProposalStatus.failed):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This proposal is already {p.status}")
    p.status, p.decided_by, p.decided_at = ProposalStatus.denied, actor.id, utcnow()
    p.note = (body.note or "").strip() or None
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="improve.denied",
            target=str(p.id),
            data={"kind": p.kind, "title": p.title, "note": p.note},
        )
    )
    await db.commit()
    return improve.payload(p)


@router.post("/improvements/{proposal_id}/revert")
async def revert(
    proposal_id: uuid.UUID, request: Request, actor: OwnerUser, db: DbDep, redis: RedisDep
):
    """Undo an applied skill, instruction or setting proposal."""
    p = await _proposal(db, proposal_id)
    if p.status != ProposalStatus.applied or p.kind not in improve.LOCAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an applied change can be undone here")
    try:
        change = await improve.revert(db, p, actor.id)
    except (improve.ProposalError, config.ConfigError) as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    p.status = ProposalStatus.reverted
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="improve.reverted",
            target=str(p.id),
            data={"kind": p.kind, "title": p.title},
        )
    )
    await db.commit()
    await _apply_config(request, redis, change)
    return improve.payload(p)
