"""Raid: watch another user's playlist, raid it into a staging playlist,
review what came in, keep some, dismiss one, and raid again.

Bob's Finds is public, and Alice follows it but cannot edit it. Spotify
withholds its items from her, so the raid reads its public page. The
staging playlist is created with POST /me/playlists.

The Workshop page keeps promotions in the browser until a commit, then
finalizes them, so the test does the same: commit, then finalize.
"""

from tests.e2e.conftest import pid

MORNING_MIX = pid("alice-morning-mix")
BOB_FINDS = pid("bob-finds")


def test_raid_review_keep_and_dismiss(alice, fake):
    before, source = fake.uris(MORNING_MIX), fake.uris(BOB_FINDS)
    link = alice.write(
        f"/playlist/{MORNING_MIX}/raid-link",
        json={"create_new": True, "target_playlist_name": "Morning Mix"},
    )["raid_link"]
    staging = fake.playlist(link["raid_playlist_id"])
    assert (staging["name"], staging["owner"], staging["public"], staging["uris"]) == (
        "Morning Mix [Raids]", "alice", False, []
    )

    watched = alice.ok(
        "post", f"/playlist/{MORNING_MIX}/raid-watch",
        json={"source_playlist_id": BOB_FINDS, "source_playlist_name": "Bob's Finds", "auto_schedule": False},
    )["source"]
    raided = alice.write(f"/playlist/{MORNING_MIX}/raid-now", json={})
    assert raided["tracks_added"] == 5  # the default per-source raid count

    pending = alice.ok("get", f"/playlist/{MORNING_MIX}/pending-raids")["tracks"]
    raided_uris = {t["track_uri"] for t in pending}
    assert len(pending) == 5 and all(t["track_name"] and t["track_artists"] for t in pending)
    assert raided_uris <= set(source) and not raided_uris & set(before)
    assert set(fake.uris(link["raid_playlist_id"])) == raided_uris
    assert fake.uris(MORNING_MIX) == before, "a raid stages tracks; it does not touch the target"

    kept, dismissed, left = pending[:2], pending[2:3], pending[3:]
    staged = before + [t["track_uri"] for t in kept]
    alice.write(f"/workshop/{MORNING_MIX}/commit", json={"baseline_track_uris": before, "track_uris": staged})
    alice.write(f"/playlist/{MORNING_MIX}/pending-raids/finalize", json={"track_ids": [t["id"] for t in kept],
              "source_playlist_id": link["raid_playlist_id"], "target_track_uris": staged})
    alice.ok("post", f"/playlist/{MORNING_MIX}/pending-raids/dismiss",
             json={"track_ids": [t["id"] for t in dismissed]})

    assert fake.uris(MORNING_MIX) == staged
    assert set(fake.uris(link["raid_playlist_id"])) == {t["track_uri"] for t in left}
    assert {t["id"] for t in alice.ok("get", f"/playlist/{MORNING_MIX}/pending-raids")["tracks"]} == {
        t["id"] for t in left
    }
    assert fake.uris(BOB_FINDS) == source, "raiding must not touch the source"

    # A second raid, for as many as the source has, takes exactly the tracks
    # not yet seen: none already in the target, staged, or dismissed.
    alice.ok("put", f"/playlist/{MORNING_MIX}/raid-source-count",
             json={"source_id": watched["id"], "raid_count": 100})
    unseen = set(source) - raided_uris
    assert len(unseen) <= 100, "the source has more unseen tracks than one raid can take"
    assert alice.write(f"/playlist/{MORNING_MIX}/raid-now", json={})["tracks_added"] == len(unseen)
    pending_now = {t["track_uri"] for t in alice.ok("get", f"/playlist/{MORNING_MIX}/pending-raids")["tracks"]}
    assert pending_now == unseen | {t["track_uri"] for t in left}
