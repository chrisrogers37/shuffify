"""Workshop context, exact queued orders, merge reads and session-safe refusals."""

from tests.e2e.conftest import pid
from tests.paths import api_path

MORNING_MIX = pid("alice-morning-mix")
ON_REPEAT = pid("alice-on-repeat")


def _open_in_workshop(alice, playlist_id):
    context = alice.ok("get", f"/workshop/{playlist_id}")
    assert context["playlist"]["id"] == playlist_id
    return [t["uri"] for t in context["tracks"]]


def test_track_operations_commit_exactly_the_staged_order(alice, fake):
    current = _open_in_workshop(alice, MORNING_MIX)
    assert current == fake.uris(MORNING_MIX)

    found = alice.ok("post", "/workshop/search", json={"query": "Velvet", "limit": 10})["tracks"]
    addition = next(t["uri"] for t in found if t["uri"] not in current)

    staged = current[1:]  # remove the first track
    staged.insert(0, staged.pop(10))  # move one to the top
    staged.append(addition)  # add a search result at the end

    body = alice.write(f"/workshop/{MORNING_MIX}/commit", json={"baseline_track_uris": current, "track_uris": staged})
    assert fake.uris(MORNING_MIX) == staged
    assert body["playlist_state"]["current_index"] >= 1

    # The commit is undoable like a shuffle.
    alice.write(f"/undo/{MORNING_MIX}")
    assert fake.uris(MORNING_MIX) == current


def test_merging_another_of_her_playlists_in(alice, fake):
    current = _open_in_workshop(alice, MORNING_MIX)
    loaded = alice.ok(
        "post",
        "/workshop/load-external-playlist",
        json={"url": f"https://open.spotify.com/playlist/{ON_REPEAT}"},
    )
    assert loaded["mode"] == "tracks"
    merged_in = [t["uri"] for t in loaded["tracks"]]
    assert merged_in == fake.uris(ON_REPEAT)

    # Merge as the Workshop does: new tracks appended, duplicates kept once.
    staged = current + [u for u in dict.fromkeys(merged_in) if u not in current]
    alice.write(f"/workshop/{MORNING_MIX}/commit", json={"baseline_track_uris": current, "track_uris": staged})
    assert fake.uris(MORNING_MIX) == staged
    assert fake.uris(ON_REPEAT) == merged_in, "merging must not touch the source"


def test_someone_elses_playlist_opens_in_restricted_mode(alice, fake):
    """Spotify returns another user's playlist without its items or their
    count, so the Workshop offers a search for its tracks."""
    loaded = alice.ok(
        "post",
        "/workshop/load-external-playlist",
        json={"url": f"https://open.spotify.com/playlist/{pid('bob-finds')}"},
    )
    assert loaded["mode"] == "restricted" and loaded["tracks"] == []
    assert loaded["playlist"]["name"] == loaded["suggested_search"] == "Bob's Finds"

    recent = alice.ok("get", f"/workshop/{MORNING_MIX}")["recently_loaded"]
    row = next(row for row in recent if row["id"] == pid("bob-finds"))
    assert row["name"] == "Bob's Finds" and row["track_count"] is None


def test_a_playlist_the_workshop_cannot_open_keeps_her_logged_in(alice, fake):
    for playlist_id in (pid("bob-finds"), "0" * 22):
        response = alice.get(api_path(f"/workshop/{playlist_id}"), allow_redirects=False)
        assert response.status_code == 404, response.text
        body = response.json()
        assert body["success"] is False and body["error_code"] == "not_found"
        assert body["message"]
        assert alice.ok("get", "/session")["signed_in"] is True
        assert _open_in_workshop(alice, MORNING_MIX) == fake.uris(MORNING_MIX)
