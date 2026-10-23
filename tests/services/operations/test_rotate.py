"""Frozen Rotate choices run through the real durable journal and Spotify observer."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.services.operations.journal import NeedsAttention
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.spotify.api import SpotifyAPI
from tests.services.operations.test_crash_recovery import Crash, World, uri


class RotateRuntime(OperationRuntime):
    """Use production prepare/journal with a durable document and authorized fake I/O."""

    def __init__(self, world):
        self.world = world
        self.user = SimpleNamespace(id=7)
        self.lease = SimpleNamespace(targets=("target", "archive"))
        self.operation = SimpleNamespace(targets=["target", "archive"])
        self._lease_lost = None
        self._journal = None
        self._api = object.__new__(SpotifyAPI)
        self._api._invalidate_playlist_write_cache = lambda resource: None

    def get_journal(self):
        return deepcopy(self.world.saved or {})

    def checkpoint(self, document):
        return self.world.checkpoint(document)

    def read_resource(self, resource):
        return self.world.read(resource)

    def read_snapshot(self, resource):
        state = self.world.read(resource)
        return {"snapshot_id": state["snapshot_id"], "metadata": state["metadata"]}

    def dispatch(self, method, path, body):
        return self.world.dispatch(method, path, body)


def world_with(target=(), archive=()):
    world = World(target)
    world.playlists["archive"] = {
        "track_uris": list(archive), "metadata": {"name": "Archive", "public": False},
        "snapshot_id": "archive-0", "settled": True,
    }
    return world


def config(**changes):
    from shuffify.services.operations.executors.rotate import RotateConfig

    return replace(RotateConfig(7, "target", "archive", target_size=10), **changes)


def run(world, command=None, *, runtime=None, prepare_key="schedule.step.0.rotate.v1"):
    from shuffify.services.operations.executors.rotate import execute_rotate

    runtime = runtime or RotateRuntime(world)
    with runtime.journal.observe():
        return execute_rotate(runtime, command or config(), prepare_key=prepare_key)


def test_fifo_swap_preserves_orders_and_supplies_original_and_verified_resources(monkeypatch):
    world = world_with([uri(1), uri(2), uri(3)], [uri(4), uri(5), uri(6)])
    monkeypatch.setattr("random.sample", lambda values, count: values[-count:])
    before = deepcopy(world.playlists)

    outcome = run(world, config(rotation_count=2))

    assert outcome["result"] == {"tracks_added": 2, "tracks_total": 3}
    assert outcome["before"] == before
    assert outcome["after"] == world.playlists
    assert world.playlists["target"]["track_uris"] == [uri(1), uri(4), uri(5)]
    assert world.playlists["archive"]["track_uris"] == [uri(6), uri(2), uri(3)]
    assert [(method, path) for method, path, _ in world.writes] == [
        ("POST", "/playlists/archive/items"), ("POST", "/playlists/target/items"),
        ("DELETE", "/playlists/target/items"), ("DELETE", "/playlists/archive/items"),
    ]


@pytest.mark.parametrize("protection", [{"protect_count": 99}, {"locked_uris": (uri(1), uri(2))}])
def test_overlap_purge_runs_even_when_every_target_track_is_protected(protection):
    world = world_with([uri(1), uri(2)], [uri(1), uri(1), uri(3), uri(2)])

    result = run(world, config(**protection))

    assert result["result"] == {"tracks_added": 0, "tracks_total": 2, "skipped_reason": "all_tracks_protected"}
    assert world.playlists["archive"]["track_uris"] == [uri(3)]
    assert len(world.writes) == 1
    assert world.writes[0][2] == {"items": [{"uri": uri(1)}, {"uri": uri(1)}, {"uri": uri(2)}]}


def test_empty_target_precedes_cap_validation_and_does_not_read_archive():
    world = world_with([], [uri(2)])
    reads = []
    original = world.read

    def read(resource):
        reads.append(resource)
        return original(resource)

    world.read = read
    outcome = run(world, config(target_size=None))

    assert outcome["result"] == {"tracks_added": 0, "tracks_total": 0}
    assert set(outcome["before"]) == set(outcome["after"]) == {"target"}
    assert set(reads) == {"target"}
    assert world.writes == []


def test_nonempty_target_requires_cap_before_reading_archive():
    world = world_with([uri(1)], [uri(2)])
    with pytest.raises(JobExecutionError, match="playlist size cap"):
        run(world, config(target_size=None))
    assert world.writes == []
    assert world.saved is None


def test_overflow_duplicate_uri_removes_every_occurrence_and_preserves_selected_duplicates(monkeypatch):
    world = world_with([uri(1), uri(1), uri(2), uri(3)], [uri(1), uri(4)])
    monkeypatch.setattr("random.sample", lambda values, count: values[:count])

    outcome = run(world, config(target_size=2))

    assert outcome["result"] == {"tracks_added": 0, "tracks_total": 2}
    assert world.playlists["target"]["track_uris"] == [uri(2), uri(3)]
    # Purge the old occurrence first, then append both selected occurrences.
    assert world.playlists["archive"]["track_uris"] == [uri(4), uri(1), uri(1)]
    assert [path for _, path, _ in world.writes] == [
        "/playlists/archive/items", "/playlists/archive/items", "/playlists/target/items",
    ]


def test_overflow_can_drop_below_cap_when_one_selected_uri_has_multiple_occurrences(monkeypatch):
    world = world_with([uri(1), uri(1), uri(2)], [uri(3)])
    monkeypatch.setattr("random.sample", lambda values, count: values[:count])

    outcome = run(world, config(target_size=2))

    assert outcome["result"] == {"tracks_added": 0, "tracks_total": 1}
    assert world.playlists["target"]["track_uris"] == [uri(2)]
    assert world.playlists["archive"]["track_uris"] == [uri(3), uri(1)]


def test_swap_preserves_untrimmed_fifo_incoming_when_fewer_outgoing_are_eligible():
    world = world_with([uri(1), uri(2)], [uri(3), uri(4), uri(5)])

    outcome = run(world, config(rotation_count=3, protect_count=1))

    assert outcome["result"] == {"tracks_added": 1, "tracks_total": 4}
    assert world.playlists["target"]["track_uris"] == [uri(1), uri(3), uri(4), uri(5)]
    assert world.playlists["archive"]["track_uris"] == [uri(2)]


def test_duplicate_fifo_selection_removes_all_source_occurrences(monkeypatch):
    world = world_with([uri(1), uri(2)], [uri(3), uri(3), uri(4)])
    monkeypatch.setattr("random.sample", lambda values, count: values[:count])

    outcome = run(world, config(rotation_count=1))

    assert outcome["result"] == {"tracks_added": 1, "tracks_total": 2}
    assert world.playlists["archive"]["track_uris"] == [uri(4), uri(1)]
    assert world.playlists["target"]["track_uris"] == [uri(2), uri(3)]


def test_protected_prefix_and_locked_uri_occurrences_are_excluded_from_random_pool(monkeypatch):
    world = world_with([uri(1), uri(2), uri(3), uri(2), uri(4)], [uri(5)])
    calls = []

    def sample(values, count):
        calls.append((list(values), count))
        return values[:count]

    monkeypatch.setattr("random.sample", sample)
    outcome = run(world, config(rotation_count=1, protect_count=1, locked_uris=(uri(2),)))

    assert calls == [([uri(3), uri(4)], 1)]
    assert outcome["after"]["target"]["track_uris"] == [uri(1), uri(2), uri(2), uri(4), uri(5)]


def test_sample_entire_pool_preserves_order_without_calling_random(monkeypatch):
    world = world_with([uri(1), uri(2)], [uri(3), uri(4), uri(5)])

    def unexpected_sample(*args):
        pytest.fail("Sampling the entire eligible pool must preserve its order")

    monkeypatch.setattr("random.sample", unexpected_sample)
    outcome = run(world, config(rotation_count=3))

    assert outcome["result"] == {"tracks_added": 2, "tracks_total": 3}
    assert outcome["after"]["archive"]["track_uris"] == [uri(1), uri(2)]


def test_empty_archive_returns_both_authorized_noop_resources_without_writes():
    world = world_with([uri(1), uri(2)], [])
    original = deepcopy(world.playlists)

    outcome = run(world)

    assert outcome["result"] == {"tracks_added": 0, "tracks_total": 2}
    assert outcome["before"] == outcome["after"] == original
    assert world.writes == []


def test_preparation_freezes_random_once_before_any_plan_or_write(monkeypatch):
    from shuffify.services.operations.executors.rotate import prepare_rotate

    world = world_with([uri(1), uri(2), uri(3)], [uri(4)])
    calls = []

    def sample(values, count):
        calls.append((list(values), count))
        return values[-count:]

    monkeypatch.setattr("random.sample", sample)
    first = prepare_rotate(RotateRuntime(world), config(rotation_count=1), prepare_key="step.rotate")
    assert world.writes == []
    assert not world.saved.get("plans")
    second = prepare_rotate(RotateRuntime(world), config(rotation_count=1), prepare_key="step.rotate")
    assert second == first
    assert calls == [([uri(1), uri(2), uri(3)], 1)]
    assert world.trace.count("read.before") == 2
    outcome = run(world, config(rotation_count=1), prepare_key="step.rotate")
    assert len(calls) == 1
    assert outcome["after"]["target"]["track_uris"] == [uri(1), uri(2), uri(4)]


@pytest.mark.parametrize("change", [{"user_id": 8}, {"archive_playlist_id": "unleased"},
                                   {"archive_playlist_id": "target"}, {"rotation_mode": "unsupported"}])
def test_invalid_identity_or_configuration_fails_before_any_provider_read(change):
    world = world_with([uri(1)], [uri(2)])
    with pytest.raises(JobExecutionError):
        run(world, config(**change))
    assert world.trace == []
    assert world.writes == []


def test_reusing_prepare_key_with_another_captured_configuration_cannot_change_choices():
    from shuffify.services.operations.executors.rotate import prepare_rotate

    world = world_with([uri(1)], [uri(2)])
    prepare_rotate(RotateRuntime(world), config(), prepare_key="rotate")
    with pytest.raises(JobExecutionError, match="configuration"):
        prepare_rotate(RotateRuntime(world), config(protect_count=1), prepare_key="rotate")
    assert world.writes == []


def test_provider_authorization_error_propagates_without_frozen_plan_or_write():
    from shuffify.spotify.exceptions import SpotifyForbiddenError

    world = world_with([uri(1)], [uri(2)])

    def forbidden(resource):
        raise SpotifyForbiddenError("owner changed")

    world.read = forbidden
    with pytest.raises(SpotifyForbiddenError):
        run(world)
    assert world.saved is None
    assert world.writes == []


def test_prepared_snapshot_only_external_edit_is_rejected_before_first_write():
    from shuffify.services.operations.executors.rotate import prepare_rotate

    world = world_with([uri(1)], [uri(2)])
    command = config(rotation_count=1)
    prepare_rotate(RotateRuntime(world), command, prepare_key="rotate")
    world.playlists["target"]["snapshot_id"] = "external-same-content"
    with pytest.raises(NeedsAttention, match="prepared_baseline_changed"):
        run(world, command, prepare_key="rotate")
    assert world.writes == []


@pytest.mark.parametrize("change", [{"snapshot_id": "external-restored"},
                                   {"track_uris": [uri(99)], "snapshot_id": "external-order"}])
def test_target_edit_is_checked_before_archive_overlap_purge(change):
    from shuffify.services.operations.executors.rotate import prepare_rotate

    world = world_with([uri(1), uri(2)], [uri(1), uri(3)])
    command = config(rotation_count=1)
    prepare_rotate(RotateRuntime(world), command, prepare_key="rotate")
    world.playlists["target"].update(change)
    before = deepcopy(world.playlists)

    with pytest.raises(NeedsAttention):
        run(world, command, prepare_key="rotate")

    assert world.playlists == before
    assert world.writes == []


def test_protected_target_remains_guarded_after_archive_purge_crash():
    world = world_with([uri(1), uri(2)], [uri(1), uri(3)])
    command = config(protect_count=99)
    checkpoint = world.checkpoint

    def cut_after_purge(document):
        saved = checkpoint(document)
        if any(batch["state"] == "acknowledged" for plan in document.get("plans", [])
               for batch in plan["batches"]):
            raise Crash("after archive purge acknowledgment")
        return saved

    world.checkpoint = cut_after_purge
    with pytest.raises(Crash):
        run(world, command)
    world.checkpoint = checkpoint
    world.playlists["target"]["snapshot_id"] = "external-restored"
    before, written = deepcopy(world.playlists), deepcopy(world.writes)

    with pytest.raises(NeedsAttention):
        run(world, command)

    assert world.playlists == before
    assert world.writes == written
    assert world.saved["status"] == "needs_attention"


def test_intermediate_snapshot_only_external_edit_is_not_adopted_as_our_baseline():
    world = world_with([uri(1)], [uri(2)])
    original = world.dispatch

    def dispatch(method, path, body):
        response = original(method, path, body)
        # The archive append is acknowledged with snapshot A; another actor then
        # makes a metadata edit and restores it, producing snapshot B.
        if method == "POST" and path == "/playlists/archive/items":
            world.playlists["archive"]["snapshot_id"] = "external-restored-content"
        return response

    world.dispatch = dispatch
    with pytest.raises(NeedsAttention):
        run(world, config(rotation_count=1))
    assert len(world.writes) == 1
    assert world.playlists["archive"]["track_uris"] == [uri(2), uri(1)]
    assert world.playlists["target"]["track_uris"] == [uri(1)]


def test_without_outer_journal_observer_no_write_can_bypass_the_durable_transport():
    from shuffify.services.operations.executors.rotate import execute_rotate

    world = world_with([uri(1)], [uri(2)])
    with pytest.raises(JobExecutionError, match="observer"):
        execute_rotate(RotateRuntime(world), config(), prepare_key="rotate")
    assert world.trace == []


@pytest.mark.parametrize("cut_after", [1, 2, 3, 4, 5, 6, 7, 8])
def test_acknowledged_multibatch_recovery_reuses_each_wire_write_once(monkeypatch, cut_after):
    # Four high-level actions of 101 items each create eight real wire batches.
    world = world_with([uri(index) for index in range(202)], [uri(1000 + index) for index in range(101)])
    command = config(rotation_count=101, target_size=202)
    random_calls = []

    def sample(values, count):
        random_calls.append(1)
        return values[:count]

    monkeypatch.setattr("random.sample", sample)
    original_checkpoint = world.checkpoint

    def cut(document):
        saved = original_checkpoint(document)
        acknowledgements = [item for step in document.get("plans", []) for item in step["batches"]
                            if item["state"] == "acknowledged"]
        if len(acknowledgements) == cut_after:
            raise Crash("after acknowledged wire batch")
        return saved

    world.checkpoint = cut
    with pytest.raises(Crash):
        run(world, command)
    world.checkpoint = original_checkpoint

    outcome = run(world, command)

    assert outcome["result"] == {"tracks_added": 101, "tracks_total": 202}
    assert len(random_calls) == 1
    assert len(world.writes) == 8
    assert world.playlists["target"]["track_uris"] == [uri(index) for index in range(101, 202)] + [
        uri(1000 + index) for index in range(101)
    ]
    assert world.playlists["archive"]["track_uris"] == [uri(index) for index in range(101)]


def test_lost_response_after_applied_append_is_reconciled_without_append_replay(monkeypatch):
    world = world_with([uri(1), uri(2)], [uri(3)])
    monkeypatch.setattr("random.sample", lambda values, count: values[:count])
    original = world.dispatch
    crashed = False

    def lose_response(method, path, body):
        nonlocal crashed
        result = original(method, path, body)
        if method == "POST" and not crashed:
            crashed = True
            raise Crash("applied append response lost")
        return result

    world.dispatch = lose_response
    with pytest.raises(Crash):
        run(world, config(rotation_count=1))
    world.dispatch = original

    outcome = run(world, config(rotation_count=1))

    assert outcome["result"] == {"tracks_added": 1, "tracks_total": 2}
    assert len(world.writes) == 4
    assert world.playlists["archive"]["track_uris"] == [uri(1)]
    assert world.playlists["target"]["track_uris"] == [uri(2), uri(3)]


def test_recovery_never_compensates_or_overwrites_an_external_edit(monkeypatch):
    world = world_with([uri(1), uri(2)], [uri(3)])
    monkeypatch.setattr("random.sample", lambda values, count: values[:count])
    original = world.dispatch

    def crash(method, path, body):
        original(method, path, body)
        raise Crash("after first dispatched write")

    world.dispatch = crash
    with pytest.raises(Crash):
        run(world, config(rotation_count=1))
    world.dispatch = original
    world.playlists["target"]["track_uris"] = [uri(99)]
    world.playlists["target"]["snapshot_id"] = "external-edit"
    before_recovery = deepcopy(world.playlists)

    with pytest.raises(NeedsAttention):
        run(world, config(rotation_count=1))

    assert world.playlists == before_recovery
    assert len(world.writes) == 1
    assert world.saved["phase"] == "forward"
    assert world.saved["status"] == "needs_attention"


def _union(world):
    return set(world.playlists["target"]["track_uris"]) | set(world.playlists["archive"]["track_uris"])


def _interrupt_after_each_write(world_factory, command, writes, monkeypatch):
    """Cut after every acknowledged wire write; no cut may leave a track in neither playlist."""
    monkeypatch.setattr("random.sample", lambda values, count: values[:count])
    for cut_after in range(1, writes + 1):
        world = world_factory()
        everything = _union(world)
        original_checkpoint = world.checkpoint

        def cut(document, original_checkpoint=original_checkpoint, cut_after=cut_after):
            saved = original_checkpoint(document)
            acknowledged = [item for step in document.get("plans", []) for item in step["batches"]
                            if item["state"] == "acknowledged"]
            if len(acknowledged) == cut_after:
                raise Crash("after acknowledged write")
            return saved

        world.checkpoint = cut
        with pytest.raises(Crash):
            run(world, command)
        assert len(world.writes) == cut_after
        assert everything <= _union(world), f"track lost after write {cut_after}"
        world.checkpoint = original_checkpoint
        run(world, command)
        assert len(world.writes) == writes


def test_overflow_never_loses_a_track_at_any_interruption_point(monkeypatch):
    _interrupt_after_each_write(
        lambda: world_with([uri(index) for index in range(1, 7)], [uri(20)]),
        config(target_size=4), 2, monkeypatch)


def test_swap_never_loses_a_track_at_any_interruption_point(monkeypatch):
    _interrupt_after_each_write(
        lambda: world_with([uri(1), uri(2), uri(3)], [uri(4), uri(5), uri(6)]),
        config(rotation_count=2), 4, monkeypatch)


def test_overflow_archives_selected_tracks_before_removing_them(monkeypatch):
    world = world_with([uri(1), uri(2), uri(3)], [uri(9)])
    monkeypatch.setattr("random.sample", lambda values, count: values[:count])
    run(world, config(target_size=2))
    assert [(method, path) for method, path, _ in world.writes] == [
        ("POST", "/playlists/archive/items"), ("DELETE", "/playlists/target/items")]


def test_swap_adds_before_it_removes(monkeypatch):
    world = world_with([uri(1), uri(2), uri(3)], [uri(4), uri(5), uri(6)])
    monkeypatch.setattr("random.sample", lambda values, count: values[-count:])
    run(world, config(rotation_count=2))
    assert [(method, path) for method, path, _ in world.writes] == [
        ("POST", "/playlists/archive/items"), ("POST", "/playlists/target/items"),
        ("DELETE", "/playlists/target/items"), ("DELETE", "/playlists/archive/items"),
    ]


def _old_order_actions(prepared):
    """Rebuild the previous remove-before-add action order from a prepared rotation."""
    before, choices = prepared["before"], prepared["choices"]
    current = deepcopy(before)
    actions = []

    def action(kind, resource, values, phase):
        if not values:
            return
        baseline = deepcopy(current[resource])
        if kind == "remove":
            gone = set(values)
            current[resource]["track_uris"] = [u for u in baseline["track_uris"] if u not in gone]
        else:
            current[resource]["track_uris"].extend(values)
        actions.append({"kind": kind, "resource": resource, "track_uris": list(values),
                        "before": baseline, "phase": phase})

    action("remove", "archive", choices["purged_uris"], "archive_purge")
    archive_set = set(current["archive"]["track_uris"])
    if choices["phase"] == "overflow":
        selected = choices["overflow_uris"]
        action("remove", "target", selected, "rotate")
        action("add", "archive", [u for u in selected if u not in archive_set], "rotate")
    else:
        incoming, outgoing = choices["swap_in_uris"], choices["swap_out_uris"]
        action("remove", "target", outgoing, "rotate")
        action("add", "archive", [u for u in outgoing if u not in archive_set], "rotate")
        action("remove", "archive", incoming, "rotate")
        action("add", "target", incoming, "rotate")
    return actions, current


@pytest.mark.parametrize("target, archive, change", [
    ([uri(1), uri(2), uri(3)], [uri(4), uri(5), uri(6)], {"rotation_count": 2}),
    ([uri(1), uri(2), uri(3)], [uri(1), uri(4), uri(5)], {"rotation_count": 2, "protect_count": 1}),
    ([uri(1), uri(2), uri(3), uri(4)], [uri(9)], {"target_size": 2}),
])
def test_new_order_reaches_the_same_final_states_as_the_old_order(monkeypatch, target, archive, change):
    from shuffify.services.operations.executors.rotate import prepare_rotate

    monkeypatch.setattr("random.sample", lambda values, count: values[-count:])
    world = world_with(target, archive)
    prepared = prepare_rotate(RotateRuntime(world), config(**change), prepare_key="rotate")
    _, old_desired = _old_order_actions(prepared)
    assert prepared["desired"] == old_desired
    run(world, config(**change), prepare_key="rotate")
    for name in ("target", "archive"):
        assert world.playlists[name]["track_uris"] == old_desired[name]["track_uris"]


@pytest.mark.parametrize("target, archive, change", [
    ([uri(1), uri(2), uri(3)], [uri(1), uri(4), uri(5)], {"rotation_count": 2}),
    ([uri(1), uri(2), uri(3), uri(4)], [uri(9)], {"target_size": 2}),
])
def test_journal_frozen_before_the_reorder_still_replays_to_the_same_final_state(
        monkeypatch, target, archive, change):
    from shuffify.services.operations.executors.rotate import prepare_rotate

    monkeypatch.setattr("random.sample", lambda values, count: values[-count:])
    world = world_with(target, archive)
    command = config(**change)
    prepared = prepare_rotate(RotateRuntime(world), command, prepare_key="rotate")
    old_actions, old_desired = _old_order_actions(prepared)
    world.saved["runtime"]["prepared"]["rotate"]["actions"] = old_actions

    run(world, command, prepare_key="rotate")

    for name in ("target", "archive"):
        assert world.playlists[name]["track_uris"] == old_desired[name]["track_uris"]
