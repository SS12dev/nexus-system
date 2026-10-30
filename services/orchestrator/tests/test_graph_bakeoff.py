from nexus_orchestrator.graph_bakeoff import Result, Score, parse, score_chat, transcript


def test_parse_keeps_valid_parts_and_flags_bad_types():
    res = parse(
        '```json\n{"entities": [{"name": "UST", "type": "organisation"},'
        ' {"name": "tray.stl", "type": "document"}, {"name": "Orb", "type": "gizmo"}],'
        ' "relations": [{"from": "USER", "predicate": "works at", "to": "UST"}]}\n```'
    )
    assert [e["key"] for e in res.entities] == ["ust", "orb"]
    assert res.relations[0]["family"] == "belonging"
    assert res.valid is False  # "gizmo" is not a type


def test_parse_reports_broken_json():
    res = parse("not json")
    assert res.error and not res.valid


def test_score_matches_names_by_meaning_and_relations_by_family():
    ref = Result(
        entities=[{"key": "ust global", "type": "organisation"}, {"key": "react", "type": "tool"}],
        relations=[
            {"from": "user", "to": "ust global", "family": "belonging", "predicate": "works at"}
        ],
    )
    got = Result(
        entities=[{"key": "ust", "type": "organisation"}, {"key": "chess", "type": "interest"}],
        relations=[{"from": "user", "to": "ust", "family": "belonging", "predicate": "member of"}],
    )
    vectors = {
        "ust global": [1.0, 0.0],
        "ust": [0.99, 0.05],
        "react": [0.0, 1.0],
        "chess": [0.7, -0.7],
    }
    s = score_chat(ref, got, vectors)
    assert s["entities"] == (1, 2, 2)
    assert s["typed"] == 1
    assert s["relations"] == (1, 1, 1)
    assert s["predicate_same"] == 0


def test_score_f1_and_empty_cases():
    s = Score()
    assert s.precision == 1.0 and s.recall == 1.0
    s.add(2, 4, 2)
    assert round(s.f1, 2) == 0.67


def test_transcript_keeps_the_latest_part():
    text = transcript([("user", "a" * 50), ("assistant", "b" * 50)], limit=60)
    assert text.startswith("…") and text.endswith("b" * 50)


def test_a_broken_answer_scores_zero_even_when_the_reference_is_empty():
    s = score_chat(Result(), Result(valid=False, error="not JSON"), {})
    score = Score()
    score.add(*s["entities"])
    assert score.f1 == 0.0


def _perfect(case):
    from nexus_common.graph import norm

    ents = [
        {"name": e["name"], "key": norm(e["name"]), "type": e["types"][0]} for e in case.entities
    ]
    rels = [
        {
            "from": "user" if r["from"] == "USER" else norm(r["from"]),
            "to": "user" if r["to"] == "USER" else norm(r["to"]),
            "family": r["families"][0],
            "predicate": "",
        }
        for r in case.relations
    ]
    return Result(entities=ents, relations=rels)


def test_every_case_is_consistent_and_a_perfect_answer_scores_full_marks():
    from nexus_orchestrator.graph_bakeoff import ALL_TYPES, FAMILIES, score_gold
    from nexus_orchestrator.graph_cases import CASES

    assert len({c.id for c in CASES}) == len(CASES)
    for c in CASES:
        names = {e["name"] for e in c.entities}
        for e in c.entities:
            assert set(e["types"]) <= set(ALL_TYPES), (c.id, e)
        for r in c.relations:
            assert {r["from"], r["to"]} <= names | {"USER"}, (c.id, r)
            assert set(r["families"]) <= set(FAMILIES), (c.id, r)
        s = score_gold(c, _perfect(c), {})
        tp, got, ref = s["entities"]
        assert tp == got == ref, c.id
        assert s["relations"][0] == len(c.relations), c.id
        assert s["traps"] == 0, c.id


def test_a_trap_name_counts_as_a_hit():
    from nexus_orchestrator.graph_bakeoff import score_gold
    from nexus_orchestrator.graph_cases import CASES

    case = next(c for c in CASES if c.id == "edge-hypothetical")
    got = Result(entities=[{"name": "Berlin", "key": "berlin", "type": "place"}])
    assert score_gold(case, got, {})["traps"] == 1
