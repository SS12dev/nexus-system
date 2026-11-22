"""Background work for Money (nexus_common/money.py, money_store.py):

- `sync_user`: read new bank and card alert emails from Gmail into transactions, and the newest
  CAMS / KFintech consolidated statement (when its password is saved) into holdings. Runs
  hourly for everyone who switched it on, and when they press "Check now".
  Card statement emails give each card's bill and due date, and "converted to EMI" emails
  start tracking the loan.
- `send_reminders`: once a day at 9 am local time, renewals coming up, a charge after
  cancelling, a price rise, and card bills due in three days or missed. Each is sent once.
- `revalue_holdings`: each night, value every holding at AMFI's latest published NAV.
- `reprice_stocks`: each night, price shares at NSE's latest close (the bhavcopy).
- `record_history`: each night after that, store what each share and fund is worth, for the
  investments graph (money_history).
- `fill_share_history`: for shares with a purchase date, fetch NSE's closes back to it, once,
  and again after the quantity, price paid or date changes.

No model sees any of it: alerts and statements are read with plain rules on this machine.
Logs carry counts and ids only, never amounts, merchants, subjects or senders.
"""

import asyncio
import logging
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
from redis.exceptions import RedisError
from sqlalchemy import select

from nexus_common import (
    advice_store,
    money,
    money_history,
    money_mail,
    money_orders,
    money_repair,
    money_salary,
    money_stocks,
)
from nexus_common import money_store as store
from nexus_common.google import GoogleNotConnected, get_grant, services_in
from nexus_common.models import (
    MoneyHolding,
    MoneyMailbox,
    MoneySettings,
    MoneyStock,
    MoneyTxn,
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


class MailboxGoogle(Google):
    """Gmail calls as another mailbox the person connected for Money (read-only)."""

    def __init__(self, deps: TurnDeps, user_id: uuid.UUID, mailbox_id: uuid.UUID):
        super().__init__(deps, user_id)
        self.mailbox_id = mailbox_id

    async def token(self, service: str) -> str:
        async with self._lock:
            cached = self._tokens.get(service)
            if cached and cached[1] > datetime.now(UTC):
                return cached[0]
            async with self.deps.sessionmaker() as db:
                box = await db.get(MoneyMailbox, self.mailbox_id)
                try:
                    if box is None:
                        raise GoogleNotConnected(service)
                    token = await money_mail.access_token(
                        db, self.deps.settings, box, self.deps.google_http
                    )
                except GoogleNotConnected:
                    raise GoogleError(money_mail.REFUSED) from None
                except httpx.HTTPError as exc:
                    raise GoogleError(f"Couldn't reach Google: {type(exc).__name__}") from exc
                finally:
                    # Keep a refreshed token, or the note that Google refused it.
                    await db.commit()
            self._tokens[service] = (token, datetime.now(UTC) + timedelta(minutes=2))
        return token


# On the first sync, read this far back.
FIRST_SYNC = timedelta(days=90)
# Read again a little before the last sync, for mail that arrived late.
OVERLAP = timedelta(days=1)
MAX_MESSAGES = 1500
# Reading old mail for history goes further, up to two years of alerts.
MAX_HISTORY_MESSAGES = 6000
PARALLEL = 4
# Gmail allows 15,000 quota units a minute per mailbox and reading one message costs 5, so at
# most 50 a second in theory; in practice Google slows a mailbox down well before that, so a
# long read goes gently and saves as it goes (BATCH messages at a time).
READS_PER_SECOND = 8.0
BATCH = 100


class Pace:
    """Spaces calls evenly: at most `per_second` starts a second, shared by parallel tasks."""

    def __init__(self, per_second: float):
        self.gap = 1 / per_second
        self.next = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        async with self.lock:
            now = loop.time()
            at = max(now, self.next)
            self.next = at + self.gap
        if at > now:
            await asyncio.sleep(at - now)


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


class PartlyRead(GoogleError):
    """Google stopped a read part way; what was read before it is saved (`counts`)."""

    def __init__(self, message: str, counts: dict):
        super().__init__(message)
        self.counts = counts


def _empty_counts() -> dict:
    return {"messages": 0, "added": 0, "matched": 0, "skipped": 0, "bills": 0, "emis": 0,
            "orders": 0, "orders_matched": 0, "statements": 0}  # fmt: skip


async def _already_read(deps: TurnDeps, user_id: uuid.UUID, ids: list[str]) -> set[str]:
    """Messages already in Money as a transaction or an order, so a read that's repeated after
    Google stopped it part way doesn't fetch them again."""
    found: set[str] = set()
    async with deps.sessionmaker() as db:
        for i in range(0, len(ids), 1000):
            found |= set(
                (
                    await db.scalars(
                        select(MoneyTxn.source_ref).where(
                            MoneyTxn.user_id == user_id, MoneyTxn.source_ref.in_(ids[i : i + 1000])
                        )
                    )
                ).all()
            )
    return found


async def _read_alerts(
    deps: TurnDeps,
    g: Google,
    user: User,
    mark: datetime | None,
    back_days: int | None = None,
    mailbox_id: uuid.UUID | None = None,
) -> dict:
    """Alerts since `mark` (the newest mail read last time) from one mailbox: the sign-in
    Gmail, or another one connected for Money (mailbox_id).

    Mail is read oldest first in batches, and each batch is saved before the next is fetched, so
    when Google slows the reading down part way (its per-minute quota), what came before is
    kept and the mark only moves past mail that was read. The next run carries on from there.
    """
    if back_days:
        since = utcnow() - timedelta(days=back_days)
        cap = MAX_HISTORY_MESSAGES
    else:
        since = (mark or utcnow() - FIRST_SYNC) - OVERLAP
        cap = MAX_MESSAGES
    after = f"after:{int(since.timestamp())}"
    ids = await _list_ids(g, f"{money.ALERT_QUERY} {after}", cap)
    for query in (money.CARD_BILL_QUERY, money.EMI_QUERY, money.STATEMENT_QUERY):
        ids += [i for i in await _list_ids(g, f"{query} {after}", 200) if i not in ids]
    # Order emails (Swiggy, Amazon, a cab...): counted apart so the alert counts stay honest.
    seen = set(ids)
    order_ids = [
        i for i in await _list_ids(g, f"{money_orders.ORDER_QUERY} {after}", cap) if i not in seen
    ]
    counts = _empty_counts()
    counts["messages"] = len(ids)
    done = await _already_read(deps, user.id, [*ids, *order_ids])
    counts["skipped"] = len(done & seen)
    # Gmail lists newest first; read oldest first so the mark can follow.
    todo = [i for i in reversed(ids) if i not in done]
    todo_orders = [i for i in reversed(order_ids) if i not in done]
    tz = zone(user)
    pace = Pace(READS_PER_SECOND)
    gate = asyncio.Semaphore(PARALLEL)

    async def fetch(mid: str) -> tuple[str, dict]:
        async with gate:
            await pace.wait()
            return mid, await g.call(
                "gmail", "GET", f"{GMAIL}/messages/{mid}", params={"format": "full"}
            )

    # Orders last, so each can find the payment its bank already reported.
    batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
    batches += [todo_orders[i : i + BATCH] for i in range(0, len(todo_orders), BATCH)]
    for batch in batches:
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(fetch(mid)) for mid in batch]
        except* GoogleError as group:
            # The rest of the batch was cancelled with it; nothing more is fetched this run.
            raise PartlyRead(str(group.exceptions[0]), counts) from group.exceptions[0]
        await _save_batch(
            deps, user, tz, [t.result() for t in tasks], counts, mailbox_id, bool(back_days)
        )
    if not batches:
        await _save_batch(deps, user, tz, [], counts, mailbox_id, bool(back_days))
    return counts


async def _save_batch(
    deps: TurnDeps,
    user: User,
    tz,
    messages: list[tuple[str, dict]],
    counts: dict,
    mailbox_id: uuid.UUID | None,
    history: bool,
) -> None:
    """Parse and store one batch of messages, then move the mailbox's mark past them."""
    parsed: list[tuple[money.Parsed, str]] = []
    bills: list[money.CardBill] = []
    bookings: list[money.EmiBooking] = []
    orders: list[tuple[money_orders.Order, str]] = []
    statements: list[tuple[money.StatementMail, str, datetime]] = []
    newest: datetime | None = None
    for mid, msg in messages:
        received = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000, UTC)
        newest = max(newest or received, received)
        body, _ = message_body(msg.get("payload") or {})
        subject, sender = _header(msg, "subject"), _header(msg, "from")
        day = received.astimezone(tz).date()
        has_pdf = _pdf_part(msg.get("payload") or {}) is not None
        if found := money.parse_statement_mail(subject, day, sender, has_pdf=has_pdf):
            # Offered on the Tracking panel; its password is typed there when it's imported.
            statements.append((found, mid, received))
        if bill := money.parse_card_bill(subject, body, day, sender=sender):
            bills.append(bill)
        elif booking := money.parse_emi_booking(subject, body, day, sender=sender):
            bookings.append(booking)
        elif p := money.parse_alert(subject, body, day, sender=sender):
            parsed.append((p, mid))
        elif order := money_orders.parse_order(subject, body, day, sender=sender):
            orders.append((order, mid))
    async with deps.sessionmaker() as db:
        for found, mid, received in statements:
            counts["statements"] += await store.record_mail_statement(
                db, user.id, mailbox_id, mid, received, found
            )
        for bill in sorted(bills, key=lambda b: b.statement_on):
            counts["bills"] += await store.record_card_bill(db, user.id, bill)
        # Oldest first, so an account's rows arrive in order.
        for p, mid in sorted(parsed, key=lambda x: x[0].occurred_on):
            got = await store.ingest(
                db, deps.settings, user.id, [p], source="alert", source_ref=mid
            )
            for k, v in got.items():
                counts[k] += v
        # After the alerts, so the purchase an EMI replaces is already in.
        for booking in bookings:
            counts["emis"] += await store.record_emi_booking(db, user.id, booking) is not None
        for order, mid in sorted(orders, key=lambda x: x[0].occurred_on):
            got = await store.record_order(db, deps.settings, user.id, order, mid)
            counts["orders"] += got == "added"
            counts["orders_matched"] += got == "matched"
        # Reading old mail for history never moves the mark; a batch of new mail moves it past
        # what's just been read (oldest first, so everything before it is in too).
        box = await db.get(MoneyMailbox, mailbox_id) if mailbox_id else None
        row = box if box is not None else await store.settings_for(db, user.id)
        field = "synced_until" if box is not None else "gmail_synced_until"
        mark = getattr(row, field)
        if mailbox_id and box is None:
            pass  # the mailbox was removed while it was being read
        elif newest and not history and (mark is None or newest > mark):
            setattr(row, field, newest)
        elif mark is None and not messages:
            setattr(row, field, utcnow())
        if box is not None:
            box.synced_at = utcnow()
            box.last_error = None
        elif mailbox_id is None:
            row.gmail_synced_at = utcnow()
            row.last_error = None
        await db.commit()


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


READING_KEY = "nexus:money:reading:{}"


async def sync_user(
    deps: TurnDeps, user_id: uuid.UUID, *, back_days: int | None = None
) -> dict | None:
    """One read at a time per person: the hourly check and a read back running together would
    double the rate Gmail sees, which is what made Google slow the reading down."""
    key = READING_KEY.format(user_id)
    try:
        free = await deps.redis.set(key, "1", nx=True, ex=1800)
    except RedisError:
        free = True
    if not free:
        log.info("money.sync_busy", extra={"user_id": str(user_id)})
        if back_days:
            await _refund_history(deps, user_id)
            await _tell_history(
                deps, user_id, back_days, None,
                "Money was already reading your mail. Try the read back again in a few minutes.",
            )  # fmt: skip
        return None
    try:
        return await _sync_user(deps, user_id, back_days=back_days)
    finally:
        try:
            await deps.redis.delete(key)
        except RedisError:
            pass


async def _sync_user(
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
        grant = await get_grant(db, deps.settings, user_id)
        primary = grant is not None and "gmail" in services_in(grant.scopes)
        boxes = [(b.id, b.synced_until) for b in await money_mail.mailboxes(db, user_id)]
        db.expunge(prefs)
        db.expunge(user)
    counts: dict | None = None
    holdings = None
    error: str | None = None
    if primary or not boxes:
        g = Google(deps, user_id)
        try:
            counts = await _read_alerts(deps, g, user, prefs.gmail_synced_until, back_days)
            holdings = await _read_cas(deps, g, user, prefs)
        except GoogleError as exc:
            if isinstance(exc, PartlyRead):
                counts = exc.counts
            # The text is about the connection (not connected, API off), never the mail itself.
            error = str(exc)
            async with deps.sessionmaker() as db:
                row = await store.settings_for(db, user_id)
                row.last_error = error[:300]
                await db.commit()
            log.warning(
                "money.sync_failed", extra={"user_id": str(user_id), "error": type(exc).__name__}
            )
    for box_id, mark in boxes:
        try:
            got = await _read_alerts(
                deps, MailboxGoogle(deps, user_id, box_id), user, mark, back_days, box_id
            )
        except GoogleError as exc:
            error = error or str(exc)
            if isinstance(exc, PartlyRead):
                got = exc.counts
                counts = (
                    got if counts is None else {k: counts.get(k, 0) + v for k, v in got.items()}
                )
            async with deps.sessionmaker() as db:
                box = await db.get(MoneyMailbox, box_id)
                if box is not None:
                    box.last_error = box.last_error or str(exc)[:300]
                    await db.commit()
            log.warning(
                "money.sync_failed",
                extra={"user_id": str(user_id), "mailbox_id": str(box_id),
                       "error": type(exc).__name__},
            )  # fmt: skip
            continue
        counts = got if counts is None else {k: counts.get(k, 0) + v for k, v in got.items()}
    if back_days and error:
        await _refund_history(deps, user_id)
    if counts is None:
        if back_days:
            await _tell_history(deps, user_id, back_days, None, error)
        return None
    if not primary:
        # Only other mailboxes are read: the check still counts as done.
        async with deps.sessionmaker() as db:
            row = await store.settings_for(db, user_id)
            row.gmail_synced_at = utcnow()
            row.last_error = None if error is None else row.last_error
            await db.commit()
    if holdings is not None:
        counts["holdings"] = holdings
    if back_days:
        await _tell_history(deps, user_id, back_days, counts, error)
    log.info("money.synced", extra={"user_id": str(user_id), **counts})
    if any(counts.get(k) for k in ("added", "matched", "bills", "emis", "orders",
                                   "orders_matched", "statements")) or holdings:  # fmt: skip
        await store.changed(deps.redis, user_id, **counts)
    return counts


async def _refund_history(deps: TurnDeps, user_id: uuid.UUID) -> None:
    """A read back that Google cut short doesn't count against the day's few (the API's
    ratelimit.hit bucket "money-history", one window a day)."""
    key = f"nexus:rl:money-history:{user_id}:{int(time.time() // 86400)}"
    try:
        if int(await deps.redis.get(key) or 0) > 0:
            await deps.redis.decr(key)
    except RedisError as exc:
        log.warning("money.history_refund_failed", extra={"error": type(exc).__name__})


def history_note(months: int, counts: dict | None, error: str | None) -> tuple[str, str]:
    """The bell notice after reading old alerts: what was found, in counts only. Reading back
    runs in the background, so without this a read that finds nothing looks like it never ran."""
    if error and counts is None:
        return (
            "Couldn't read your old alert emails",
            f"{error[:200]} Fix it in Money › Import and settings, then try again; this read "
            "didn't count against today's limit.",
        )
    found = counts["added"] + counts["matched"] + counts["skipped"]
    orders = counts.get("orders", 0) + counts.get("orders_matched", 0)
    if not counts["messages"] and not orders:
        return (
            "No bank alert emails found",
            f"Nothing in the last {months} months of Gmail looked like a bank or card alert. If "
            "your banks send alerts by SMS only, switch on email alerts in each bank's app, or add "
            "statements and screenshots instead.",
        )
    if not found and not counts["bills"] and not counts["emis"] and not orders:
        return (
            "Old alert emails read, but none could be understood",
            f"{counts['messages']} possible alert emails, but none matched the formats Money "
            "knows. Send feedback naming the bank and Nexus can learn its format; meanwhile "
            "statements and screenshots work.",
        )
    parts = [f"{counts['added']} new transactions"]
    if counts["matched"] + counts["skipped"]:
        parts.append(f"{counts['matched'] + counts['skipped']} already in Money")
    if counts["bills"]:
        parts.append(f"{counts['bills']} card bills")
    if counts["emis"]:
        parts.append(f"{counts['emis']} EMIs")
    if orders:
        parts.append(f"{orders} orders from shop emails")
    rest = counts["messages"] - found - counts["bills"] - counts["emis"]
    tail = (
        f" {rest} other emails weren't transactions (OTPs, reminders, offers)." if rest > 0 else ""
    )
    if error:
        return (
            "Read some of your old emails, but not all",
            ", ".join(parts) + f". One mailbox couldn't be read: {error[:160]} Read back again "
            "when it's fixed; what's already in is skipped, and this read didn't count against "
            "today's limit.",
        )
    return f"Read {months} months of alert emails", ", ".join(parts) + "." + tail


async def _tell_history(
    deps: TurnDeps, user_id: uuid.UUID, back_days: int, counts: dict | None, error: str | None
) -> None:
    title, body = history_note(max(back_days // 31, 1), counts, error)
    async with deps.sessionmaker() as db:
        await notify(
            db, deps.redis, deps.settings, user_id, NotificationKind.info, title, body,
            url="/money/transactions?month=all", tag="money-history",
        )  # fmt: skip
        await db.commit()


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


async def repair_all(deps: TurnDeps) -> int:
    """Each person's one-off repair (money_repair), once per version, then their expected
    salary rows brought up to date. Returns how many people were repaired."""
    async with deps.sessionmaker() as db:
        ids = list(
            (
                await db.scalars(
                    select(MoneySettings.user_id)
                    .join(User, User.id == MoneySettings.user_id)
                    .where(User.status == UserStatus.active)
                )
            ).all()
        )
    done = 0
    for uid in ids:
        flag = f"money:repaired:{money_repair.VERSION}:{uid}"
        try:
            async with deps.sessionmaker() as db:
                fixed = None
                if not await deps.redis.exists(flag):
                    fixed = await money_repair.repair(db, deps.settings, uid)
                filled = await money_salary.fill(db, uid, local_today(await db.get(User, uid)))
                await db.commit()
            if fixed is not None:
                await deps.redis.set(flag, "1")
                done += 1
                # Emails this version reads that the last one couldn't: read them again.
                await sync_user(deps, uid, back_days=money_repair.REREAD_DAYS)
            if (fixed and any(fixed.values())) or any(filled.values()):
                await store.changed(deps.redis, uid)
        except Exception:
            log.exception("money.repair_crashed", extra={"user_id": str(uid)})
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


async def reprice_stocks(
    deps: TurnDeps, quotes: dict | None = None, today: date | None = None
) -> int:
    """Price every share at NSE's latest close and refresh the symbol list the add dialog
    searches. Returns how many changed."""
    async with deps.sessionmaker() as db:
        stocks = (await db.scalars(select(MoneyStock))).all()
        if quotes is None:
            try:
                quotes = await money_stocks.fetch_quotes(today or date.today())
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("money.quotes_unavailable", extra={"error": type(exc).__name__})
                return 0
            await deps.redis.set(
                money_stocks.CACHE_KEY, money_stocks.dump(quotes), ex=money_stocks.CACHE_SECONDS
            )
        changed = [s for s in stocks if store.reprice(s, quotes)]
        await db.commit()
    for uid in {s.user_id for s in changed}:
        await store.changed(deps.redis, uid)
    log.info("money.repriced", extra={"count": len(changed), "quotes": len(quotes)})
    return len(changed)


async def record_history(deps: TurnDeps) -> int:
    """Store tonight's value of every share and fund holding. Returns how many rows."""
    async with deps.sessionmaker() as db:
        users = set(await db.scalars(select(MoneyStock.user_id).distinct()))
        users |= set(await db.scalars(select(MoneyHolding.user_id).distinct()))
        stored = 0
        for uid in users:
            stored += await money_history.record(db, uid)
        await db.commit()
    log.info("money.history_recorded", extra={"users": len(users), "count": stored})
    return stored


# A pause between NSE files, so a five-year fill doesn't hammer their archive.
FILL_PAUSE = 0.4


async def fill_share_history(
    deps: TurnDeps,
    user_id: uuid.UUID | None = None,
    today: date | None = None,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Fill in earlier closes for shares whose history isn't there yet (history_from isn't
    their bought_on). Stops at the first NSE failure, keeping what it has; the next night
    carries on. Returns how many days were stored."""
    today = today or date.today()
    own = client is None
    client = client or httpx.AsyncClient(
        timeout=60, follow_redirects=True, headers=money_stocks.HEADERS
    )
    stored = 0
    try:
        async with deps.sessionmaker() as db:
            q = select(MoneyStock).where(MoneyStock.bought_on.is_not(None))
            if user_id:
                q = q.where(MoneyStock.user_id == user_id)
            todo = [s for s in (await db.scalars(q)).all() if s.history_from != s.bought_on]
            for stock in todo:
                closes: list[tuple[date, int]] = []
                seen: set[date] = set()
                try:
                    for day in money_history.fill_days(stock.bought_on, today):
                        cached = await deps.redis.exists(
                            money_stocks.CLOSES_KEY.format(day.isoformat())
                        )
                        found = await money_stocks.closes_from(deps.redis, day, today, client)
                        if not cached and FILL_PAUSE:
                            await asyncio.sleep(FILL_PAUSE)
                        if not found or found[0] in seen:
                            continue
                        trading_day, by_isin = found
                        seen.add(trading_day)
                        if stock.isin and stock.isin in by_isin:
                            closes.append((trading_day, by_isin[stock.isin]))
                except httpx.HTTPError as exc:
                    await money_history.save_filled(db, stock, closes)
                    await db.commit()
                    log.warning(
                        "money.history_fill_stopped",
                        extra={"stock_id": str(stock.id), "error": type(exc).__name__},
                    )
                    return stored + len(closes)
                stored += await money_history.save_filled(db, stock, closes)
                stock.history_from = stock.bought_on
                await db.commit()
                log.info(
                    "money.history_filled", extra={"stock_id": str(stock.id), "days": len(closes)}
                )
    finally:
        if own:
            await client.aclose()
    return stored


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
            sent += await _tell_advice(deps, db, user, today)
        await db.commit()
    if sent:
        log.info("money.reminders_sent", extra={"count": sent})
    return sent


async def _tell_advice(deps: TurnDeps, db, user: User, today) -> int:
    """One bell notice when new items land in Advice's "Do now", with a count only: the
    lock screen never shows what they are."""
    out = await advice_store.view(db, user, today)
    fresh = []
    for item in out["items"]:
        if item["level"] != "act":
            continue
        if await deps.redis.set(f"advice:told:{user.id}:{item['key']}", "1", nx=True,
                                ex=REMIND_KEEP):  # fmt: skip
            fresh.append(item)
    if not fresh:
        return 0
    n = len(fresh)
    await notify(
        db, deps.redis, deps.settings, user.id, NotificationKind.reminder,
        f"{n} new thing{'s' if n != 1 else ''} to sort out",
        "Open Advice to see what and why.", url="/advice", tag=f"advice-{today}",
    )  # fmt: skip
    return 1
