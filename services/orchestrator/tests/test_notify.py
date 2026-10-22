import pytest
from arq import Retry

from nexus_api.users import create_user
from nexus_common import mail
from nexus_common.models import AccessRequest, AccessRequestStatus, RoleName, UserStatus
from nexus_orchestrator import notify


@pytest.fixture
def smtp_deps(deps, settings):
    deps.settings = settings.model_copy(
        update={"smtp_host": "smtp.example.com", "smtp_from": "nexus@example.com"}
    )
    return deps


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def fake_send(settings, msg):
        out.append(msg)

    monkeypatch.setattr(mail, "send", fake_send)
    return out


async def make_request(sessionmaker, status=AccessRequestStatus.pending) -> AccessRequest:
    async with sessionmaker() as db:
        for email, role, state in [
            ("owner@example.com", RoleName.owner, UserStatus.active),
            ("admin@example.com", RoleName.admin, UserStatus.active),
            ("gone@example.com", RoleName.admin, UserStatus.disabled),
            ("member@example.com", RoleName.member, UserStatus.active),
        ]:
            await create_user(db, email=email, role=role, status=state)
        req = AccessRequest(
            email="new@example.com",
            name="Ada\nBcc: evil@example.com",
            note="Research team",
            status=status,
        )
        db.add(req)
        await db.commit()
        return req


async def test_new_request_emails_active_owners_and_admins(smtp_deps, sessionmaker, sent):
    req = await make_request(sessionmaker)
    await notify.access(smtp_deps, "requested", req.id)
    [msg] = sent
    assert msg["To"] == "admin@example.com, owner@example.com"
    # A line break in the name can't add headers.
    assert "\n" not in msg["Subject"] and msg["Bcc"] is None
    body = msg.get_content()
    assert "Research team" in body and "/people" in body


async def test_notify_list_overrides_recipients(smtp_deps, sessionmaker, sent):
    smtp_deps.settings = smtp_deps.settings.model_copy(
        update={"access_request_notify": "Me@Example.com"}
    )
    req = await make_request(sessionmaker)
    await notify.access(smtp_deps, "requested", req.id)
    assert sent[0]["To"] == "me@example.com"


async def test_approval_emails_the_person(smtp_deps, sessionmaker, sent):
    req = await make_request(sessionmaker, AccessRequestStatus.approved)
    await notify.access(smtp_deps, "approved", req.id)
    assert sent[0]["To"] == "new@example.com" and "/login" in sent[0].get_content()


async def test_decided_requests_are_not_announced(smtp_deps, sessionmaker, sent):
    req = await make_request(sessionmaker, AccessRequestStatus.denied)
    await notify.access(smtp_deps, "requested", req.id)
    await notify.access(smtp_deps, "approved", req.id)
    assert sent == []


async def test_no_smtp_skips(deps, sessionmaker, sent):
    req = await make_request(sessionmaker)
    await notify.access(deps, "requested", req.id)
    assert sent == []


async def test_transient_failures_retry_then_give_up(smtp_deps, sessionmaker, monkeypatch):
    req = await make_request(sessionmaker)

    async def flaky(settings, msg):
        raise mail.MailError("421 busy", transient=True)

    monkeypatch.setattr(mail, "send", flaky)
    with pytest.raises(Retry) as exc:
        await notify.access(smtp_deps, "requested", req.id, attempt=2)
    assert exc.value.defer_score == 60_000
    # The last try logs and stops rather than failing the job.
    await notify.access(smtp_deps, "requested", req.id, attempt=notify.MAX_TRIES)
