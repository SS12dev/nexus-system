"""What the knowledge graph is made of: the kinds of things in a user's world, which shell of
the orb each sits in, the life domains that split the shells into sectors, and the relation
predicates grouped into families (which the orb draws in different colours).

Shell 0 is the user. Shell 1 is who they are (traits, values, goals), shell 2 the people and
places in their life, shell 3 what they do (projects, apps, routines), shell 4 what they know
and have made (topics, tools, documents). The closer the shell, the closer it is to identity.

The built-in lists below are seeded into ontology_types and ontology_predicates (`seed`), where
self-review can propose more later. Code reads the lists here; rows added in the database are
merged in by `load`.
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import OntologyPredicate, OntologyType

SHELLS = {0: "you", 1: "personality", 2: "life", 3: "activity", 4: "knowledge"}

# type -> (shell, label). The first seven are the types the graph had before (graph.TYPES).
TYPES: dict[str, tuple[int, str]] = {
    "person": (2, "Person"),
    "project": (3, "Project"),
    "organisation": (2, "Organisation"),
    "place": (2, "Place"),
    "topic": (4, "Topic"),
    "tool": (4, "Tool"),
    "other": (4, "Other"),
    # Personality
    "trait": (1, "Trait"),
    "value": (1, "Value"),
    "belief": (1, "Belief"),
    "preference": (1, "Preference"),
    "goal": (1, "Goal"),
    "skill": (1, "Skill"),
    "interest": (1, "Interest"),
    "habit": (1, "Habit"),
    "state": (1, "State"),
    "communication_style": (1, "Communication style"),
    # Life
    "pet": (2, "Pet"),
    "group": (2, "Group"),
    "role": (2, "Role"),
    "life_event": (2, "Life event"),
    # Activity
    "app": (3, "App"),
    "routine": (3, "Routine"),
    "task": (3, "Task"),
    "decision": (3, "Decision"),
    # Knowledge
    "document": (4, "Document"),
    "source": (4, "Source"),
}

DOMAINS = ("work", "learning", "home", "health", "social", "hobbies", "finance")

# family -> predicates. Direction reads src -> dst ("USER works at UST").
FAMILIES: dict[str, tuple[str, ...]] = {
    "identity": (
        "has trait",
        "values",
        "believes",
        "prefers",
        "dislikes",
        "aims for",
        "good at",
        "interested in",
        "feels",
    ),
    "social": ("knows", "family of", "partner of", "friend of", "works with", "reports to"),
    "belonging": (
        "part of",
        "member of",
        "is a",
        "located in",
        "lives in",
        "works at",
        "studied at",
        "in domain",
    ),
    "activity": ("works on", "owns", "built", "uses", "runs", "produced", "plans", "left"),
    "causal": ("led to", "depends on", "blocked by", "motivated by", "before"),
    "knowledge": ("about", "learned from", "references", "explains"),
    "association": ("related to",),
}
FAMILY_OF = {p: f for f, ps in FAMILIES.items() for p in ps}
SYMMETRIC = frozenset({"knows", "family of", "partner of", "friend of", "works with", "related to"})
DEFAULT_PREDICATE = "related to"

# Wording the models use, mapped to a canonical predicate. Matched on the whole label first,
# then on the patterns below in order.
SYNONYMS = {
    "uses": "uses",
    "use": "uses",
    "is using": "uses",
    "used": "uses",
    "works on": "works on",
    "is working on": "works on",
    "is building": "works on",
    "building": "works on",
    "develops": "works on",
    "maintains": "works on",
    "built": "built",
    "made": "built",
    "created": "built",
    "wrote": "built",
    "works at": "works at",
    "works in": "works at",
    "employed at": "works at",
    "works for": "works at",
    "joined": "works at",
    "is joining": "works at",
    "left": "left",
    "quit": "left",
    "used to work at": "left",
    "lives in": "lives in",
    "moved to": "lives in",
    "based in": "lives in",
    "lives at": "lives in",
    "from": "located in",
    "located in": "located in",
    "is in": "located in",
    "studied at": "studied at",
    "graduated from": "studied at",
    "studies at": "studied at",
    "likes": "prefers",
    "loves": "prefers",
    "enjoys": "interested in",
    "prefers": "prefers",
    "wants": "aims for",
    "is planning": "plans",
    "plans": "plans",
    "plans to": "plans",
    "is learning": "learned from",
    "learns": "learned from",
    "is studying": "learned from",
    "hates": "dislikes",
    "dislikes": "dislikes",
    "avoids": "dislikes",
    "owns": "owns",
    "has": "owns",
    "bought": "owns",
    "is good at": "good at",
    "knows how to": "good at",
    "is interested in": "interested in",
    "interested in": "interested in",
    "values": "values",
    "cares about": "values",
    "believes": "believes",
    "thinks": "believes",
    "is": "is a",
    "is a": "is a",
    "is an": "is a",
    "member of": "member of",
    "is part of": "part of",
    "part of": "part of",
    "belongs to": "part of",
    "sister of": "family of",
    "brother of": "family of",
    "mother of": "family of",
    "father of": "family of",
    "cousin of": "family of",
    "married to": "partner of",
    "spouse of": "partner of",
    "dating": "partner of",
    "friend of": "friend of",
    "is friends with": "friend of",
    "colleague of": "works with",
    "works with": "works with",
    "pairs with": "works with",
    "manages": "reports to",
    "manager of": "reports to",
    "reports to": "reports to",
    "knows": "knows",
    "met": "knows",
    "depends on": "depends on",
    "requires": "depends on",
    "needs": "depends on",
    "blocked by": "blocked by",
    "is blocked by": "blocked by",
    "caused": "led to",
    "led to": "led to",
    "because of": "motivated by",
    "about": "about",
    "covers": "about",
    "references": "references",
    "runs": "runs",
    "schedules": "runs",
    "produced": "produced",
    "generated": "produced",
    "related to": "related to",
}
_PATTERNS = [
    (re.compile(r"\b(work|works|working|worked) (at|for)\b|\bemployed\b"), "works at"),
    (re.compile(r"\b(live|lives|living|lived|moved)\b"), "lives in"),
    (
        re.compile(
            r"\b(sister|brother|mother|father|mum|mom|dad|cousin|aunt|uncle|son|"
            r"daughter|parent|sibling)\b"
        ),
        "family of",
    ),
    (re.compile(r"\b(wife|husband|partner|girlfriend|boyfriend|spouse)\b"), "partner of"),
    (re.compile(r"\b(friend)\b"), "friend of"),
    (re.compile(r"\b(colleague|teammate|coworker)\b"), "works with"),
    (re.compile(r"\b(manager|boss|reports)\b"), "reports to"),
    (re.compile(r"\b(build|builds|building|develop|developing)\b"), "works on"),
    (re.compile(r"\b(use|uses|using)\b"), "uses"),
    (re.compile(r"\b(learn|learning|studying|reading)\b"), "learned from"),
    (re.compile(r"\b(like|likes|love|loves|prefer|prefers)\b"), "prefers"),
    (re.compile(r"\b(hate|hates|dislike|dislikes)\b"), "dislikes"),
    (re.compile(r"\b(plan|plans|planning|want|wants)\b"), "plans"),
    (re.compile(r"\b(own|owns|bought)\b"), "owns"),
    (re.compile(r"\b(member|joined)\b"), "member of"),
    (re.compile(r"\b(depend|depends|need|needs|require)\b"), "depends on"),
    (re.compile(r"\b(block|blocked)\b"), "blocked by"),
]


@dataclass(frozen=True)
class Canonical:
    predicate: str
    family: str
    label: str


def canonical(label: str) -> Canonical:
    """The canonical predicate and family for a model's wording; the wording is kept as the
    label so nothing it said is lost."""
    text = " ".join(str(label).casefold().split())[:80]
    pred = text if text in FAMILY_OF else SYNONYMS.get(text)
    if pred is None:
        pred = next((p for rx, p in _PATTERNS if rx.search(text)), DEFAULT_PREDICATE)
    return Canonical(pred, FAMILY_OF[pred], text or pred)


def shell_of(kind: str) -> int:
    return TYPES.get(kind, TYPES["other"])[0]


# What the orb and Knowledge page drew before shells existed: each type's nearest old type, so
# today's pages keep working while the graph grows new kinds.
LEGACY = {
    "person": "person",
    "pet": "person",
    "group": "person",
    "organisation": "organisation",
    "place": "place",
    "project": "project",
    "app": "project",
    "routine": "project",
    "task": "project",
    "decision": "project",
    "topic": "topic",
    "tool": "tool",
    **{t: "topic" for t, (shell, _l) in TYPES.items() if shell == 1},
}


def legacy_type(kind: str) -> str:
    return LEGACY.get(kind, "other")


async def seed(db: AsyncSession) -> None:
    """Make sure the built-in types and predicates are in the ontology tables."""
    await db.execute(
        insert(OntologyType)
        .values(
            [
                {"name": t, "shell": shell, "label": label, "builtin": True}
                for t, (shell, label) in TYPES.items()
            ]
        )
        .on_conflict_do_nothing(index_elements=["name"])
    )
    await db.execute(
        insert(OntologyPredicate)
        .values(
            [
                {"name": p, "family": f, "symmetric": p in SYMMETRIC, "builtin": True}
                for f, ps in FAMILIES.items()
                for p in ps
            ]
        )
        .on_conflict_do_nothing(index_elements=["name"])
    )


async def load(db: AsyncSession) -> None:
    """Merge types and predicates added in the database (by an approved proposal) into the
    lists above, so the rest of the code sees them."""
    for row in (await db.scalars(select(OntologyType))).all():
        TYPES.setdefault(row.name, (row.shell, row.label))
    for row in (await db.scalars(select(OntologyPredicate))).all():
        if row.name not in FAMILY_OF and row.family in FAMILIES:
            FAMILIES[row.family] = (*FAMILIES[row.family], row.name)
            FAMILY_OF[row.name] = row.family
