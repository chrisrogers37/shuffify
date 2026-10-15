"""A scheduled job when Spotify is busy or out of quota, against the fake.

The fake answers as Spotify documents for a rate limit and for a spent quota
(harness/fake_spotify/faults.py); the app's client and job runner have to do
the rest. Each test runs the schedule through the app (/schedules/<id>/run,
as the Schedules page's Run now does) and checks Spotify's side in the
fake's playlist.
"""

import time

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from config import HarnessConfig
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

    alice.write(f"/schedules/{schedule_id}/run")

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

    operation = alice.queued(f"/schedules/{schedule_id}/run")
    assert operation["state"] == "rolled_back", operation
    outcome = operation["result"]
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


def test_a_late_write_requires_review_without_guessing_a_restore(alice, fake):
    """A timed-out write can still land; no settled outcome authorizes a guessed rollback."""
    before = fake.uris(LONG_HAUL)
    schedule_id = _daily_shuffle(alice)
    fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="slow",
             playlist_id=LONG_HAUL, delay=33)
    operation = alice.queued(f"/schedules/{schedule_id}/run")
    assert operation["state"] == "needs_attention", operation
    effect = operation["result"]["operation"]["resources"][0]
    assert effect["status"] == "unknown"
    assert effect["affected_count"] is None
    assert "current_track_uris" not in effect
    expected_prefix = effect["desired_track_uris"][:100]
    deadline = time.monotonic() + 15
    while fake.uris(LONG_HAUL) != expected_prefix and time.monotonic() < deadline:
        time.sleep(.05)
    assert fake.uris(LONG_HAUL) == expected_prefix
    assert fake.uris(LONG_HAUL) != before
    assert _last_run(alice, schedule_id)["status"] == "failed"
    HarnessConfig.assert_safe()
    engine = create_engine(HarnessConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            journal = connection.execute(text("SELECT journal FROM job_executions WHERE id = :id"),
                                         {"id": operation["id"]}).scalar_one()
        assert [batch["attempts"] for plan in journal["plans"] for batch in plan["batches"]] == [1, 0, 0]
        assert journal["compensation"] == []
    finally:
        engine.dispose()
