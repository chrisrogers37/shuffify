"""A scheduled job when Spotify is busy or out of quota, against the fake.

The fake answers as Spotify documents for a rate limit and for a spent quota
(harness/fake_spotify/faults.py); the app's client and job runner have to do
the rest. Each test runs the schedule through the app (/schedules/<id>/run,
as the Schedules page's Run now does) and checks Spotify's side in the
fake's playlist.
"""

import time

from tests.e2e.conftest import pid

LONG_HAUL = pid("alice-long-haul")  # 250 tracks: a PUT of 100, then two POSTs


def _daily_shuffle(alice) -> int:
    return alice.ok("post", "/schedules/create", json={
        "job_type": "shuffle", "target_playlist_id": LONG_HAUL, "target_playlist_name": "Long Haul",
        "schedule_value": "daily", "algorithm_name": "BasicShuffle",
    })["schedule"]["id"]


def _last_run(alice, schedule_id) -> dict:
    return alice.ok("get", f"/schedules/{schedule_id}/history")["history"][0]


def test_a_scheduled_shuffle_rides_out_a_rate_limit(alice, fake):
    before = fake.uris(LONG_HAUL)
    schedule_id = _daily_shuffle(alice)
    fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="rate_limit", retry_after=1,
             playlist_id=LONG_HAUL)

    alice.ok("post", f"/schedules/{schedule_id}/run")

    after = fake.uris(LONG_HAUL)
    assert sorted(after) == sorted(before) and after != before
    assert [f["fired"] for f in fake.faults()] == [1]
    assert _last_run(alice, schedule_id)["status"] == "success"


def test_a_scheduled_shuffle_refused_partway_is_rolled_back(alice, fake):
    """Spotify takes the first 100 tracks, then refuses the next batch for
    quota. The job must put the playlist back as it was, not leave it at 100."""
    before = fake.uris(LONG_HAUL)
    schedule_id = _daily_shuffle(alice)
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=LONG_HAUL)

    resp = alice.post(f"/schedules/{schedule_id}/run")

    assert resp.status_code == 502, resp.text
    outcome = resp.json()
    assert outcome["success"] is False
    assert outcome["operation"]["aggregate_status"] == "partial"
    assert outcome["operation"]["retryable"] is False
    effect = outcome["operation"]["resources"][0]
    assert effect["status"] == "restored"
    assert effect["current_track_uris"] == before
    assert outcome["result"]["execution_id"] == _last_run(alice, schedule_id)["id"]
    assert fake.uris(LONG_HAUL) == before, "the half-written playlist was not restored"
    assert [f["fired"] for f in fake.faults()] == [1]
    assert _last_run(alice, schedule_id)["status"] == "failed_rolled_back"


def test_a_write_that_lands_after_the_rollback_is_restored_again(alice, fake):
    """Spotify holds the first batch past the client's 30-second timeout and
    applies it after the rollback found the playlist unchanged. The
    rollback must notice the late write and put the playlist back."""
    before = fake.uris(LONG_HAUL)
    schedule_id = _daily_shuffle(alice)
    fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="slow",
             playlist_id=LONG_HAUL, delay=33)
    sent = time.monotonic()

    resp = alice.post(f"/schedules/{schedule_id}/run")

    # Judge the playlist only after the held write has landed: 33 s after the PUT goes out, which is after `sent`.
    time.sleep(max(0.0, sent + 40 - time.monotonic()))
    assert resp.status_code == 502, resp.text
    effect = resp.json()["operation"]["resources"][0]
    assert effect["status"] == "restored"
    assert effect["current_track_uris"] == before
    assert fake.uris(LONG_HAUL) == before, "the late write was left in place"
    assert _last_run(alice, schedule_id)["status"] == "failed_rolled_back"
