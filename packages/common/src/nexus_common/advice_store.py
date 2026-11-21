"""Gathering the facts advice.py reads, and remembering what the person did with each item.

Money facts are read only for the owner with Money set up; everyone gets the Organiser ones.
Logs carry counts and rule names only, never titles, merchants or amounts.
"""

import logging
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import advice
from nexus_common import money_store as store
from nexus_common.models import (
    AdviceState,
    MoneyAccount,
    MoneySettings,
    OrgItem,
    RoleName,
    User,
    utcnow,
)
from nexus_common.organiser import live

log = logging.getLogger(__name__)

HISTORY_DAYS = 130
INBOX_AGE_DAYS = 7


async def has_money(db: AsyncSession, user: User) -> bool:
    if user.role != RoleName.owner:
        return False
    settings = await db.scalar(
        select(MoneySettings.user_id).where(MoneySettings.user_id == user.id)
    )
    account = await db.scalar(
        select(MoneyAccount.id).where(MoneyAccount.user_id == user.id).limit(1)
    )
    return settings is not None or account is not None


async def life_counts(db: AsyncSession, user: User, today: date) -> dict:
    old = utcnow() - timedelta(days=INBOX_AGE_DAYS)
    inbox_old = await db.scalar(
        select(func.count())
        .select_from(OrgItem)
        .where(live(user.id), OrgItem.status == "inbox", OrgItem.created_at < old)
    )
    overdue = await db.scalar(
        select(func.count())
        .select_from(OrgItem)
        .where(
            live(user.id),
            OrgItem.type == "task",
            OrgItem.status.in_(("todo", "doing")),
            OrgItem.due_on < today,
        )
    )
    return {"inbox_old": inbox_old or 0, "overdue": overdue or 0}


async def facts(
    db: AsyncSession, user: User, today: date, *, with_money: bool = True
) -> advice.Facts:
    f = advice.Facts(today=today, life=await life_counts(db, user, today))
    if with_money and await has_money(db, user):
        f.accounts = (await store.accounts_view(db, user.id, today))["items"]
        f.subs = (await store.subscriptions_view(db, user.id, today))["items"]
        f.txns = await store.load(
            db, user.id, today - timedelta(days=HISTORY_DAYS), today + timedelta(days=1)
        )
        f.health = await store.health_view(db, user.id, today)
        f.orders = await store.orders_to_confirm(db, user.id, today)
    return f


async def states(db: AsyncSession, user_id: uuid.UUID) -> dict[str, tuple[str, date | None]]:
    rows = (await db.scalars(select(AdviceState).where(AdviceState.user_id == user_id))).all()
    return {r.key: (r.status, r.until) for r in rows}


async def view(db: AsyncSession, user: User, today: date, *, with_money: bool = True) -> dict:
    """The Advice page: items to show, most urgent first, with counts per level."""
    items = advice.build(await facts(db, user, today, with_money=with_money))
    marks = await states(db, user.id)
    shown, hidden = advice.visible(items, marks, today)
    counts = {level: sum(1 for i in shown if i.level == level) for level in advice.LEVELS}
    kept = {i.key for i in shown}
    # Items the person set aside, still true today, so they can bring one back.
    aside = [
        {"key": i.key, "title": i.title, "status": marks[i.key][0], "until": marks[i.key][1]}
        for i in items
        if i.key not in kept and i.key in marks
    ]
    return {
        "items": [i.out() for i in shown],
        "counts": counts,
        "hidden": hidden,
        "set_aside": aside,
        "money": with_money and await has_money(db, user),
    }


async def set_state(
    db: AsyncSession, user_id: uuid.UUID, key: str, action: str, today: date, days: int = 7
) -> None:
    """done, dismissed, snoozed for `days`, or restore (forget what was done)."""
    row = await db.scalar(
        select(AdviceState).where(AdviceState.user_id == user_id, AdviceState.key == key)
    )
    if action == "restore":
        if row is not None:
            await db.delete(row)
        return
    if row is None:
        row = AdviceState(user_id=user_id, key=key[:160])
        db.add(row)
    row.status = action
    row.until = today + timedelta(days=days) if action == "snoozed" else None
    row.updated_at = utcnow()
    await db.flush()
