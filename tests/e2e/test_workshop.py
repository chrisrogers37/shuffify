"""The Playlist Workshop: stage track operations and a merge, commit, and
find exactly the staged order in the fake.

The Workshop page does its edits in the browser and sends the result to
/workshop/<id>/commit, so these tests stage the same way: read the tracks,
search and load other playlists through the app's own routes, build the new
order, commit it.
"""

from tests.e2e.conftest import pid

MORNING_MIX = pid("alice-morning-mix")
ON_REPEAT = pid("alice-on-repeat")


def _open_in_workshop(alice, playlist_id):
    """Open the playlist's Workshop page, then read the order it starts from
    from the app's playlist API."""
    resp = alice.get(f"/workshop/{playlist_id}")
    assert resp.status_code == 200, resp.status_code
    return [t["uri"] for t in alice.ok("get", f"/playlist/{playlist_id}")["tracks"]]


def test_track_operations_commit_exactly_the_staged_order(alice, fake):
    current = _open_in_workshop(alice, MORNING_MIX)
    assert current == fake.uris(MORNING_MIX)

    found = alice.ok("post", "/workshop/search", json={"query": "Velvet", "limit": 10})["tracks"]
    addition = next(t["uri"] for t in found if t["uri"] not in current)

    staged = current[1:]  # remove the first track
    staged.insert(0, staged.pop(10))  # move one to the top
    staged.append(addition)  # add a search result at the end

    body = alice.write(f"/workshop/{MORNING_MIX}/commit",
                    json={"baseline_track_uris": current, "track_uris": staged})
    assert fake.uris(MORNING_MIX) == staged
    assert body["playlist_state"]["current_index"] >= 1

    # The commit is undoable like a shuffle.
    alice.write(f"/undo/{MORNING_MIX}")
    assert fake.uris(MORNING_MIX) == current


def test_merging_another_of_her_playlists_in(alice, fake):
    current = _open_in_workshop(alice, MORNING_MIX)
    loaded = alice.ok(
        "post", "/workshop/load-external-playlist",
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
        "post", "/workshop/load-external-playlist",
        json={"url": f"https://open.spotify.com/playlist/{pid('bob-finds')}"},
    )
    assert loaded["mode"] == "restricted" and loaded["tracks"] == []
    assert loaded["playlist"]["name"] == loaded["suggested_search"] == "Bob's Finds"

    # Recently Loaded lists it, without a count Spotify never gave.
    page = alice.get(f"/workshop/{MORNING_MIX}").text
    assert "Bob&#39;s Finds" in page and "None tracks" not in page


def test_a_playlist_the_workshop_cannot_open_keeps_her_logged_in(alice, fake):
    """Someone else's playlist (Spotify refuses its items) and one that does
    not exist each send her back to the Workshop hub with a message that
    fits; neither logs her out."""
    for playlist_id, message in (
        (pid("bob-finds"), "You can only open your own or collaborative playlists in the Workshop."),
        ("0" * 22, "exist or was deleted."),
    ):
        resp = alice.get(f"/workshop/{playlist_id}", allow_redirects=False)
        assert resp.status_code == 302, f"{playlist_id}: {resp.status_code}"
        assert resp.headers["Location"].endswith("/workshop"), resp.headers["Location"]
        assert message in alice.get("/workshop").text
        # Still logged in: her own playlist opens, with no detour to the login page.
        assert alice.get(f"/workshop/{MORNING_MIX}", allow_redirects=False).status_code == 200
