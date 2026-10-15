"""Actual unarchive preserves requested occurrences and writes target before source."""

import importlib
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.services.operations.journal import NeedsAttention
from tests.services.operations.test_collect_drip_executors import Runtime
from tests.services.operations.test_crash_recovery import Crash, World, uri
from tests.services.operations.test_routine_commands import routine as routine


def module():
    return importlib.import_module("shuffify.services.operations.executors.unarchive")


@pytest.fixture
def world():
    value = World([uri(1), uri(1), uri(2)])
    value.playlists["archive"] = deepcopy(value.playlists["target"])
    value.playlists["archive"]["track_uris"] = [uri(3), uri(4), uri(3), uri(5)]
    value.playlists["archive"]["metadata"]["name"] = "Archive"
    return value


def config(values=(3, 3, 4)):
    return module().UnarchiveConfig(1, "target", "archive", tuple(uri(value) for value in values))


def run(runtime, captured):
    with runtime.journal.observe():
        return module().execute_unarchive(runtime, captured, prepare_key="unarchive.v1")


def test_preparation_is_sample_free_and_has_no_spotify_or_domain_effects(world):
    before = deepcopy(world.playlists)
    prepared = module().prepare_unarchive(Runtime(world), config(), prepare_key="unarchive.v1")
    assert world.playlists == before
    assert world.writes == []
    assert (world.saved or {}).get("plans", []) == []
    assert prepared["desired"]["target"]["track_uris"] == before["target"]["track_uris"] + [uri(3), uri(3), uri(4)]
    assert prepared["desired"]["archive"]["track_uris"] == [uri(5)]
    assert [action["resource"] for action in prepared["actions"]] == ["target", "archive"]
    assert prepared["local_actions"]["history"]["before_uris"] == before["target"]["track_uris"]


def test_actual_move_appends_requested_duplicates_then_removes_all_source_occurrences(world):
    result = run(Runtime(world), config())
    assert world.playlists["target"]["track_uris"] == [uri(1), uri(1), uri(2), uri(3), uri(3), uri(4)]
    assert world.playlists["archive"]["track_uris"] == [uri(5)]
    assert [(method, path) for method, path, _ in world.writes] == [
        ("POST", "/playlists/target/items"),
        ("DELETE", "/playlists/archive/items"),
    ]
    assert result["result"]["unarchived_count"] == result["result"]["tracks_added"] == 3
    assert result["result"]["removed_count"] == 3
    assert result["local_actions"]["lock_reconcile"]["track_uris"] == world.playlists["target"]["track_uris"]
    assert result["local_actions"]["activity"]["track_count"] == 3


def test_duplicate_requests_are_appended_even_when_already_in_target_or_absent_from_archive(world):
    result = run(Runtime(world), config((1, 1, 9)))
    assert world.playlists["target"]["track_uris"] == [uri(1), uri(1), uri(2), uri(1), uri(1), uri(9)]
    assert world.playlists["archive"]["track_uris"] == [uri(3), uri(4), uri(3), uri(5)]
    assert result["result"]["unarchived_count"] == 3
    assert result["result"]["removed_count"] == 0
    assert len(world.writes) == 1


def test_empty_helper_input_is_a_verified_zero_effect_noop(world):
    before = deepcopy(world.playlists)
    result = run(Runtime(world), config(()))
    assert world.playlists == before
    assert world.writes == []
    assert result["result"]["unarchived_count"] == result["result"]["tracks_added"] == 0
    assert result["result"]["status"] == "not_required"
    assert all(value is None for value in result["local_actions"].values())


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", 2),
        ("user_id", True),
        ("archive_playlist_id", "target"),
        ("archive_playlist_id", "unreserved"),
        ("track_uris", ("not:track:uri",)),
    ],
)
def test_invalid_or_unreserved_capture_refuses_before_any_read(world, field, value):
    with pytest.raises(JobExecutionError):
        run(Runtime(world), replace(config(), **{field: value}))
    assert world.trace == []


def test_configuration_copies_occurrence_sequences_to_frozen_tuples(world):
    requested = [uri(3), uri(3)]
    captured = module().UnarchiveConfig(1, "target", "archive", requested)
    requested.append(uri(4))
    assert captured.track_uris == (uri(3), uri(3))
    with pytest.raises(FrozenInstanceError):
        captured.archive_playlist_id = "other"


@pytest.mark.parametrize("resource", ["target", "archive"])
@pytest.mark.parametrize("snapshot_only", [False, True])
def test_every_frozen_resource_is_guarded_before_first_write(world, resource, snapshot_only):
    runtime, captured = Runtime(world), config()
    module().prepare_unarchive(runtime, captured, prepare_key="unarchive.v1")
    if not snapshot_only:
        world.playlists[resource]["track_uris"].append(uri(9))
    world.playlists[resource]["snapshot_id"] = "external"
    with pytest.raises(NeedsAttention):
        run(runtime, captured)
    assert world.writes == []


def test_prepared_payload_cannot_change_under_the_same_durable_key(world):
    runtime, captured = Runtime(world), config()
    module().prepare_unarchive(runtime, captured, prepare_key="unarchive.v1")
    with pytest.raises(JobExecutionError, match="configuration changed"):
        run(runtime, config((3, 4)))
    assert world.writes == []


@pytest.mark.parametrize("size", [1, 99, 100, 101, 201])
def test_actual_transport_uses_deterministic_100_item_batches_in_move_order(world, size):
    requested = tuple(uri(index + 10) for index in range(size))
    captured = module().UnarchiveConfig(1, "target", "archive", requested)
    world.playlists["archive"]["track_uris"] = list(requested) * 2
    result = run(Runtime(world), captured)
    expected_batches = (size + 99) // 100
    assert [method for method, _, _ in world.writes] == ["POST"] * expected_batches + ["DELETE"] * expected_batches
    assert world.playlists["target"]["track_uris"] == [uri(1), uri(1), uri(2)] + list(requested)
    assert world.playlists["archive"]["track_uris"] == []
    assert result["result"]["unarchived_count"] == size
    assert result["result"]["removed_count"] == size * 2


@pytest.mark.parametrize("lost_call", [1, 3])
def test_applied_lost_response_on_either_phase_resumes_without_duplicate_append_or_cleanup(world, lost_call):
    requested = tuple(uri(index + 10) for index in range(101))
    captured = module().UnarchiveConfig(1, "target", "archive", requested)
    world.playlists["archive"]["track_uris"] = list(requested) * 2
    point, applied = world.point, []

    def lose(label):
        point(label)
        if label == "spotify.applied":
            applied.append(True)
            if len(applied) == lost_call:
                raise Crash()

    world.point = lose
    with pytest.raises(Crash):
        run(Runtime(world), captured)
    recovered = Runtime(world)
    assert recovered.journal.resume()["state"] == "succeeded"
    result = run(recovered, captured)
    assert len(world.writes) == 4
    assert world.playlists["target"]["track_uris"] == [uri(1), uri(1), uri(2)] + list(requested)
    assert world.playlists["archive"]["track_uris"] == []
    assert result["result"]["unarchived_count"] == 101


@pytest.mark.parametrize("resource", ["target", "archive"])
def test_recovery_reconciles_both_resources_before_resuming_any_partial_move(world, resource):
    requested = tuple(uri(index + 10) for index in range(101))
    captured = module().UnarchiveConfig(1, "target", "archive", requested)
    world.playlists["archive"]["track_uris"] = list(requested)
    point, stopped = world.point, []

    def stop(label):
        point(label)
        if label == "spotify.response" and not stopped:
            stopped.append(True)
            raise Crash()

    world.point = stop
    with pytest.raises(Crash):
        run(Runtime(world), captured)
    written = deepcopy(world.writes)
    world.playlists[resource].update(track_uris=[uri(99)], snapshot_id="external")
    recovered = Runtime(world)
    assert recovered.journal.resume()["state"] == "needs_attention"
    with pytest.raises(NeedsAttention):
        run(recovered, captured)
    assert world.writes == written
    assert world.playlists[resource]["track_uris"] == [uri(99)]


def test_cached_preparation_resumes_without_expanding_ordered_requests(world):
    captured = config((3, 3, 4))
    prepared = module().prepare_unarchive(Runtime(world), captured, prepare_key="unarchive.v1")
    result = run(Runtime(world), captured)
    assert result["choices"] == prepared["choices"]
    assert result["after"]["target"]["track_uris"] == prepared["desired"]["target"]["track_uris"]
    assert len(world.writes) == 2


def test_source_rejection_propagates_without_helper_compensation_or_false_success(world):
    from types import SimpleNamespace

    from shuffify.spotify.exceptions import SpotifyWriteRejectedError

    runtime = Runtime(world)
    request = runtime.http._session.request

    def reject(method, url, **kwargs):
        if method == "DELETE":
            return SimpleNamespace(status_code=400, text="{}", json=lambda: {})
        return request(method, url, **kwargs)

    runtime.http._session.request = reject
    with pytest.raises(SpotifyWriteRejectedError):
        run(runtime, config())
    assert [method for method, _, _ in world.writes] == ["POST"]
    assert world.playlists["target"]["track_uris"] == [uri(1), uri(1), uri(2), uri(3), uri(3), uri(4)]
    assert world.playlists["archive"]["track_uris"] == [uri(3), uri(4), uri(3), uri(5)]
    assert world.saved["phase"] == "forward"
    assert world.saved["plans"][1]["batches"][0]["state"] == "rejected"


def test_rate_limit_control_keeps_both_plans_and_does_no_io_before_the_retry_hint(world):
    from types import SimpleNamespace

    from shuffify.services.operations.journal import OperationDeferred

    runtime = Runtime(world)
    request = runtime.http._session.request

    def deferred(method, url, **kwargs):
        if method == "DELETE":
            return SimpleNamespace(status_code=429, headers={"Retry-After": "120"}, json=lambda: {})
        return request(method, url, **kwargs)

    runtime.http._session.request = deferred
    with pytest.raises(OperationDeferred):
        run(runtime, config())
    previous = deepcopy(world.trace)
    with pytest.raises(OperationDeferred):
        run(Runtime(world), config())
    assert world.trace == previous
    assert [method for method, _, _ in world.writes] == ["POST"]
    assert [plan["kind"] for plan in world.saved["plans"]] == ["add", "remove"]


def test_no_outer_observer_never_dispatches_either_phase(world):
    with pytest.raises(JobExecutionError, match="outer journal observer"):
        module().execute_unarchive(Runtime(world), config(), prepare_key="unarchive.v1")
    assert world.writes == []


@pytest.mark.parametrize("denied", ["target", "archive"])
def test_real_runtime_freshly_authorizes_both_move_resources_before_dispatch(routine, denied):
    from shuffify.services.operations.runtime import OperationRuntime
    from shuffify.services.playlist_service import PlaylistAccessError

    factory = routine.factory

    def api(user):
        value = factory(user)
        metadata = value.get_playlist

        def read(resource, **kwargs):
            assert kwargs.get("skip_cache") is True
            result = metadata(resource, **kwargs)
            if resource == denied:
                result["owner"] = {"id": "somebodyelse"}
            return result

        value.get_playlist = read
        return value

    row, _ = routine.enqueue("rotate", params={"target_size": 3})
    runtime = OperationRuntime(routine.repository, routine.repository.claim(operation_id=row.id), api_factory=api)
    captured = module().UnarchiveConfig(routine.user.id, "target", "archive", (uri(6),))
    with pytest.raises(PlaylistAccessError):
        run(runtime, captured)
    assert routine.world.writes == []
    assert routine.repository.get(row.id).journal.get("plans", []) == []


def test_real_runtime_verifies_all_pages_and_returns_only_local_action_data(routine):
    from shuffify.models.db import ActivityLog, PendingRaidTrack, PlaylistSnapshot
    from shuffify.services.operations.runtime import OperationRuntime

    requested = [uri(index + 10) for index in range(201)]
    routine.world.playlists["archive"]["track_uris"] = requested * 2
    factory, offsets = routine.factory, []

    def api(user):
        value = factory(user)

        def page(resource, limit=100, offset=0):
            values = routine.world.read(resource)["track_uris"]
            end = min(offset + limit, len(values))
            offsets.append((resource, offset))
            return {
                "items": [{"track": {"uri": item, "type": "track"}} for item in values[offset:end]],
                "offset": offset,
                "total": len(values),
                "next": f"https://fixture.invalid/?offset={end}" if end < len(values) else None,
            }

        value.get_playlist_items_raw = page
        return value

    row, _ = routine.enqueue("rotate", params={"target_size": 3})
    runtime = OperationRuntime(routine.repository, routine.repository.claim(operation_id=row.id), api_factory=api)
    captured = module().UnarchiveConfig(routine.user.id, "target", "archive", tuple(requested))
    result = run(runtime, captured)
    assert routine.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)] + requested
    assert routine.world.playlists["archive"]["track_uris"] == []
    assert ("archive", 400) in offsets and ("target", 200) in offsets
    assert len(routine.world.writes) == 6
    assert result["result"]["unarchived_count"] == 201
    assert result["result"]["removed_count"] == 402
    assert result["local_actions"]["history"]["before_uris"] == [uri(1), uri(2), uri(3)]
    assert result["local_actions"]["history"]["after_uris"] == routine.world.playlists["target"]["track_uris"]
    assert ActivityLog.query.count() == PendingRaidTrack.query.count() == PlaylistSnapshot.query.count() == 0
