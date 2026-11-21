"""Background work for Money (nexus_common/money.py, money_store.py):

- `sync_user`: read new bank and card alert emails from Gmail into transactions, and the newest
  CAMS / KFintech consolidated statement (when its password is saved) into holdings. Runs
  hourly for everyone who switched it on, and when they press "Check now".
  Card statement emails give each card's bill and due date, and "converted to EMI" emails
  start tracking the loan.
- `send_reminders`: once a day at 9 am local time, renewals coming up, a charge after
  cancelling, a price rise, and card bills due in three days or missed. Each is sent once.
- `revalue_holdings`: each night, value every holding at AMFI's latest published NAV.

No model sees any of it: alerts and statements are read with plain rules on this machine.
Logs carry counts and ids only, never amounts, merchants, subjects or senders.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from nexus_common import money
from nexus_common import money_store as store
from nexus_common.models import (
    MoneyHolding,
    MoneySettings,
    NotificationKind,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.notices import notify
from nexus_common.organiser import local_today, zone
from nexus_orchestrator.google_tools import GMAIL, Google, GoogleError, _b64decode, message_body
from nexus_orchestrator.turn import TurnDeps

log = logging.getLogger(__name__)

# On the first sync, read this far back.
FIRST_SYNC = timedelta(days=90)
# Read again a little before the last sync, for mail that arrived late.
OVERLAP = timedelta(days=1)
MAX_MESSAGES = 1500
# Reading old mail for history goes further, up to two years of alerts.
MAX_HISTORY_MESSAGES = 6000
PARALLEL = 5
CAS_QUERY = (
    "from:(camsonline.com OR kfintech.com OR karvy.com) has:attachment filename:pdf newer_than:120d"
)


async def _list_ids(g: Google, query: str, cap: int) -> list[str]:
    ids: list[str] = []
    token = None
    while len(ids) < cap:
        params = {"q": query, "maxResults": min(500, cap - len(ids))}
        if token:
            params["pageToken"] = token
        page = await g.call("gmail", "GET", f"{GMAIL}/messages", params=params)
        ids += [m["id"] for m in page.get("messages", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    return ids


def _header(msg: dict, name: str) -> str:
    for h in (msg.get("payload") or {}).get("headers", []):
        if h.get("name", "").lower() == name:
            return h.get("value", "")
    return ""


async def _read_alerts(
    deps: TurnDeps, g: Google, user: User, prefs: MoneySettings, back_days: int | None = None
) -> dict:
    if back_days:
        since = utcnow() - timedelta(days=back_days)
        cap = MAX_HISTORY_MESSAGES
    else:
        since = (prefs.gmail_synced_until or utcnow() - FIRST_SYNC) - OVERLAP
        cap = MAX_MESSAGES
    after = f"after:{int(since.timestamp())}"
    ids = await _list_ids(g, f"{money.ALERT_QUERY} {after}", cap)
    for query in (money.CARD_BILL_QUERY, money.EMI_QUERY):
        ids += [i for i in await _list_ids(g, f"{query} {after}", 200) if i not in ids]
    tz = zone(user)
    parsed: list[tuple[money.Parsed, str]] = []
    bills: list[money.CardBill] = []
    bookings: list[money.EmiBooking] = []
    newest = prefs.gmail_synced_until
    gate = asyncio.Semaphore(PARALLEL)

    async def one(mid: str) -> None:
        nonlocal newest
        async with gate:
            msg = await g.call("gmail", "GET", f"{GMAIL}/messages/{mid}", params={"format": "full"})
        received = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000, UTC)
        newest = max(newest or received, received)
        body, _ = message_body(msg.get("payload") or {})
        subject, sender = _header(msg, "subject"), _header(msg, "from")
        day = received.astimezone(tz).date()
        if bill := money.parse_card_bill(subject, body, day, sender=sender):
            bills.append(bill)
        elif booking := money.parse_emi_booking(subject, body, day, sender=sender):
            bookings.append(booking)
        elif p := money.parse_alert(subject, body, day, sender=sender):
            parsed.append((p, mid))

    await asyncio.gather(*(one(m) for m in ids))
    counts = {"messages": len(ids), "added": 0, "matched": 0, "skipped": 0, "bills": 0, "emis": 0}
    async with deps.sessionmaker() as db:
        for bill in sorted(bills, key=lambda b: b.statement_on):
            counts["bills"] += await store.record_card_bill(db, user.id, bill)
        for booking in bookings:
            counts["emis"] += await store.record_emi_booking(db, user.id, booking) is not None
        # Oldest first, so an account's rows arrive in order.
        for p, mid in sorted(parsed, key=lambda x: x[0].occurred_on):
            got = await store.ingest(
                db, deps.settings, user.id, [p], source="alert", source_ref=mid
            )
            for k, v in got.items():
                counts[k] += v
        row = await store.settings_for(db, user.id)
        # Reading old mail never moves the mark backwards.
        if newest and (row.gmail_synced_until is None or newest > row.gmail_synced_until):
            row.gmail_synced_until = newest
        elif row.gmail_synced_until is None:
            row.gmail_synced_until = utcnow()
        row.gmail_synced_at = utcnow()
        row.last_error = None
        await db.commit()
    return counts


def _pdf_part(payload: dict) -> dict | None:
    stack = [payload]
    while stack:
        part = stack.pop()
        name = (part.get("filename") or "").lower()
        if name.endswith(".pdf") and (part.get("body") or {}).get("attachmentId"):
            return part
        stack += part.get("parts") or []
    return None


async def _read_cas(deps: TurnDeps, g: Google, user: User, prefs: MoneySettings) -> int | None:
    """The newest CAS email's holdings. None when there's nothing new to read."""
    password = money.unseal(deps.settings, prefs.cas_password_enc)
    if not password:
        return None
    ids = await _list_ids(g, CAS_QUERY, 1)
    if not ids or ids[0] == prefs.cas_message_id:
        return None
    mid = ids[0]
    msg = await g.call("gmail", "GET", f"{GMAIL}/messages/{mid}", params={"format": "full"})
    part = _pdf_part(msg.get("payload") or {})
    async with deps.sessionmaker() as db:
        row = await store.settings_for(db, user.id)
        row.cas_message_id = mid
        if part is None:
            await db.commit()
            return None
        att = await g.call(
            "gmail",
            "GET",
            f"{GMAIL}/messages/{mid}/attachments/{part['body']['attachmentId']}",
        )
        data = _b64decode(att.get("data", ""))
        try:
            holdings, as_of = await asyncio.to_thread(money.read_cas, data, password)
        except money.StatementError as exc:
            row.last_error = f"The statement emailed by CAMS couldn't be read: {exc}"[:300]
            await store.record_import(
                db, deps.settings, user.id, kind="cas", origin="gmail",
                filename=part.get("filename") or "CAS.pdf", status="failed", error=str(exc),
            )  # fmt: skip
            await db.commit()
            log.info("money.cas_failed", extra={"user_id": str(user.id)})
            return None
        saved = await store.save_holdings(db, user.id, holdings, as_of)
        await store.record_import(
            db, deps.settings, user.id, kind="cas", origin="gmail",
            filename=part.get("filename") or "CAS.pdf", status="done", counts={"holdings": saved},
        )  # fmt: skip
        row.cas_synced_at = utcnow()
        await db.commit()
    return saved


async def sync_user(
    deps: TurnDeps, user_id: uuid.UUID, *, back_days: int | None = None
) -> dict | None:
    """Read new alerts and the newest CAS for one person (with back_days, every alert that far
    back). None when they haven't switched it on."""
    async with deps.sessionmaker() as db:
        user = await db.get(User, user_id)
        prefs = await db.get(MoneySettings, user_id)
        if (
            user is None
            or user.status != UserStatus.active
            or prefs is None
            or not prefs.gmail_sync
        ):
            return None
        db.expunge(prefs)
        db.expunge(user)
    g = Google(deps, user_id)
    try:
        counts = await _read_alerts(deps, g, user, prefs, back_days)
        holdings = await _read_cas(deps, g, user, prefs)
    except GoogleError as exc:
        # The text is about the connection (not connected, API off), never the mail itself.
        async with deps.sessionmaker() as db:
            row = await store.settings_for(db, user_id)
            row.last_error = str(exc)[:300]
            await db.commit()
        log.warning(
            "money.sync_failed", extra={"user_id": str(user_id), "error": type(exc).__name__}
        )
        return None
    if holdings is not None:
        counts["holdings"] = holdings
    log.info("money.synced", extra={"user_id": str(user_id), **counts})
    if any(counts.get(k) for k in ("added", "matched", "bills", "emis")) or holdings:
        await store.changed(deps.redis, user_id, **counts)
    return counts


async def sync_all(deps: TurnDeps) -> int:
    async with deps.sessionmaker() as db:
        ids = list(
            (
                await db.scalars(
                    select(MoneySettings.user_id)
                    .join(User, User.id == MoneySettings.user_id)
                    .where(MoneySettings.gmail_sync, User.status == UserStatus.active)
                )
            ).all()
        )
    done = 0
    for uid in ids:
        try:
            if await sync_user(deps, uid) is not None:
                done += 1
        except Exception:
            log.exception("money.sync_crashed", extra={"user_id": str(uid)})
    return done


async def fetch_navs(client: httpx.AsyncClient | None = None) -> dict:
    own = client is None
    client = client or httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        resp = await client.get(money.AMFI_NAV_URL)
        resp.raise_for_status()
        return money.parse_amfi_navs(resp.text)
    finally:
        if own:
            await client.aclose()


async def revalue_holdings(deps: TurnDeps, navs: dict | None = None) -> int:
    """Value every holding at AMFI's latest NAV. Returns how many changed."""
    async with deps.sessionmaker() as db:
        holdings = (await db.scalars(select(MoneyHolding))).all()
        if not holdings:
            return 0
        if navs is None:
            try:
                navs = await fetch_navs()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("money.navs_unavailable", extra={"error": type(exc).__name__})
                return 0
        changed = [h for h in holdings if store.revalue(h, navs)]
        await db.commit()
    for uid in {h.user_id for h in changed}:
        await store.changed(deps.redis, uid)
    log.info("money.revalued", extra={"count": len(changed), "navs": len(navs)})
    return len(changed)


REMIND_HOUR = 9
REMIND_KEEP = 60 * 60 * 24 * 45


async def send_reminders(deps: TurnDeps, now: datetime | None = None) -> int:
    """Money reminders for everyone at 9 am their time. A Redis key per reminder makes sure
    each is sent once, however often this runs."""
    now = now or utcnow()
    async with deps.sessionmaker() as db:
        users = (
            await db.scalars(
                select(User)
                .join(MoneySettings, MoneySettings.user_id == User.id)
                .where(User.status == UserStatus.active)
            )
        ).all()
        sent = 0
        for user in users:
            if now.astimezone(zone(user)).hour != REMIND_HOUR:
                continue
            today = local_today(user, now)
            subs = await store.subscriptions_view(db, user.id, today)
            accounts = await store.accounts_view(db, user.id, today)
            due = store.reminders(subs, accounts, today)
            health = await store.health_view(db, user.id, today)
            due += store.health_reminders(health, today)
            for r in due:
                key = f"money:remind:{user.id}:{r['tag']}"
                if not await deps.redis.set(key, "1", nx=True, ex=REMIND_KEEP):
                    continue
                url = r.get("url") or (
                    "/money/accounts" if r["tag"].startswith("card") else "/money/subscriptions"
                )
                await notify(
                    db, deps.redis, deps.settings, user.id, NotificationKind.reminder,
                    r["title"], r["body"], url=url, tag=f"money-{r['tag']}"[:120],
                )  # fmt: skip
                sent += 1
        await db.commit()
    if sent:
        log.info("money.reminders_sent", extra={"count": sent})
    return sent
