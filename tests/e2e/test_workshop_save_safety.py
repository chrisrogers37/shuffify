"""Workshop saves preserve intent across stale drafts and uncertain writes.

These tests observe the isolated fake's playlist, not just the response body.
Run only through scripts/harness.sh e2e.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from tests.e2e.conftest import hold_playlist_lock, pid

MORNING_MIX = pid("alice-morning-mix")
LONG_HAUL = pid("alice-long-haul")


def _commit(alice, playlist_id, baseline, desired):
    return alice.post(f"/workshop/{playlist_id}/commit", json={
        "baseline_track_uris": baseline, "track_uris": desired,
    })


def test_stale_draft_retained_then_manual_new_baseline(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    # Warm cached playlist reads, then save a different revision.
    alice.ok("get", f"/playlist/{MORNING_MIX}")
    external = baseline[1:] + baseline[:1]
    assert _commit(alice, MORNING_MIX, baseline, external).status_code == 200
    before_reconcile = fake.playlist(MORNING_MIX)
    draft = list(reversed(baseline))

    response = _commit(alice, MORNING_MIX, baseline, draft)
    assert response.status_code == 409, response.text
    body = response.json()
    assert (body["target_status"], body["error_code"], body["retryable"]) == (
        "not_written", "stale_draft", False,
    )
    assert body["current_track_uris"] == external
    assert "saved_track_uris" not in body
    assert fake.playlist(MORNING_MIX) == before_reconcile

    fresh = alice.ok("get", f"/workshop/{MORNING_MIX}/current-state")
    assert fresh["current_track_uris"] == external and fresh["representable"]
    assert fresh["loaded_at"]
    assert fake.playlist(MORNING_MIX) == before_reconcile, "reconciliation wrote to Spotify"

    # A new edit from the explicitly adopted order uses the ordinary endpoint.
    desired = external[2:] + external[:2]
    saved = _commit(alice, MORNING_MIX, fresh["current_track_uris"], desired)
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved_track_uris"] == fake.uris(MORNING_MIX) == desired


def test_duplicate_empty_and_already_applied_orders(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    desired = [baseline[0], baseline[0], baseline[2]]
    saved = _commit(alice, MORNING_MIX, baseline, desired)
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved_track_uris"] == fake.uris(MORNING_MIX) == desired
    before_repeat = fake.playlist(MORNING_MIX)
    repeated = _commit(alice, MORNING_MIX, baseline, desired)
    assert repeated.json()["target_status"] == "already_applied"
    assert fake.playlist(MORNING_MIX) == before_repeat
    empty = _commit(alice, MORNING_MIX, desired, [])
    assert empty.status_code == 200, empty.text
    assert empty.json()["saved_track_uris"] == fake.uris(MORNING_MIX) == []
    restored = _commit(alice, MORNING_MIX, [], desired)
    assert restored.status_code == 200, restored.text
    assert fake.uris(MORNING_MIX) == desired


def test_uncertain_first_write_is_not_replayed(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="server_error",
             playlist_id=MORNING_MIX, times=3)
    response = _commit(alice, MORNING_MIX, baseline, list(reversed(baseline)))
    assert response.status_code == 502, response.text
    body = response.json()
    assert (body["target_status"], body["error_code"], body["retryable"]) == (
        "unknown", "write_unverified", False,
    )
    assert fake.faults()[0]["fired"] == 1, "the uncertain PUT was retried"
    assert fake.uris(MORNING_MIX) == baseline


def test_later_batch_failure_leaves_truthful_partial_result(alice, fake):
    baseline = fake.uris(LONG_HAUL)
    desired = list(reversed(baseline))
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=LONG_HAUL)
    response = _commit(alice, LONG_HAUL, baseline, desired)
    assert response.status_code == 502, response.text
    body = response.json()
    assert (body["target_status"], body["error_code"], body["retryable"]) == (
        "partial", "partial_write", False,
    )
    assert (body["confirmed_batches"], body["total_batches"]) == (1, 3)
    assert "saved_track_uris" not in body
    assert fake.uris(LONG_HAUL) == desired[:100]
    fresh = alice.ok("get", f"/workshop/{LONG_HAUL}/current-state")
    assert fresh["current_track_uris"] == desired[:100]


def test_lost_response_is_reconciled_without_replaying_write(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    desired = list(reversed(baseline))
    # The real HTTP client's timeout is 30 seconds. The fake applies the write
    # after that timeout, modeling an operation that survives its response.
    fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="slow",
             playlist_id=MORNING_MIX, delay=31)
    response = _commit(alice, MORNING_MIX, baseline, desired)
    assert response.status_code == 502, response.text
    assert response.json()["target_status"] == "unknown"
    assert response.json()["retryable"] is False
    deadline = time.monotonic() + 5
    while fake.uris(MORNING_MIX) != desired and time.monotonic() < deadline:
        time.sleep(0.1)
    assert fake.uris(MORNING_MIX) == desired
    before_reconcile = fake.playlist(MORNING_MIX)
    fresh = alice.ok("get", f"/workshop/{MORNING_MIX}/current-state")
    assert fresh["current_track_uris"] == desired
    assert fake.playlist(MORNING_MIX) == before_reconcile
    assert fake.faults()[0]["fired"] == 1


def test_executor_lock_bounds_commit_and_current_state(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    # Hold exactly the advisory key acquired by scheduled executors.
    with hold_playlist_lock(MORNING_MIX), ThreadPoolExecutor(max_workers=1) as pool:
        for request in (
            lambda: _commit(alice, MORNING_MIX, baseline, list(reversed(baseline))),
            lambda: alice.get(f"/workshop/{MORNING_MIX}/current-state"),
        ):
            started = time.monotonic()
            response = pool.submit(request).result(timeout=8)
            assert response.status_code == 409, response.text
            assert response.json()["error_code"] == "operation_in_progress"
            assert time.monotonic() - started < 6
            assert fake.uris(MORNING_MIX) == baseline
    # Releasing the lock lets the save through.
    assert _commit(alice, MORNING_MIX, baseline, list(reversed(baseline))).status_code == 200
