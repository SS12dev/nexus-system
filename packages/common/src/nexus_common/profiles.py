"""Profile packs: what Nexus sets up for someone from their answers at onboarding.

Each pack is a YAML file in library/profiles/ for one profession ("role") or interest: memories
to start with, prompts to suggest, and routines to offer. Adding a profession is a new file, not
code. Packs are read from disk when first needed and again whenever a file changes.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from nexus_common.models import MemoryKind
from nexus_common.routines import ScheduleError, rule_for, shift_time

log = logging.getLogger(__name__)

# Google services a routine may need; it is offered without them but says what it misses.
SERVICES = {"gmail", "calendar", "tasks", "contacts"}
DEFAULT_DAY_START = "08:00"


@dataclass(frozen=True)
class PackRoutine:
    id: str
    name: str
    prompt: str
    # "daily", "weekdays", ["SU"], "monthly:1" (see routines.rule_for).
    days: str | tuple[str, ...]
    # "HH:MM", "day_start" or "day_start+90" (minutes after the person's day starts).
    time: str
    needs: tuple[str, ...] = ()
    only_if_new: bool = False
    default_on: bool = False

    def schedule(self, day_start: str | None) -> str:
        time = self.time.strip()
        if time.startswith("day_start"):
            offset = time.removeprefix("day_start").strip() or "0"
            time = shift_time(day_start or DEFAULT_DAY_START, int(offset))
        days = list(self.days) if isinstance(self.days, tuple) else self.days
        return rule_for(days, time)


@dataclass(frozen=True)
class Pack:
    id: str
    label: str
    # "role" (a profession, step 2 of onboarding), "interest" (step 4) or "base" (everyone).
    kind: str
    blurb: str = ""
    # Role or interest ids that select this pack; a role pack matches its own id too.
    match: tuple[str, ...] = ()
    memories: tuple[tuple[MemoryKind, str], ...] = ()
    prompts: tuple[str, ...] = ()
    routines: tuple[PackRoutine, ...] = ()
    order: int = 100

    def matches(self, roles: set[str], interests: set[str]) -> bool:
        if self.kind == "base":
            return True
        wanted = {self.id, *self.match}
        return bool(wanted & (roles | interests))


@dataclass
class _Cache:
    stamp: tuple = ()
    packs: list[Pack] = field(default_factory=list)


_cache: dict[str, _Cache] = {}


def _routine(raw: dict) -> PackRoutine:
    days = raw.get("days", "daily")
    return PackRoutine(
        id=str(raw["id"]),
        name=str(raw["name"])[:80],
        prompt=" ".join(str(raw["prompt"]).split()),
        days=tuple(days) if isinstance(days, list) else str(days),
        time=str(raw.get("time", "day_start")),
        needs=tuple(n for n in raw.get("needs", []) if n in SERVICES),
        only_if_new=bool(raw.get("only_if_new", False)),
        default_on=bool(raw.get("default_on", False)),
    )


def _pack(raw: dict) -> Pack:
    memories = tuple(
        (MemoryKind(m.get("kind", "fact")), " ".join(str(m["content"]).split()))
        for m in raw.get("memories", [])
    )
    pack = Pack(
        id=str(raw["id"]),
        label=str(raw["label"]),
        kind=str(raw.get("kind", "interest")),
        blurb=str(raw.get("blurb", "")),
        match=tuple(str(x) for x in raw.get("match", [])),
        memories=memories,
        prompts=tuple(str(p) for p in raw.get("prompts", [])),
        routines=tuple(_routine(r) for r in raw.get("routines", [])),
        order=int(raw.get("order", 100)),
    )
    for r in pack.routines:
        r.schedule(None)  # raises ScheduleError for a bad days/time
    return pack


def load_packs(library_path: str | Path) -> list[Pack]:
    folder = Path(library_path) / "profiles"
    files = sorted(folder.glob("*.yaml")) if folder.is_dir() else []
    stamp = tuple((f.name, f.stat().st_mtime_ns) for f in files)
    cache = _cache.setdefault(str(folder), _Cache())
    if cache.stamp == stamp and cache.packs:
        return cache.packs
    packs = []
    for f in files:
        try:
            packs.append(_pack(yaml.safe_load(f.read_text()) or {}))
        except (KeyError, ValueError, TypeError, ScheduleError, yaml.YAMLError) as exc:
            log.error("Profile pack %s is invalid and was skipped: %s", f.name, exc)
    packs.sort(key=lambda p: (p.order, p.label))
    cache.stamp, cache.packs = stamp, packs
    return packs


def packs_for(packs: list[Pack], profile: dict) -> list[Pack]:
    roles = set(profile.get("roles") or [])
    interests = set(profile.get("interests") or [])
    return [p for p in packs if p.matches(roles, interests)]


def find_routine(packs: list[Pack], key: str) -> tuple[Pack, PackRoutine] | None:
    """A pack routine by "<pack id>/<routine id>"."""
    pack_id, _, routine_id = key.partition("/")
    for p in packs:
        if p.id == pack_id:
            for r in p.routines:
                if r.id == routine_id:
                    return p, r
    return None


def suggested_prompts(packs: list[Pack], profile: dict, limit: int = 4) -> list[str]:
    """A few prompts from the person's packs, the most specific (role) packs first."""
    chosen = packs_for(packs, profile)
    rank = {"role": 0, "interest": 1, "base": 2}
    chosen.sort(key=lambda p: rank.get(p.kind, 3))
    out: list[str] = []
    # One from each pack in turn, so a single pack doesn't take every slot.
    depth = max((len(p.prompts) for p in chosen), default=0)
    for i in range(depth):
        for p in chosen:
            if i < len(p.prompts) and p.prompts[i] not in out:
                out.append(p.prompts[i])
            if len(out) >= limit:
                return out
    return out


# What onboarding's "what should it help with" step offers. Packs whose id or match names one of
# these are chosen by it; the rest only shape the memory saying what they want help with.
INTERESTS: dict[str, str] = {
    "email": "Email",
    "calendar": "Calendar and planning",
    "writing": "Writing",
    "research": "Research",
    "learning": "Learning a subject",
    "reading": "Reading",
    "news": "News on my topics",
    "job_hunt": "Job hunting",
    "coding": "Side projects and coding",
    "fitness": "Health and fitness",
    "cooking": "Cooking",
    "money": "Money",
    "travel": "Travel",
    "family": "Family and home",
    "making": "3D printing and making",
}
