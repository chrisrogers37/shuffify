"""The worker's memory scheduler admits a durable routine at its due minute.

The real API creates the definition, PostgreSQL notifies the worker, and the
worker refreshes the stored login credentials before changing the local fake.
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
        operation = alice.ok("get", f"/operations/{history[0]['id']}")["operation"]
        assert operation["kind"] == "schedule_run" and operation["state"] == "succeeded"
        assert operation["result"]["execution_id"] == history[0]["id"]
        assert operation["result"]["triggered_by"] == "scheduler"
    finally:
        # The committed deletion notifies the worker before the next harness reset.
        alice.ok("delete", f"/schedules/{schedule['id']}")

    after = fake.uris(LONG_HAUL)
    assert sorted(after) == sorted(before) and after != before
