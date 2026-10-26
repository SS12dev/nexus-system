"""The Admin page's Configuration tab: runtime settings laid over .env without a redeploy."""

import logging

import pytest
from sqlalchemy import select

from nexus_common import config
from nexus_common.models import AuditLog
from nexus_common.settings import Settings


@pytest.fixture(autouse=True)
def restore_settings(settings):
    """The test settings object is shared by the whole session: put back what a test changed."""
    before = {o.name: getattr(settings, o.name) for o in config.OPTIONS}
    yield
    for name, value in before.items():
        setattr(settings, name, value)
    config.forget(settings)


def _option(view: dict, name: str) -> dict:
    return next(o for o in view["options"] if o["name"] == name)


def test_no_secret_is_ever_listed():
    names = {o.name for o in config.OPTIONS}
    for secret in (
        "smtp_password",
        "secret_key",
        "token_encryption_keys",
        "litellm_master_key",
        "oidc_client_secret",
        "tavily_api_key",
        "github_app_private_key_b64",
        "database_url",
        "dev_login_enabled",
    ):
        assert secret not in names
    assert all(o.help and o.label for o in config.OPTIONS)


def test_every_setting_can_come_from_env():
    """compose.yaml passes containers only the variables it names, so a setting missing there
    would ignore .env and the Admin page would show a built-in default as the .env value."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    compose = (root / "compose.yaml").read_text()
    block = compose[compose.index("x-nexus-env:") : compose.index("x-langfuse-env:")]
    passed = set(re.findall(r"^\s+([A-Z][A-Z0-9_]+):", block, re.M))
    example = set(re.findall(r"^#? ?([A-Z][A-Z0-9_]+)=", (root / ".env.example").read_text(), re.M))
    assert [o.env for o in config.OPTIONS if o.env not in passed] == []
    assert [o.env for o in config.OPTIONS if o.env not in example] == []
    # The compose fallbacks are the same as the built-in defaults.
    for o in config.OPTIONS:
        m = re.search(rf"^\s+{o.env}: \$\{{{o.env}:?-(.*)\}}$", block, re.M)
        if m is None or "${" in m.group(1):
            continue
        default = Settings.model_fields[o.name].default
        assert config.validate(o, m.group(1) or default) == config.validate(o, default), o.env


def test_values_are_checked():
    opt = config.BY_NAME
    assert config.validate(opt["chat_model"], "nexus-pro") == "nexus-pro"
    assert config.validate(opt["memory_top_k"], "12") == 12
    assert config.validate(opt["default_budget_usd"], 7.5) == 7.5
    assert config.validate(opt["google_services"], "tasks, Gmail,gmail") == "gmail,tasks"
    assert config.validate(opt["reserved_hostnames"], " Photos, drive ") == "photos,drive"
    for name, bad in [
        ("chat_model", "gpt-4"),
        ("memory_top_k", 2.5),
        ("memory_top_k", 999),
        ("memory_top_k", True),
        ("litellm_budget_duration", "monthly"),
        ("access_request_notify", "not-an-email"),
        ("google_services", "photos"),
        ("langfuse_url", "localhost:3000"),
        ("rate_limit_enabled", "maybe"),
        ("improve_repo", "nexus-system"),
        ("improve_base_branch", "main branch"),
    ]:
        with pytest.raises(config.ConfigError):
            config.validate(opt[name], bad)


async def test_owner_sees_every_setting_with_its_env_default(owner, settings):
    view = (await owner.get("/api/admin/config")).json()
    assert view["groups"] == list(config.GROUPS)
    assert len(view["options"]) == len(config.OPTIONS)
    chat = _option(view, "chat_model")
    assert chat["value"] == chat["default"] == settings.chat_model
    assert chat["env"] == "CHAT_MODEL" and not chat["overridden"]
    assert chat["choices"] == list(config.MODEL_TIERS)


async def test_only_the_owner_can_see_or_change_it(owner, client_for, invite):
    await invite(owner, "admin@example.com", role="admin")
    admin = await client_for("admin@example.com")
    assert (await admin.get("/api/admin/config")).status_code == 403
    resp = await admin.put("/api/admin/config", json={"values": {"chat_model": "nexus-max"}})
    assert resp.status_code == 403


async def test_a_change_applies_at_once_and_is_audited(owner, settings, sessionmaker):
    resp = await owner.put(
        "/api/admin/config",
        json={"values": {"chat_model": "nexus-pro", "rate_limit_messages_per_minute": 5}},
    )
    assert resp.status_code == 200, resp.text
    assert settings.chat_model == "nexus-pro"
    assert settings.rate_limit_messages_per_minute == 5
    view = resp.json()
    assert _option(view, "chat_model")["overridden"]
    assert view["version"] == 1 and view["updated_by"]
    async with sessionmaker() as db:
        entry = await db.scalar(select(AuditLog).where(AuditLog.action == "config.changed"))
    assert entry.data["changes"]["chat_model"]["to"] == "nexus-pro"

    # null goes back to the .env value
    resp = await owner.put("/api/admin/config", json={"values": {"chat_model": None}})
    assert settings.chat_model == _option(resp.json(), "chat_model")["default"]
    assert not _option(resp.json(), "chat_model")["overridden"]


async def test_the_new_value_is_used_by_the_next_request(owner, settings):
    await owner.put("/api/admin/config", json={"values": {"max_upload_mb": 1}})
    resp = await owner.post(
        "/api/files", files={"file": ("big.txt", b"x" * (1024 * 1024 + 10), "text/plain")}
    )
    assert resp.status_code == 413
    assert "1 MB" in resp.json()["detail"]


async def test_bad_values_and_secrets_are_refused(owner, settings):
    before = settings.memory_top_k
    resp = await owner.put("/api/admin/config", json={"values": {"memory_top_k": 1000}})
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("Memories per turn:")
    assert settings.memory_top_k == before
    resp = await owner.put("/api/admin/config", json={"values": {"smtp_password": "hunter2"}})
    assert resp.status_code == 422
    view = (await owner.get("/api/admin/config")).json()
    assert view["version"] == 0


async def test_start_up_only_settings_wait_for_a_restart(owner, settings, redis):
    before = settings.alert_interval_s
    resp = await owner.put("/api/admin/config", json={"values": {"alert_interval_s": 120}})
    assert settings.alert_interval_s == before  # the alert loop read it when the API started
    opt = _option(resp.json(), "alert_interval_s")
    assert opt["value"] == 120 and opt["restart"] == ["api"]
    assert opt["waiting_for_restart"] == ["api"]
    assert opt["restart_command"] == "./scripts/compose.sh restart api"
    # A worker doesn't read it at all, so nothing waits there.
    assert not _option(resp.json(), "sandbox_timeout_s")["waiting_for_restart"]


async def test_other_processes_pick_up_changes(owner, sessionmaker):
    worker = Settings(_env_file=None, env="test", chat_model="nexus-mini", sandbox_timeout_s=120)
    await owner.put(
        "/api/admin/config",
        json={"values": {"chat_model": "nexus-nano", "sandbox_timeout_s": 300}},
    )
    try:
        # What the watcher does on each replica: live settings change, start-up ones wait...
        await config.load_and_apply(worker, sessionmaker, "orchestrator", starting=False)
        assert worker.chat_model == "nexus-nano"
        assert worker.sandbox_timeout_s == 120
        # ...until the worker starts again.
        await config.load_and_apply(worker, sessionmaker, "orchestrator", starting=True)
        assert worker.sandbox_timeout_s == 300
    finally:
        config.forget(worker)


def test_log_settings_reconfigure_logging():
    worker = Settings(_env_file=None, env="test", log_level="INFO")
    root = logging.getLogger()
    level = root.level
    try:
        config.apply(worker, {"log_level": "WARNING"}, "doc-worker")
        assert root.level == logging.WARNING
    finally:
        root.setLevel(level)
        for h in [h for h in root.handlers if getattr(h, "_nexus", False)]:
            root.removeHandler(h)
        config.forget(worker)
