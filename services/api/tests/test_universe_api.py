async def test_universe_lists_areas_and_stars_and_keeps_the_core_unnamed(owner):
    data = (await owner.get("/api/memories/universe")).json()
    assert set(data) == {"core", "self_id", "areas", "stars", "lanes", "filaments", "roots"}
    assert data["core"]["neurons"] == [] and data["areas"] == []
