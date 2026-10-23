"""Spotify reads can lag a write the run already knows succeeded; that is never an external edit."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from shuffify.services.operations.journal import OperationDeferred, ReadRetryExhausted
from shuffify.spotify.exceptions import SpotifyWriteRejectedError
from tests.services.operations.test_crash_recovery import Crash, World, uri

BASELINE = [uri(index) for index in range(300)]
DESIRED = [uri(1000 + index) for index in range(300)]


def lagging(tracks, mode="snapshot", reads=3):
    world = World(tracks)
    world.lag_mode, world.lag_reads = mode, reads
    return world


def make_due(world):
    world.saved["read_retry"]["next_attempt_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def tracks(world):
    return world.playlists["target"]["track_uris"]


def external_edit_after_first_write(world, journal):
    original = world.dispatch

    def dispatch(method, path, body):
        result = original(method, path, body)
        if len(world.writes) == 1:
            state = world.playlists["target"]
            state["track_uris"] = state["track_uris"] + [uri(9999)]
            state["snapshot_id"] = "someone-else"
        return result

    journal.dispatch = dispatch


@pytest.mark.parametrize("reads", [1, 3, 8])
def test_multi_batch_replace_completes_under_snapshot_lag(reads):
    world = lagging(BASELINE, reads=reads)
    journal = world.journal()
    journal.freeze_replace("target", DESIRED)
    assert journal.resume()["state"] == "succeeded"
    assert tracks(world) == DESIRED
    assert [len(write[2]["uris"]) for write in world.writes] == [100, 100, 100]


@pytest.mark.parametrize("interruption", ["after_ack", "dispatch_lost"])
def test_replace_interrupted_after_the_first_batch_resumes_to_the_full_list(interruption):
    world = lagging(BASELINE, reads=2)
    journal = world.journal()
    journal.freeze_replace("target", DESIRED)
    if interruption == "after_ack":
        original = journal._before_batch

        def stop_before_second(step, item):
            if item["method"] == "POST":
                raise Crash()
            return original(step, item)

        journal._before_batch = stop_before_second
    else:
        original = world.dispatch

        def lose_second(method, path, body):
            if world.writes:
                raise Crash()
            return original(method, path, body)

        journal.dispatch = lose_second
    with pytest.raises(Crash):
        journal.resume()
    assert len(tracks(world)) == 100
    assert world.journal().resume()["state"] == "succeeded"
    assert tracks(world) == DESIRED
    assert len(world.writes) == 3


def test_single_batch_remove_passes_its_final_reconcile_under_snapshot_lag():
    world = lagging(BASELINE + [uri(500 + index) for index in range(16)], reads=3)
    journal = world.journal()
    journal.freeze_remove("target", [uri(500 + index) for index in range(16)])
    outcome = journal.resume()
    assert outcome["state"] == "succeeded"
    assert tracks(world) == BASELINE


def test_full_lag_defers_then_completes_once_the_read_catches_up():
    world = lagging(BASELINE, mode="full", reads=1)
    journal = world.journal()
    journal.freeze_replace("target", DESIRED)
    with pytest.raises(OperationDeferred):
        journal.resume()
    assert len(world.writes) == 1
    assert world.saved["status"] != "needs_attention"
    deferrals = 1
    while True:
        make_due(world)
        try:
            outcome = world.journal().resume()
            break
        except OperationDeferred:
            deferrals += 1
    # Every write is followed by one lagging read, so the two later batches and the final reconcile each wait once.
    assert deferrals == 3
    assert outcome["state"] == "succeeded"
    assert tracks(world) == DESIRED
    assert "read_retry" not in world.saved


def test_full_lag_that_never_clears_exhausts_the_read_budget_without_an_external_edit():
    world = lagging(BASELINE, mode="full", reads=10_000)
    journal = world.journal()
    journal.freeze_replace("target", DESIRED)
    with pytest.raises((OperationDeferred, ReadRetryExhausted)):
        journal.resume()
    for _ in range(20):
        make_due(world)
        try:
            outcome = world.journal().resume()
        except OperationDeferred:
            continue
        except ReadRetryExhausted:
            break
        raise AssertionError(outcome)
    else:
        raise AssertionError("the read budget was never exhausted")
    assert world.saved["status"] != "needs_attention"
    assert len(world.writes) == 1


def test_a_genuine_edit_before_a_later_batch_is_still_an_external_edit():
    world = lagging(BASELINE, reads=2)
    journal = world.journal()
    journal.freeze_replace("target", DESIRED)
    external_edit_after_first_write(world, journal)
    outcome = journal.resume()
    assert outcome["state"] == "needs_attention"
    assert outcome["recovery"]["reason"] == "external_edit"
    assert len(world.writes) == 1


def test_a_genuine_edit_at_the_final_reconcile_is_still_an_external_edit():
    world = lagging(BASELINE + [uri(500)], reads=0)
    journal = world.journal()
    journal.freeze_remove("target", [uri(500)])
    external_edit_after_first_write(world, journal)
    outcome = journal.resume()
    assert outcome["state"] == "needs_attention"
    assert outcome["recovery"]["reason"] == "external_edit"


def test_a_genuine_edit_that_keeps_the_acknowledged_snapshot_is_still_an_external_edit():
    world = lagging(BASELINE, reads=0)
    journal = world.journal()
    journal.freeze_replace("target", DESIRED)
    original = world.dispatch

    def dispatch(method, path, body):
        result = original(method, path, body)
        if len(world.writes) == 1:
            world.playlists["target"]["track_uris"] = DESIRED[:50]
        return result

    journal.dispatch = dispatch
    assert journal.resume()["recovery"]["reason"] == "external_edit"


def test_a_dispatched_batch_that_landed_is_acknowledged_under_a_new_snapshot():
    world = lagging(BASELINE + [uri(500)], reads=0)
    journal = world.journal()
    journal.freeze_remove("target", [uri(500)])
    original = world.dispatch

    def lost_response(*args):
        original(*args)
        raise Crash()

    journal.dispatch = lost_response
    with pytest.raises(Crash):
        journal.resume()
    assert world.journal().resume()["state"] == "succeeded"
    assert len(world.writes) == 1


def test_a_dispatched_batch_seen_only_under_a_lagging_snapshot_is_neither_acknowledged_nor_replayed():
    world = lagging(BASELINE + [uri(500)], reads=1)
    journal = world.journal()
    journal.freeze_remove("target", [uri(500)])
    original = world.dispatch

    def lost_response(*args):
        original(*args)
        raise Crash()

    journal.dispatch = lost_response
    with pytest.raises(Crash):
        journal.resume()
    writes = deepcopy(world.writes)
    outcome = world.journal().resume()
    assert outcome["state"] == "needs_attention"
    assert outcome["recovery"]["reason"] == "snapshot_ambiguous"
    assert world.writes == writes


def test_an_unlanded_dispatch_read_under_a_lagging_snapshot_is_not_an_external_edit():
    world = lagging(BASELINE, reads=3)
    journal = world.journal()
    journal.freeze_replace("target", DESIRED)
    original = world.dispatch

    def lose_second(method, path, body):
        if world.writes:
            raise Crash()
        return original(method, path, body)

    journal.dispatch = lose_second
    with pytest.raises(Crash):
        journal.resume()
    outcome = world.journal().resume()
    assert outcome["recovery"]["reason"] != "external_edit"


@pytest.mark.parametrize("mode", ["snapshot", "full"])
def test_compensation_replace_of_the_original_list_completes_under_lag(mode):
    world = World(BASELINE)
    journal = world.journal()
    journal.freeze_replace("target", DESIRED)
    original = world.dispatch

    def reject_second(method, path, body):
        if world.writes:
            raise SpotifyWriteRejectedError("batch rejected")
        return original(method, path, body)

    journal.dispatch = reject_second
    with pytest.raises(SpotifyWriteRejectedError):
        journal.resume()
    assert len(tracks(world)) == 100
    world.lag_mode, world.lag_reads = mode, 2
    while True:
        try:
            outcome = world.journal().compensate()
            break
        except OperationDeferred:
            make_due(world)
    assert outcome["state"] == "rolled_back"
    assert tracks(world) == BASELINE
    assert len(world.writes) == 4


def test_a_genuine_edit_during_compensation_is_still_an_external_edit():
    world = World(BASELINE)
    journal = world.journal()
    journal.freeze_replace("target", DESIRED)
    original = world.dispatch

    def reject_second(method, path, body):
        if len(world.writes) == 1:
            raise SpotifyWriteRejectedError("batch rejected")
        return original(method, path, body)

    journal.dispatch = reject_second
    with pytest.raises(SpotifyWriteRejectedError):
        journal.resume()
    world.playlists["target"]["track_uris"] = DESIRED[:40]
    world.playlists["target"]["snapshot_id"] = "someone-else"
    outcome = world.journal().compensate()
    assert outcome["state"] == "needs_attention"
    assert outcome["recovery"]["reason"] == "external_edit"
