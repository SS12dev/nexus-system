import io
import json
import logging
import uuid
import zipfile

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select, update

from nexus_api.appsettings import maintenance_notice
from nexus_api.cache import cached_spend
from nexus_api.deps import (
    CurrentUser,
    DbDep,
    EmbedderDep,
    GatewayDep,
    QueueDep,
    RedisDep,
    SettingsDep,
    StorageDep,
)
from nexus_api.onboarding import apply as apply_onboarding
from nexus_api.schemas import (
    BudgetOut,
    ChoiceOut,
    ClientErrorIn,
    DeleteAccountIn,
    GoogleAccessOut,
    MeIn,
    MeOut,
    OnboardingIn,
    OnboardingOut,
    PackOut,
    PackRoutineOut,
    ProfilesOut,
    SessionOut,
    TermsIn,
    UserOut,
)
from nexus_api.sessions import hash_token, revoke_all
from nexus_common import google
from nexus_common.access import pending_count
from nexus_common.litellm import active_key, get_budget
from nexus_common.models import (
    TERMS_VERSION,
    AuditLog,
    Chat,
    File,
    Memory,
    Message,
    Project,
    RoleName,
    Routine,
    Session,
    User,
    utcnow,
)
from nexus_common.profiles import INTERESTS, Pack, load_packs, suggested_prompts
from nexus_common.routines import ScheduleError, check_timezone, describe, routine_payload

router = APIRouter(prefix="/api", tags=["me"])
web_log = logging.getLogger("nexus.web")
log = logging.getLogger(__name__)


@router.get("/me", response_model=MeOut)
async def me(
    user: CurrentUser, db: DbDep, settings: SettingsDep, gateway: GatewayDep, redis: RedisDep
) -> MeOut:
    budget = await get_budget(db, settings, user)
    await db.commit()
    key = await active_key(db, user.id)
    spend = await cached_spend(redis, gateway, key.secret) if key else None
    channels = [
        c
        for c, on in (
            ("push", settings.push_enabled),
            ("email", settings.smtp_enabled),
            ("whatsapp", settings.whatsapp_enabled),
        )
        if on
    ]
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        assistant_name=user.assistant_name,
        budget=BudgetOut(max_usd=budget.max_usd, duration=budget.duration, spend_usd=spend),
        notice=await maintenance_notice(db),
        pending_access=await pending_count(db) if user.has_role(RoleName.admin) else None,
        onboarded_at=user.onboarded_at,
        timezone=user.timezone,
        profile=user.profile or {},
        phone=user.phone,
        share_usage=user.share_usage,
        channels=channels,
        terms_version=user.terms_version,
        terms_current=TERMS_VERSION,
        terms_needed=terms_needed(user),
    )


def terms_needed(user: User) -> bool:
    return user.role != RoleName.owner and user.terms_version != TERMS_VERSION


def accept_terms(db, user: User, version: str | None) -> None:
    """Record agreement to the current terms; any other version is refused."""
    if version != TERMS_VERSION:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Agree to the current Terms of Service and Privacy Policy to continue",
        )
    if user.terms_version != version:
        user.terms_version, user.terms_accepted_at = version, utcnow()
        db.add(
            AuditLog(
                actor_id=user.id,
                action="user.terms_accepted",
                target=str(user.id),
                data={"version": version},
            )
        )


@router.post("/me/terms", response_model=MeOut)
async def agree_to_terms(
    body: TermsIn,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    gateway: GatewayDep,
    redis: RedisDep,
) -> MeOut:
    """Agree to the current Terms of Service and Privacy Policy (after they changed)."""
    accept_terms(db, user, body.version)
    await db.commit()
    return await me(user, db, settings, gateway, redis)


@router.patch("/me", response_model=MeOut)
async def update_me(
    body: MeIn,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    gateway: GatewayDep,
    redis: RedisDep,
) -> MeOut:
    """Name (or rename) the user's assistant, or change their time zone, WhatsApp number or
    whether the self-review may count their request topics."""
    if body.assistant_name is not None and body.assistant_name != user.assistant_name:
        user.assistant_name = body.assistant_name
        db.add(AuditLog(actor_id=user.id, action="user.assistant_named", target=str(user.id)))
    if body.timezone is not None and body.timezone != user.timezone:
        try:
            user.timezone = check_timezone(body.timezone)
        except ScheduleError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if body.phone is not None:
        user.phone = body.phone or None
    if body.share_usage is not None:
        user.share_usage = body.share_usage
    if body.tour_done is not None:
        user.profile = {**(user.profile or {}), "tour_done": body.tour_done}
    await db.commit()
    return await me(user, db, settings, gateway, redis)


@router.get("/profiles", response_model=ProfilesOut)
async def profiles(user: CurrentUser, settings: SettingsDep) -> ProfilesOut:
    """What the setup interview offers: professions, interests, and every profile pack with
    its prompts and routines (times worked out from the person's day start)."""
    packs = load_packs(settings.library_path)
    day_start = (user.profile or {}).get("day_start")
    return ProfilesOut(
        roles=[ChoiceOut(id=p.id, label=p.label, blurb=p.blurb) for p in packs if p.kind == "role"],
        interests=[ChoiceOut(id=k, label=v) for k, v in INTERESTS.items()],
        packs=[pack_out(p, day_start) for p in packs],
    )


def pack_out(p: Pack, day_start: str | None) -> PackOut:
    routines = []
    for r in p.routines:
        schedule = r.schedule(day_start)
        parts = dict(x.split("=", 1) for x in schedule.split(";"))
        time = f"{int(parts['BYHOUR']):02d}:{int(parts['BYMINUTE']):02d}"
        routines.append(
            PackRoutineOut(
                key=f"{p.id}/{r.id}",
                name=r.name,
                prompt=r.prompt,
                schedule=schedule,
                schedule_text=describe(schedule),
                time=time,
                at=r.time,
                needs=list(r.needs),
                only_if_new=r.only_if_new,
                default_on=r.default_on,
            )
        )
    return PackOut(
        id=p.id,
        label=p.label,
        kind=p.kind,
        blurb=p.blurb,
        match=list(p.match),
        prompts=list(p.prompts),
        routines=routines,
    )


@router.post("/me/onboarding", response_model=OnboardingOut)
async def onboarding(
    body: OnboardingIn,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    gateway: GatewayDep,
    redis: RedisDep,
    queue: QueueDep,
    embedder: EmbedderDep,
) -> OnboardingOut:
    """The setup interview, finished or skipped: store the answers as memories, add the routines
    they picked, and write a welcome chat."""
    if not user.assistant_name and not body.assistant_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Name your assistant first")
    if terms_needed(user) or body.accept_terms:
        accept_terms(db, user, body.accept_terms)
    packs = load_packs(settings.library_path)
    chat_id, added = await apply_onboarding(db, settings, user, body, packs, embedder)
    await db.commit()
    try:
        await queue.enqueue_profile_rebuild(user.id)
    except Exception:
        log.warning("Could not queue a profile rebuild after onboarding", exc_info=True)
    return OnboardingOut(
        me=await me(user, db, settings, gateway, redis), chat_id=chat_id, routines=added
    )


@router.get("/me/suggestions", response_model=list[str])
async def suggestions(user: CurrentUser, settings: SettingsDep) -> list[str]:
    """Prompts for the home screen, from the person's profile packs."""
    return suggested_prompts(load_packs(settings.library_path), user.profile or {}, 4)


@router.get("/me/sessions", response_model=list[SessionOut])
async def my_sessions(
    request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> list[SessionOut]:
    """Browsers signed in as this user, newest first."""
    current = hash_token(request.cookies.get(settings.session_cookie_name) or "")
    rows = await db.scalars(
        select(Session)
        .where(
            Session.user_id == user.id,
            Session.revoked_at.is_(None),
            Session.expires_at > utcnow(),
        )
        .order_by(Session.created_at.desc())
    )
    out = []
    for s in rows.all():
        item = SessionOut.model_validate(s)
        item.current = s.token_hash == current
        out.append(item)
    return out


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_session(session_id: uuid.UUID, user: CurrentUser, db: DbDep) -> None:
    s = await db.scalar(select(Session).where(Session.id == session_id, Session.user_id == user.id))
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    s.revoked_at = s.revoked_at or utcnow()
    db.add(AuditLog(actor_id=user.id, action="session.revoked", target=str(s.id)))
    await db.commit()


@router.post("/me/sessions/sign-out-others", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_others(
    request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> None:
    """Sign out every other browser, keeping this one."""
    current = hash_token(request.cookies.get(settings.session_cookie_name) or "")
    await revoke_all(db, user, keep_hash=current)
    db.add(AuditLog(actor_id=user.id, action="session.revoked_others", target=str(user.id)))
    await db.commit()


@router.get("/me/google", response_model=GoogleAccessOut)
async def google_access(user: CurrentUser, db: DbDep, settings: SettingsDep) -> GoogleAccessOut:
    """Which Google services this user let Nexus use, out of those it asks for."""
    grant = await google.get_grant(db, settings, user.id)
    await db.commit()
    return GoogleAccessOut(
        available=settings.oidc_enabled and bool(settings.google_service_list),
        requested=settings.google_service_list,
        granted=google.services_in(grant.scopes) if grant else [],
        connected_at=grant.updated_at if grant else None,
    )


@router.delete("/me/google", status_code=status.HTTP_204_NO_CONTENT)
async def google_disconnect(
    request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> None:
    """Revoke Nexus's access to the user's Google services."""
    await google.disconnect(db, settings, user.id, http=request.app.state.google_http)
    await db.commit()


@router.post("/me/client-error", status_code=status.HTTP_204_NO_CONTENT)
async def client_error(body: ClientErrorIn, user: CurrentUser) -> None:
    """Log a page that crashed in someone's browser, so it shows with the other recent errors on
    the Admin page. The per-IP limit applies, and the size of each field is capped."""
    web_log.error(
        "Page error on %s: %s",
        body.page[:300] or "?",
        body.message[:1000],
        extra={"page": body.page, "stack": body.stack[-4000:]},
    )


def _json(rows: list[dict]) -> bytes:
    return json.dumps(rows, indent=2, ensure_ascii=False, default=str).encode()


@router.get("/me/export")
async def export_data(user: CurrentUser, db: DbDep) -> Response:
    """Everything Nexus keeps about the person, as JSON files in a zip: their account and setup
    answers, chats with every message, memories, routines and the list of their files (the
    files themselves download from the Library)."""
    chats = (await db.scalars(select(Chat).where(Chat.user_id == user.id))).all()
    messages = (
        await db.scalars(
            select(Message)
            .join(Chat, Chat.id == Message.chat_id)
            .where(Chat.user_id == user.id)
            .order_by(Message.created_at)
        )
    ).all()
    by_chat: dict[uuid.UUID, list[dict]] = {}
    for m in messages:
        by_chat.setdefault(m.chat_id, []).append(
            {"role": m.role, "content": m.content, "created_at": m.created_at}
        )
    memories = (await db.scalars(select(Memory).where(Memory.user_id == user.id))).all()
    routines = (await db.scalars(select(Routine).where(Routine.user_id == user.id))).all()
    files = (await db.scalars(select(File).where(File.user_id == user.id))).all()
    account = {
        "email": user.email,
        "name": user.name,
        "assistant_name": user.assistant,
        "timezone": user.timezone,
        "phone": user.phone,
        "profile": user.profile,
        "created_at": user.created_at,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("account.json", _json([account]))
        z.writestr(
            "chats.json",
            _json(
                [
                    {
                        "title": c.title,
                        "created_at": c.created_at,
                        "messages": by_chat.get(c.id, []),
                    }
                    for c in chats
                ]
            ),
        )
        z.writestr(
            "memories.json",
            _json(
                [
                    {"kind": m.kind, "content": m.content, "pinned": m.pinned, "source": m.source}
                    for m in memories
                ]
            ),
        )
        z.writestr("routines.json", _json([routine_payload(r) for r in routines]))
        z.writestr(
            "files.json",
            _json([{"name": f.name, "size": f.size, "created_at": f.created_at} for f in files]),
        )
    db.add(AuditLog(actor_id=user.id, action="user.exported", target=str(user.id)))
    await db.commit()
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="nexus-export.zip"'},
    )


@router.post("/me/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: DeleteAccountIn,
    request: Request,
    response: Response,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    gateway: GatewayDep,
    storage: StorageDep,
) -> None:
    """Delete the person's account and everything in it: chats, memories, files, routines,
    teams, their model key and their Google connection. Owners can't (the instance needs
    them); live apps must be deleted on the Apps page first, since they run outside Nexus."""
    if body.confirm.strip().lower() != user.email.lower():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Type your email to confirm")
    if user.role == RoleName.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "An owner's account can't be deleted")
    live = await db.scalar(
        select(func.count()).where(Project.user_id == user.id, Project.app_slug.is_not(None))
    )
    if live:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Delete your apps on the Apps page first, then try again"
        )
    keys = [f.storage_key for f in (await db.scalars(select(File).where(File.user_id == user.id)))]
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:
            log.warning("Could not delete %s from storage", key, exc_info=True)
    try:
        await google.disconnect(db, settings, user.id, http=request.app.state.google_http)
    except Exception:
        log.warning("Could not revoke Google access for a deleted account", exc_info=True)
    if key := await active_key(db, user.id):
        try:
            await gateway.delete_key(key.secret)
        except Exception:
            log.warning("Could not delete a deleted account's gateway key", exc_info=True)
    await db.execute(update(User).where(User.invited_by == user.id).values(invited_by=None))
    db.add(
        AuditLog(
            actor_id=None,
            action="user.deleted_self",
            target=str(user.id),
            data={"files": len(keys)},
        )
    )
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()
    response.delete_cookie(settings.session_cookie_name)
