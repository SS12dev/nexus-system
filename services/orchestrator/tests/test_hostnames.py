"""App hostnames: the owner's and other people's names, reserved names, collisions."""

import pytest

from jarvis_common import hostnames
from jarvis_common.hostnames import NoFreeName, base_name, pick, usable


def taken_from(names: set[str]):
    async def taken(name: str) -> bool:
        return name in names

    return taken


def test_owner_and_other_peoples_names():
    assert base_name("2048") == "2048"
    assert base_name("2048", "Priya") == "2048-priya"
    assert base_name("Snake: The Game!", "sam.k") == "snake-the-game-sam-k"
    assert base_name("") == "app"
    assert len(base_name("x" * 200, "y" * 50)) <= 61


def test_names_are_single_dns_labels():
    for good in ("2048", "a", "my-app-k7"):
        assert usable(good)
    for bad in ("", "-x", "x-", "a.b", "UPPER", "x" * 64, "two_words"):
        assert not usable(bad), bad


@pytest.mark.parametrize("name", ["jarvis", "www", "mail", "api", "admin", "traefik"])
async def test_reserved_names_get_a_suffix(name):
    picked = await pick(name, taken_from(set()))
    assert picked.startswith(f"{name}-") and len(picked) == len(name) + 3


async def test_configured_reserved_names_too():
    picked = await pick("photos", taken_from(set()), {"photos", "nas"})
    assert picked != "photos" and picked.startswith("photos-")
    assert await pick("music", taken_from(set()), {"photos"}) == "music"


async def test_a_taken_name_gets_a_short_random_suffix(monkeypatch):
    assert await pick("2048", taken_from(set())) == "2048"
    picks = iter("k7")
    monkeypatch.setattr(hostnames.secrets, "choice", lambda chars: next(picks))
    assert await pick("2048", taken_from({"2048"})) == "2048-k7"


async def test_gives_up_after_many_collisions(monkeypatch):
    monkeypatch.setattr(hostnames.secrets, "choice", lambda chars: "a")
    with pytest.raises(NoFreeName):
        await pick("2048", taken_from({"2048", "2048-aa"}))
