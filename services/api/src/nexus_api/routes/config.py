"""The Admin page's Configuration tab: plain runtime settings the owner changes without a
redeploy (nexus_common/config.py). Owners only. Secrets are never listed or accepted."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from nexus_api.deps import DbDep, OwnerUser, RedisDep, SettingsDep
from nexus_common import config
from nexus_common.models import AuditLog, User

router = APIRouter(prefix="/api/admin/config", tags=["admin"])


class ConfigIn(BaseModel):
    # Setting name to its new value, or null to go back to the .env value.
    values: dict[str, Any] = Field(max_length=len(config.OPTIONS))


def _restart_command(services: list[str]) -> str:
    return f"./scripts/compose.sh restart {' '.join(services)}"


async def _view(db, redis, settings) -> dict:
    values, version, row = await config.load(db)
    defaults = config.defaults(settings)
    running = await config.reports(redis)
    updated_by = await db.get(User, row.updated_by) if row and row.updated_by else None
    options = []
    for opt in config.OPTIONS:
        overridden = opt.name in values
        # Services still running the old value because they read it only at start-up.
        waiting = sorted(
            {r["service"] for r in running if opt.name in (r.get("pending_restart") or [])}
        )
        options.append(
            {
                "name": opt.name,
                "env": opt.env,
                "group": opt.group,
                "label": opt.label,
                "help": opt.help,
                "kind": opt.kind,
                "choices": list(opt.choices),
                "min": opt.min,
                "max": opt.max,
                "unit": opt.unit,
                "restart": list(opt.restart),
                "value": values[opt.name] if overridden else defaults[opt.name],
                "default": defaults[opt.name],
                "overridden": overridden,
                "waiting_for_restart": waiting,
                "restart_command": _restart_command(waiting) if waiting else None,
            }
        )
    return {
        "groups": list(config.GROUPS),
        "options": options,
        "version": version,
        "updated_at": row.updated_at if row else None,
        "updated_by": (updated_by.name or updated_by.email) if updated_by else None,
        "services": running,
    }


@router.get("")
async def get_config(actor: OwnerUser, db: DbDep, redis: RedisDep, settings: SettingsDep):
    return await _view(db, redis, settings)


@router.put("")
async def put_config(
    body: ConfigIn,
    request: Request,
    actor: OwnerUser,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
):
    """Store the changes, apply them on this replica now and tell the others, which pick them up
    within a few seconds. Settings a service reads only at start-up wait for its restart."""
    try:
        values, version, before = await config.save(db, body.values, actor.id)
    except config.ConfigError as exc:
        name = exc.args[1] if len(exc.args) > 1 else None
        label = config.BY_NAME[name].label if name in config.BY_NAME else name
        message = f"{label}: {exc.args[0]}" if label else exc.args[0]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message) from None
    defaults = config.defaults(settings)
    changes = {
        name: {"from": before.get(name, defaults[name]), "to": values.get(name, defaults[name])}
        for name in body.values
        if before.get(name, defaults[name]) != values.get(name, defaults[name])
        or (name in before) != (name in values)
    }
    db.add(AuditLog(actor_id=actor.id, action="config.changed", data={"changes": changes}))
    await db.commit()
    config.apply(settings, values, request.app.state.service, version=version)
    await config.announce(redis, version)
    await config.report(redis, settings, request.app.state.service)
    return await _view(db, redis, settings)
