"""A scheduled job fires on its own: the harness app's scheduler, against the fake.

The schedule is created through the app, as the Schedules page does, with a
cron expression for every minute, and nothing calls /run: the scheduler has
to pick it up from its PostgreSQL job store, refresh Alice's token from the
refresh token stored for her at login, and shuffle the playlist. The fake's
playlist is the evidence. The test deletes the schedule through the app
when it is done.
"""

import time

from tests.e2e.conftest import pid

LONG_HAUL = pid("alice-long-haul")
# A minute for the next cron tick, and time for the job to finish after it.
FIRE_WITHIN = 120


def test_a_scheduled_shuffle_fires_and_reorders_the_playlist(alice, fake):
    before = fake.uris(LONG_HAUL)
    schedule = alice.ok(
        "post", "/schedules/create",
        json={
            "job_type": "shuffle",
            "target_playlist_id": LONG_HAUL,
            "target_playlist_name": "Long Haul",
            "schedule_type": "cron",
            "schedule_value": "* * * * *",
            "algorithm_name": "BasicShuffle",
        },
    )["schedule"]
    try:
        deadline = time.monotonic() + FIRE_WITHIN
        history = []
        while not (history and history[0]["completed_at"]) and time.monotonic() < deadline:
            time.sleep(2)
            history = alice.ok("get", f"/schedules/{schedule['id']}/history")["history"]
        assert history and history[0]["completed_at"], f"the schedule did not fire and finish within {FIRE_WITHIN}s"
        assert history[0]["status"] == "success", history[0]
    finally:
        # Through the app, so the scheduler drops the job before the reset
        # empties its job store.
        alice.ok("delete", f"/schedules/{schedule['id']}")

    after = fake.uris(LONG_HAUL)
    assert sorted(after) == sorted(before) and after != before
