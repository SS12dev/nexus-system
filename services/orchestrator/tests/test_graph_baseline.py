import argparse
from collections import Counter

from nexus_orchestrator import graph_baseline as gb
from nexus_orchestrator.graph_bakeoff import Tally, score_chat


class Fake:
    """Answers the entity pass, then the relation pass, like a model would."""

    def __init__(self, entities, relations):
        self.entities, self.relations = entities, relations
        self.cost, self.tokens = 0.5, Counter({"in": 100, "out": 20})

    async def ask(self, system, prompt, schema):
        if "ENTITIES:" in prompt:
            return {"relations": self.relations}
        return {"entities": self.entities}


def ent(name, kind="project"):
    return {"name": name, "type": kind, "domain": "work", "summary": "", "items": [0]}


def rel(a, p, b):
    return {"from": a, "predicate": p, "to": b, "label": "", "weight": 0.9, "ended": False}


async def test_the_real_two_passes_are_scored_and_described():
    ref = Fake(
        [ent("Nexus"), ent("Postgres", "tool")],
        [rel("USER", "builds", "Nexus"), rel("Nexus", "uses", "Postgres")],
    )
    small = Fake([ent("Nexus"), ent("Postgres", "tool")], [rel("USER", "builds", "Nexus")])
    r0, d0 = await gb.run_one(ref, "I'm building Nexus on Postgres")
    r1, _ = await gb.run_one(small, "I'm building Nexus on Postgres")
    assert len(d0["relations"]) == 2
    s = gb.Shape()
    s.add(r1)
    assert (s.entities, s.relations, s.linked, s.to_user, s.between) == (2, 1, 1, 1, 0)
    t = Tally()
    t.add(score_chat(r0, r1, {}), r1)
    assert t.entities.f1 == 1.0
    assert t.relations.recall == 0.5
    args = argparse.Namespace(reference="ref", samples=1)
    text = gb.report(
        args,
        [("Chat", "text")],
        900,
        {"ref": ref, "small": small},
        {"ref": gb.Shape(), "small": s},
        {"small": t},
        {"ref": [d0], "small": [{}]},
    )
    assert "about **$450.00** for all 900" in text
    assert "| small |" in text
