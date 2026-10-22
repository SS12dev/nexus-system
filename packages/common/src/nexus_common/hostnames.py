"""App hostnames: <name>.<APPS_DOMAIN>, one DNS label each (Cloudflare's free certificate covers
*.servhub.cc but not a level below). The owner's apps are named after the app ("2048"); other
people's carry their name too ("2048-priya"). A taken or reserved name gets a short random
suffix ("2048-k7"). Names are kept on the project, so redeploys keep them, and freed on delete."""

import re
import secrets
import string
from collections.abc import Awaitable, Callable

# Never handed to an app: Nexus's own and the usual service names. RESERVED_HOSTNAMES adds more.
RESERVED = {"nexus", "www", "mail", "api", "admin", "traefik"}
LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
NOT_LABEL = re.compile(r"[^a-z0-9]+")
BASE_CHARS = 40
SUFFIX_CHARS = string.ascii_lowercase + string.digits
TRIES = 30


class NoFreeName(RuntimeError):
    pass


def label(text: str, limit: int = BASE_CHARS) -> str:
    return NOT_LABEL.sub("-", (text or "").lower()).strip("-")[:limit].strip("-")


def base_name(app: str, username: str | None = None) -> str:
    """username: None for the owner."""
    name = label(app) or "app"
    if username:
        name = f"{name}-{label(username, 20) or 'user'}"
    return name


def usable(name: str, reserved: set[str] = frozenset()) -> bool:
    return bool(LABEL.match(name)) and name not in RESERVED | set(reserved)


async def pick(
    base: str,
    taken: Callable[[str], Awaitable[bool]],
    reserved: set[str] = frozenset(),
) -> str:
    """The base name if it is free, else base-<two random characters>."""
    if usable(base, reserved) and not await taken(base):
        return base
    for _ in range(TRIES):
        name = f"{base[:BASE_CHARS]}-{''.join(secrets.choice(SUFFIX_CHARS) for _ in range(2))}"
        if usable(name, reserved) and not await taken(name):
            return name
    raise NoFreeName(f"Couldn't find a free name for {base}.")
