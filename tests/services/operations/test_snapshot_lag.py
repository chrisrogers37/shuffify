"""Spotify reads can lag a write the run already knows succeeded; that is never an external edit."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from shuffify.services.operations.journal import OperationDeferred, ReadRetryExhausted
from shuffify.spotify.exceptions import SpotifyWriteRejectedError
from tests.services.operations.test_crash_recovery import Crash, World, uri
from tests.services.operations.test_restore_command import restoration  # noqa: F401
from tests.services.operations.test_routine_commands import routine  # noqa: F401

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
    world.lag_left.clear()  # The restarted worker comes back after the lag has cleared.
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
    writes = deepcopy(world.writes)
    outcome = world.journal().resume()
    # The lag signature of an earlier write is never proof that a dispatched batch can be replayed.
    assert outcome["state"] == "needs_attention"
    assert outcome["recovery"]["reason"] == "unsettled_dispatch"
    assert world.writes == writes
    assert len(tracks(world)) == 100


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


@pytest.mark.parametrize("reads", [1, 3, 4, 6])
def test_compensation_started_while_the_forward_write_still_lags_keeps_the_forward_chain(reads):
    world = lagging(BASELINE, reads=reads)
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
    outcome = world.journal().compensate()
    assert outcome["state"] == "rolled_back", outcome
    assert tracks(world) == BASELINE


def test_a_dispatched_batch_under_an_earlier_snapshot_is_never_replayed_as_settled():
    from shuffify.services.operations.recovery import reconcile_resource

    first, second = {"uris": [uri(1)]}, {"uris": [uri(2)]}
    states = [{"track_uris": [], "metadata": {}}, {"track_uris": [uri(1)], "metadata": {}},
              {"track_uris": [uri(1), uri(2)], "metadata": {}}]
    batches = [
        {"state": "acknowledged", "before": states[0], "after": states[1], "snapshot_before": "s0",
         "snapshot_id": "s1", "path": "/playlists/t/items", "body": first},
        {"state": "dispatched", "before": states[1], "after": states[2], "snapshot_before": "s1",
         "snapshot_id": None, "path": "/playlists/t/items", "body": second},
    ]
    resource = {"baseline": states[0], "snapshot_id": "s0"}
    for snapshot, settled, reason in [("s1", True, "replay_settled"), ("s0", True, "unsettled_dispatch"),
                                      ("s1", None, "unsettled_dispatch")]:
        observed = {**states[1], "snapshot_id": snapshot, "settled": settled}
        assert reconcile_resource(resource, batches, observed) == ([], reason)


def test_metadata_scope_never_treats_a_snapshot_as_staleness_evidence():
    from shuffify.services.operations.recovery import CHANGED, CURRENT, observation_verdict

    older = {"scope": "metadata", "metadata": {"name": "Old"}}
    newer = {"scope": "metadata", "metadata": {"name": "New"}}
    assert observation_verdict({**older, "snapshot_id": "s1"}, newer, "s1", [(older, "s1")]) == CHANGED
    assert observation_verdict({**newer, "snapshot_id": "other"}, newer, "s1", []) == CURRENT


def test_paired_reads_that_disagree_after_a_write_defer_instead_of_failing(monkeypatch):
    from types import SimpleNamespace

    from shuffify.services.operations.control import read_retry_delay
    from shuffify.services.operations.runtime import OperationRuntime
    from shuffify.services.workshop_commit_service import WorkshopCommitService

    reads = iter([SimpleNamespace(track_uris=(uri(1),), snapshot_id="new"),
                  SimpleNamespace(track_uris=(uri(1),), snapshot_id="old")])
    monkeypatch.setattr(WorkshopCommitService, "read_complete", lambda self, playlist, owner: next(reads))

    class Runtime(OperationRuntime):
        api = object()
        user = SimpleNamespace(spotify_id="owner")

    with pytest.raises(Exception) as caught:
        Runtime.read_resource(object.__new__(Runtime), "target")
    assert read_retry_delay(caught.value) is not None


@pytest.fixture
def pinned_sample(monkeypatch):
    monkeypatch.setattr("random.sample", lambda values, count: values[:count])


def run_rotate(world, command):
    from tests.services.operations.test_rotate import run

    for _ in range(40):
        try:
            return run(world, command)
        except OperationDeferred:
            make_due(world)
    raise AssertionError("the rotation never finished")


@pytest.mark.parametrize("mode,reads", [("snapshot", count) for count in range(1, 7)]
                         + [("full", count) for count in range(1, 5)])
def test_swap_rotation_completes_under_lag_without_losing_tracks(pinned_sample, reads, mode):
    from tests.services.operations.test_rotate import config, world_with

    target, archive = [uri(index) for index in range(1, 11)], [uri(index) for index in range(11, 21)]
    world = world_with(target, archive)
    world.lag_reads, world.lag_mode = reads, mode
    outcome = run_rotate(world, config(rotation_count=3, target_size=10))
    assert outcome["result"]["tracks_added"] == 3
    assert len(tracks(world)) == 10
    assert sorted(tracks(world) + world.playlists["archive"]["track_uris"]) == sorted(target + archive)
    assert world.playlists["archive"]["track_uris"] and len(world.playlists["archive"]["track_uris"]) == 10


@pytest.mark.parametrize("mode,reads", [("snapshot", count) for count in range(1, 7)]
                         + [("full", count) for count in range(1, 5)])
def test_overflow_trim_rotation_completes_under_lag(pinned_sample, reads, mode):
    from tests.services.operations.test_rotate import config, world_with

    target, archive = [uri(index) for index in range(1, 15)], [uri(100)]
    world = world_with(target, archive)
    world.lag_reads, world.lag_mode = reads, mode
    outcome = run_rotate(world, config(target_size=10))
    assert outcome["result"]["tracks_total"] == 10
    assert len(tracks(world)) == 10
    assert sorted(tracks(world) + world.playlists["archive"]["track_uris"]) == sorted(target + archive)


@pytest.mark.parametrize("reads", [1, 2, 3, 5])
def test_reviewed_restore_completes_under_lag(restoration, reads):
    from tests.services.operations.test_read_deferral_budget import make_due as make_row_due

    row = restoration.enqueue_restore()
    restoration.world.lag_reads = reads
    for _ in range(40):
        finished, completed = restoration.run_restore(row)
        if finished:
            break
        make_row_due(restoration, row.id)
    assert completed.state == "succeeded", completed.journal.get("recovery")
    assert restoration.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
