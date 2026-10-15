"""Login, every shuffle algorithm, and undo, checked in the fake's own state."""

import pytest

from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from tests.e2e.conftest import pid
from tests.paths import api_path

LONG_HAUL = pid("alice-long-haul")  # 250 tracks: three pages to read, three batches to write


def test_login_lists_the_playlists_alice_can_edit(alice, fake):
    names = {p["name"] for p in alice.ok("get", "/api/user-playlists")["playlists"]}
    # Owned and collaborative; Bob's Finds, which she only follows, is not editable.
    assert names == {p["name"] for p in fake.playlists()
                     if p["owner"] == "alice" or (p["collaborative"] and "alice" in p["collaborators"])}
    assert "Road Trip" in names and "Bob's Finds" not in names


@pytest.mark.parametrize("algorithm", sorted(ShuffleRegistry.get_available_algorithms()))
def test_each_algorithm_reorders_the_playlist_and_undo_restores_it(alice, fake, algorithm):
    before = fake.uris(LONG_HAUL)

    body = alice.ok("post", f"/shuffle/{LONG_HAUL}", data={"algorithm": algorithm})
    after = fake.uris(LONG_HAUL)
    assert sorted(after) == sorted(before), "a shuffle added or lost tracks"
    assert after != before, f"{algorithm} reported {body.get('message')!r} but left the order as it was"
    assert [t["uri"] for t in body["playlist"]["tracks"]] == after

    alice.ok("post", f"/undo/{LONG_HAUL}")
    assert fake.uris(LONG_HAUL) == before


def test_undo_walks_back_through_several_shuffles(alice, fake):
    orders = [fake.uris(LONG_HAUL)]
    for algorithm in ("BasicShuffle", "ArtistSpacingShuffle"):
        alice.ok("post", f"/shuffle/{LONG_HAUL}", data={"algorithm": algorithm})
        orders.append(fake.uris(LONG_HAUL))
    assert len({tuple(o) for o in orders}) == 3

    for expected in reversed(orders[:-1]):
        alice.ok("post", f"/undo/{LONG_HAUL}")
        assert fake.uris(LONG_HAUL) == expected


def test_a_playlist_alice_cannot_edit_is_refused_and_left_alone(alice, fake):
    bobs = pid("bob-finds")
    before = fake.uris(bobs)
    resp = alice.post(api_path(f"/shuffle/{bobs}"), data={"algorithm": "BasicShuffle"})
    assert resp.status_code == 403 and resp.json().get("success") is False
    assert fake.uris(bobs) == before
