"""Workshop saves preserve intent across stale drafts and uncertain writes.

These tests observe the isolated fake's playlist, not just the response body.
Run only through scripts/harness.sh e2e.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from tests.e2e.conftest import hold_playlist_lock, pid
from tests.paths import api_path

MORNING_MIX = pid("alice-morning-mix")
LONG_HAUL = pid("alice-long-haul")


def _commit(alice, playlist_id, baseline, desired):
    return alice.queued(f"/workshop/{playlist_id}/commit", json={
        "baseline_track_uris": baseline, "track_uris": desired,
    })


def test_stale_draft_retained_then_manual_new_baseline(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    # Warm cached playlist reads, then save a different revision.
    cached = alice.ok("get", f"/workshop/{MORNING_MIX}")
    assert [track["uri"] for track in cached["tracks"]] == baseline
    external = baseline[1:] + baseline[:1]
    assert _commit(alice, MORNING_MIX, baseline, external)["state"] == "succeeded"
    before_reconcile = fake.playlist(MORNING_MIX)
    draft = list(reversed(baseline))

    response = _commit(alice, MORNING_MIX, baseline, draft)
    assert response["state"] == "failed", response
    body = response["result"]
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
    assert saved["state"] == "succeeded", saved
    assert saved["result"]["saved_track_uris"] == fake.uris(MORNING_MIX) == desired


def test_duplicate_empty_and_already_applied_orders(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    desired = [baseline[0], baseline[0], baseline[2]]
    saved = _commit(alice, MORNING_MIX, baseline, desired)
    assert saved["state"] == "succeeded", saved
    assert saved["result"]["saved_track_uris"] == fake.uris(MORNING_MIX) == desired
    before_repeat = fake.playlist(MORNING_MIX)
    repeated = _commit(alice, MORNING_MIX, baseline, desired)
    assert repeated["result"]["target_status"] == "already_applied"
    assert fake.playlist(MORNING_MIX) == before_repeat
    empty = _commit(alice, MORNING_MIX, desired, [])
    assert empty["state"] == "succeeded", empty
    assert empty["result"]["saved_track_uris"] == fake.uris(MORNING_MIX) == []
    restored = _commit(alice, MORNING_MIX, [], desired)
    assert restored["state"] == "succeeded", restored
    assert fake.uris(MORNING_MIX) == desired


def test_uncertain_first_write_is_not_replayed(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="server_error",
             playlist_id=MORNING_MIX, times=3)
    response = _commit(alice, MORNING_MIX, baseline, list(reversed(baseline)))
    assert response["state"] == "needs_attention", response
    body = response["result"]
    assert (body["target_status"], body["error_code"], body["retryable"]) == (
        "unknown", "needs_attention", False,
    )
    assert fake.faults()[0]["fired"] == 1, "the uncertain PUT was retried"
    assert fake.uris(MORNING_MIX) == baseline


def test_later_batch_rate_limit_resumes_the_same_operation(alice, fake):
    baseline = fake.uris(LONG_HAUL)
    desired = list(reversed(baseline))
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="rate_limit", retry_after=1,
             playlist_id=LONG_HAUL)
    operation = _commit(alice, LONG_HAUL, baseline, desired)
    assert operation["state"] == "succeeded", operation
    body = operation["result"]
    assert body["saved_track_uris"] == fake.uris(LONG_HAUL) == desired
    assert (body["confirmed_batches"], body["total_batches"]) == (3, 3)
    assert fake.faults()[0]["fired"] == 1
    fresh = alice.ok("get", f"/workshop/{LONG_HAUL}/current-state")
    assert fresh["current_track_uris"] == desired


def test_lost_response_is_reconciled_without_replaying_write(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    desired = list(reversed(baseline))
    # The real HTTP client's timeout is 30 seconds. The fake applies the write
    # after that timeout, modeling an operation that survives its response.
    fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="slow",
             playlist_id=MORNING_MIX, delay=31)
    response = _commit(alice, MORNING_MIX, baseline, desired)
    assert response["state"] == "needs_attention", response
    assert response["result"]["target_status"] == "unknown"
    assert response["result"]["retryable"] is False
    deadline = time.monotonic() + 5
    while fake.uris(MORNING_MIX) != desired and time.monotonic() < deadline:
        time.sleep(0.1)
    assert fake.uris(MORNING_MIX) == desired
    before_reconcile = fake.playlist(MORNING_MIX)
    fresh = alice.ok("get", f"/workshop/{MORNING_MIX}/current-state")
    assert fresh["current_track_uris"] == desired
    assert fake.playlist(MORNING_MIX) == before_reconcile
    assert fake.faults()[0]["fired"] == 1


def test_executor_lock_defers_accepted_commit_and_bounds_current_state(alice, fake):
    baseline = fake.uris(MORNING_MIX)
    desired = list(reversed(baseline))
    path = api_path(f"/workshop/{MORNING_MIX}/commit")
    with hold_playlist_lock(MORNING_MIX), ThreadPoolExecutor(max_workers=1) as pool:
        accepted = alice.post(path, json={"baseline_track_uris": baseline, "track_uris": desired})
        assert accepted.status_code == 202, accepted.text
        operation_id = accepted.json()["operation_id"]
        started = time.monotonic()
        response = pool.submit(alice.get, api_path(f"/workshop/{MORNING_MIX}/current-state")).result(timeout=8)
        assert response.status_code == 409, response.text
        assert response.json()["error_code"] == "operation_in_progress"
        assert time.monotonic() - started < 6
        assert fake.uris(MORNING_MIX) == baseline
        busy = alice.post(path, json={"baseline_track_uris": baseline, "track_uris": desired})
        assert busy.status_code == 409 and busy.json()["operation_id"] == operation_id
    completed = alice.wait_operation(accepted.headers["Location"], operation_id)
    assert completed["state"] == "succeeded", completed
    assert fake.uris(MORNING_MIX) == desired
