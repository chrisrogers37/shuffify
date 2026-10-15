"""Workshop source finalizers never rewrite their already-saved target."""

import importlib
from copy import deepcopy
from dataclasses import replace

import pytest

from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.services.operations.journal import NeedsAttention
from tests.services.operations.test_collect_drip_executors import Runtime
from tests.services.operations.test_crash_recovery import Crash, World, uri
from tests.services.operations.test_routine_commands import routine as routine


def module():
    return importlib.import_module("shuffify.services.operations.executors.finalizers")


@pytest.fixture
def world():
    value = World([uri(1), uri(2), uri(1)])
    for resource, values in [("collection", [uri(1), uri(1), uri(3)]), ("archive", [uri(3), uri(3)])]:
        value.playlists[resource] = deepcopy(value.playlists["target"])
        value.playlists[resource]["track_uris"] = values
        value.playlists[resource]["metadata"]["name"] = resource.title()
    return value


def config(world, kind):
    finalizers = module()
    common = dict(
        user_id=1,
        target_playlist_id="target",
        source_playlist_id="collection" if kind == "raids" else "archive",
        linked_source_playlist_id="collection" if kind == "raids" else "archive",
        target_track_uris=tuple(world.playlists["target"]["track_uris"]),
    )
    if kind == "raids":
        return finalizers.FinalizeRaidsConfig(
            **common, pending_rows=(finalizers.FinalizeRow(17, 1, "target", uri(1), "pending"),)
        )
    if kind == "restore":
        return finalizers.FinalizeRestoreConfig(**common, track_uris=(uri(1),))
    return finalizers.ArchiveAppendConfig(
        **common,
        track_uris=(uri(1), uri(1), uri(3)),
        baseline_track_uris=tuple(world.playlists["archive"]["track_uris"]),
    )


def run(runtime, captured, kind):
    finalizers = module()
    name = {"raids": "finalize_raids", "append": "archive_append", "restore": "finalize_restore"}[kind]
    with runtime.journal.observe():
        return getattr(finalizers, "execute_" + name)(runtime, captured, prepare_key="finalizer.v1")


def test_pending_preparation_has_only_frozen_read_facts_and_exact_local_selection(world):
    finalizers = module()
    runtime, captured = Runtime(world), config(world, "raids")
    original = deepcopy(world.playlists)
    prepared = finalizers.prepare_finalize_raids(runtime, captured, prepare_key="finalizer.v1")
    assert world.playlists == original
    assert world.writes == []
    assert (world.saved or {}).get("plans", []) == []
    assert prepared["local_actions"]["pending_promotion"]["rows"] == [
        {"id": 17, "user_id": 1, "target_playlist_id": "target", "track_uri": uri(1), "status": "pending"},
    ]
    assert prepared["result"]["promoted_count"] == 1
    assert prepared["result"]["affected_count"] == 1  # Unique selected record, not two source occurrences.
    assert prepared["choices"]["source_affected_count"] == 2


@pytest.mark.parametrize("kind", ["raids", "restore", "append"])
def test_source_effects_are_ordered_and_target_is_always_readonly(world, kind):
    if kind == "restore":
        world.playlists["archive"]["track_uris"] = [uri(1), uri(3), uri(1)]
    original = deepcopy(world.playlists["target"])
    applied = run(Runtime(world), config(world, kind), kind)
    assert world.playlists["target"] == original
    assert all("/target/" not in path for _, path, _ in world.writes)
    assert applied["after"]["target"] == original
    if kind == "append":
        assert world.playlists["archive"]["track_uris"] == [uri(3), uri(3), uri(1), uri(1), uri(3)]
        assert applied["result"]["archived_count"] == applied["result"]["affected_count"] == 3
    else:
        source = "collection" if kind == "raids" else "archive"
        assert world.playlists[source]["track_uris"] == [uri(3)]
        assert applied["result"]["affected_count"] == (1 if kind == "raids" else 2)


@pytest.mark.parametrize("kind", ["raids", "restore", "append"])
def test_relinked_source_is_refused_before_any_spotify_read(world, kind):
    captured = replace(config(world, kind), linked_source_playlist_id="newsource")
    with pytest.raises(module().FinalizerRejected, match="linked source") as failure:
        run(Runtime(world), captured, kind)
    assert failure.value.code == "source_changed"
    assert world.trace == []


@pytest.mark.parametrize("kind", ["raids", "restore", "append"])
@pytest.mark.parametrize("actual", [(2, 1, 1), (1, 2), (1, 1, 2, 1)])
def test_saved_target_order_and_occurrences_must_match_exactly(world, kind, actual):
    captured = config(world, kind)
    world.playlists["target"]["track_uris"] = [uri(value) for value in actual]
    with pytest.raises(module().FinalizerRejected) as failure:
        run(Runtime(world), captured, kind)
    assert failure.value.code == "target_changed"
    assert world.writes == []


@pytest.mark.parametrize("kind", ["raids", "restore"])
def test_selected_membership_is_required_even_when_target_order_matches(world, kind):
    world.playlists["target"]["track_uris"] = [uri(2)]
    with pytest.raises(module().FinalizerRejected) as failure:
        run(Runtime(world), config(world, kind), kind)
    assert failure.value.code == "target_tracks_missing"
    assert world.writes == []


def test_null_link_pending_finalize_is_db_only_and_retains_exact_promoted_retry_rows(world):
    finalizers = module()
    captured = replace(
        config(world, "raids"),
        source_playlist_id=None,
        linked_source_playlist_id=None,
        pending_rows=(
            finalizers.FinalizeRow(19, 1, "target", uri(2), "promoted"),
            finalizers.FinalizeRow(17, 1, "target", uri(1), "pending"),
        ),
    )
    applied = run(Runtime(world), captured, "raids")
    assert set(applied["before"]) == {"target"}
    assert applied["result"]["promoted_count"] == 2
    assert applied["result"]["affected_count"] == 1
    assert applied["result"]["source_status"] == "not_required"
    assert [item["id"] for item in applied["local_actions"]["pending_promotion"]["rows"]] == [19, 17]
    assert applied["local_actions"]["pending_promotion"]["pending_ids"] == [17]
    assert world.writes == []


def test_already_desired_append_reports_no_affected_occurrences_and_does_not_append_again(world):
    captured = config(world, "append")
    world.playlists["archive"]["track_uris"] += list(captured.track_uris)
    applied = run(Runtime(world), captured, "append")
    assert applied["result"]["status"] == "not_required"
    assert applied["result"]["affected_count"] == 0
    assert applied["result"]["archived_count"] == len(captured.track_uris)
    assert world.writes == []


def test_append_refuses_an_ordered_baseline_mismatch(world):
    captured = config(world, "append")
    world.playlists["archive"]["track_uris"] = [uri(3)]
    with pytest.raises(module().FinalizerRejected) as failure:
        run(Runtime(world), captured, "append")
    assert failure.value.code == "source_changed"
    assert world.writes == []


@pytest.mark.parametrize("kind", ["raids", "restore", "append"])
@pytest.mark.parametrize(
    "resource,snapshot_only", [("target", False), ("target", True), ("source", False), ("source", True)]
)
def test_frozen_inputs_refuse_external_edits_before_source_write(world, kind, resource, snapshot_only):
    finalizers = module()
    captured, runtime = config(world, kind), Runtime(world)
    name = {"raids": "finalize_raids", "restore": "finalize_restore", "append": "archive_append"}[kind]
    getattr(finalizers, "prepare_" + name)(runtime, captured, prepare_key="finalizer.v1")
    changed = captured.source_playlist_id if resource == "source" else "target"
    if not snapshot_only:
        world.playlists[changed]["track_uris"].append(uri(9))
    world.playlists[changed]["snapshot_id"] = "external"
    with pytest.raises(NeedsAttention):
        run(runtime, captured, kind)
    assert world.writes == []


@pytest.mark.parametrize("kind", ["restore", "append"])
@pytest.mark.parametrize("size", [1, 99, 100, 101, 201])
def test_actual_http_batches_preserve_order_and_never_write_target(world, kind, size):
    values = tuple(uri(index + 10) for index in range(size))
    if kind == "append":
        captured = replace(config(world, kind), track_uris=values)
    else:
        world.playlists["target"]["track_uris"] = list(values)
        world.playlists["archive"]["track_uris"] = [uri(3)] + list(values) + list(values)
        captured = replace(config(world, kind), track_uris=values)
    applied = run(Runtime(world), captured, kind)
    assert len(world.writes) == (size + 99) // 100
    assert all("/archive/" in path for _, path, _ in world.writes)
    if kind == "append":
        assert world.playlists["archive"]["track_uris"] == [uri(3), uri(3)] + list(values)
        assert applied["result"]["affected_count"] == size
    else:
        assert world.playlists["archive"]["track_uris"] == [uri(3)]
        assert applied["result"]["affected_count"] == size * 2
        assert applied["result"]["restored_count"] == size


@pytest.mark.parametrize("kind", ["raids", "restore", "append"])
def test_applied_response_lost_recovery_does_not_repeat_the_source_effect(world, kind):
    values = tuple(uri(index + 10) for index in range(101))
    finalizers = module()
    if kind == "append":
        captured = replace(config(world, kind), track_uris=values)
    else:
        source = "collection" if kind == "raids" else "archive"
        world.playlists["target"]["track_uris"] = list(values)
        world.playlists[source]["track_uris"] = list(values) * 2
        captured = config(world, kind)
        captured = (
            replace(
                captured,
                pending_rows=tuple(
                    finalizers.FinalizeRow(index + 1, 1, "target", value, "pending")
                    for index, value in enumerate(values)
                ),
            )
            if kind == "raids"
            else replace(captured, track_uris=values)
        )
    point, crashed = world.point, []

    def lose(label):
        point(label)
        if label == "spotify.applied" and not crashed:
            crashed.append(True)
            raise Crash()

    world.point = lose
    with pytest.raises(Crash):
        run(Runtime(world), captured, kind)
    recovered = Runtime(world)
    assert recovered.journal.resume()["state"] == "succeeded"
    applied = run(recovered, captured, kind)
    assert len(world.writes) == 2
    assert all("/target/" not in path for _, path, _ in world.writes)
    if kind == "append":
        assert world.playlists["archive"]["track_uris"] == [uri(3), uri(3)] + list(values)
    else:
        assert world.playlists[captured.source_playlist_id]["track_uris"] == []
        assert applied["result"]["affected_count"] == (len(values) if kind == "raids" else len(values) * 2)


@pytest.mark.parametrize("kind", ["raids", "restore", "append"])
@pytest.mark.parametrize("changed_resource", ["target", "source"])
def test_input_edit_during_recovery_stops_remaining_source_batches_without_restoring_target(
    world, kind, changed_resource
):
    values = tuple(uri(index + 10) for index in range(101))
    finalizers = module()
    world.playlists["target"]["track_uris"] = list(values)
    captured = config(world, kind)
    if kind == "append":
        captured = replace(captured, track_uris=values)
    else:
        world.playlists[captured.source_playlist_id]["track_uris"] = list(values)
        captured = (
            replace(
                captured,
                pending_rows=tuple(
                    finalizers.FinalizeRow(index + 1, 1, "target", value, "pending")
                    for index, value in enumerate(values)
                ),
            )
            if kind == "raids"
            else replace(captured, track_uris=values)
        )
    point, stopped = world.point, []

    def stop(label):
        point(label)
        if label == "spotify.response" and not stopped:
            stopped.append(True)
            raise Crash()

    world.point = stop
    with pytest.raises(Crash):
        run(Runtime(world), captured, kind)
    before = deepcopy(world.writes)
    changed = "target" if changed_resource == "target" else captured.source_playlist_id
    world.playlists[changed].update(track_uris=[uri(99)], snapshot_id="external")
    recovered = Runtime(world)
    assert recovered.journal.resume()["state"] == "needs_attention"
    assert recovered.journal.compensate()["state"] == "needs_attention"
    assert world.writes == before
    assert "target" in world.saved["resources"]
    assert all(item["resource"] != "target" for item in recovered.journal.restore_plan()["resources"])


@pytest.mark.parametrize(
    "field,value", [("user_id", 2), ("target_playlist_id", "other"), ("status", "dismissed"), ("id", True)]
)
def test_foreign_or_invalid_pending_capture_is_rejected_before_reads(world, field, value):
    finalizers = module()
    captured = config(world, "raids")
    captured = replace(captured, pending_rows=(replace(captured.pending_rows[0], **{field: value}),))
    with pytest.raises((finalizers.FinalizerRejected, JobExecutionError)):
        run(Runtime(world), captured, "raids")
    assert world.trace == []


def test_unreserved_source_cannot_become_a_write_destination(world):
    runtime, captured = Runtime(world), config(world, "append")
    runtime.lease.targets = ("target",)
    with pytest.raises(JobExecutionError, match="unreserved"):
        run(runtime, captured, "append")
    assert world.trace == []


@pytest.mark.parametrize("denied", ["target", "collection"])
def test_real_runtime_authorizes_each_complete_resource_before_cleanup(routine, denied):
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

    row, _ = routine.enqueue("raid", direct=True)
    lease = routine.repository.claim(operation_id=row.id)
    runtime = OperationRuntime(routine.repository, lease, api_factory=api)
    captured = module().FinalizeRaidsConfig(
        routine.user.id,
        "target",
        "collection",
        "collection",
        tuple(routine.world.playlists["target"]["track_uris"]),
        (module().FinalizeRow(1, routine.user.id, "target", uri(1), "pending"),),
    )
    with pytest.raises(PlaylistAccessError):
        run(runtime, captured, "raids")
    assert routine.world.writes == []
    assert routine.repository.get(row.id).journal.get("plans", []) == []


def test_real_runtime_reads_every_page_of_201_selected_rows_and_leaves_domain_database_unchanged(routine):
    from shuffify.models.db import ActivityLog, PendingRaidTrack
    from shuffify.services.operations.runtime import OperationRuntime

    values = [uri(index + 10) for index in range(201)]
    routine.world.playlists["target"]["track_uris"] = values + [values[0]]
    routine.world.playlists["collection"]["track_uris"] = values * 2
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

    row, _ = routine.enqueue("raid", direct=True)
    runtime = OperationRuntime(routine.repository, routine.repository.claim(operation_id=row.id), api_factory=api)
    captured = module().FinalizeRaidsConfig(
        routine.user.id,
        "target",
        "collection",
        "collection",
        tuple(routine.world.playlists["target"]["track_uris"]),
        tuple(
            module().FinalizeRow(index + 1, routine.user.id, "target", value, "pending")
            for index, value in enumerate(values)
        ),
    )
    original = deepcopy(routine.world.playlists["target"])
    applied = run(runtime, captured, "raids")
    assert routine.world.playlists["target"] == original
    assert routine.world.playlists["collection"]["track_uris"] == []
    assert ("collection", 400) in offsets
    assert applied["result"]["promoted_count"] == applied["result"]["affected_count"] == 201
    assert applied["choices"]["source_affected_count"] == 402
    assert len(applied["local_actions"]["pending_promotion"]["rows"]) == 201
    assert ActivityLog.query.count() == PendingRaidTrack.query.count() == 0
    assert len(routine.world.writes) == 3


def test_already_promoted_pending_retry_counts_selection_but_has_no_remaining_effect(world):
    captured = config(world, "raids")
    captured = replace(captured, pending_rows=(replace(captured.pending_rows[0], status="promoted"),))
    world.playlists["collection"]["track_uris"] = [uri(3)]
    applied = run(Runtime(world), captured, "raids")
    assert applied["result"]["promoted_count"] == 1
    assert applied["result"]["affected_count"] == 0
    assert applied["result"]["status"] == "not_required"
    assert applied["result"]["database_status"] == "complete"
    assert applied["local_actions"]["pending_promotion"]["pending_ids"] == []
    assert world.writes == []


@pytest.mark.parametrize("kind", ["raids", "restore", "append"])
def test_null_or_same_target_source_never_grants_cleanup_authority(world, kind):
    captured = replace(config(world, kind), source_playlist_id="target", linked_source_playlist_id="target")
    with pytest.raises(module().FinalizerRejected) as failure:
        run(Runtime(world), captured, kind)
    assert failure.value.code == "invalid_source"
    assert world.trace == []
    if kind != "raids":
        captured = replace(captured, source_playlist_id=None, linked_source_playlist_id=None)
        with pytest.raises(module().FinalizerRejected) as failure:
            run(Runtime(world), captured, kind)
        assert failure.value.code == "source_changed"
        assert world.trace == []


def test_pending_selection_ids_cannot_be_duplicated_or_replaced_under_a_frozen_key(world):
    finalizers = module()
    runtime, captured = Runtime(world), config(world, "raids")
    repeated = replace(captured, pending_rows=captured.pending_rows * 2)
    with pytest.raises(finalizers.FinalizerRejected) as failure:
        run(runtime, repeated, "raids")
    assert failure.value.code == "invalid_track_selection"
    finalizers.prepare_finalize_raids(runtime, captured, prepare_key="finalizer.v1")
    changed = replace(captured, pending_rows=(replace(captured.pending_rows[0], id=23),))
    with pytest.raises(finalizers.FinalizerRejected) as failure:
        run(runtime, changed, "raids")
    assert failure.value.code == "configuration_changed"
    assert world.writes == []


def test_finalizer_rejects_missing_outer_observer_without_source_dispatch(world):
    finalizers = module()
    runtime, captured = Runtime(world), config(world, "append")
    with pytest.raises(JobExecutionError, match="outer journal observer"):
        finalizers.execute_archive_append(runtime, captured, prepare_key="finalizer.v1")
    assert world.writes == []


def test_rate_limit_control_is_not_converted_to_a_success_result_or_retry_before_due(world):
    from types import SimpleNamespace

    from shuffify.services.operations.journal import OperationDeferred

    runtime, captured = Runtime(world), config(world, "append")
    runtime.http._session.request = lambda *args, **kwargs: SimpleNamespace(
        status_code=429,
        headers={"Retry-After": "120"},
        json=lambda: {},
    )
    with pytest.raises(OperationDeferred):
        run(runtime, captured, "append")
    before = deepcopy(world.trace)
    with pytest.raises(OperationDeferred):
        run(Runtime(world), captured, "append")
    assert world.trace == before
    assert world.writes == []
    assert world.saved["plans"][0]["batches"][0]["state"] == "rejected"


def test_verified_cleanup_can_resume_only_the_same_frozen_database_selection(world):
    captured = config(world, "raids")
    first = run(Runtime(world), captured, "raids")
    written = deepcopy(world.writes)
    # A future required local receipt may fail after this verified result. It
    # must retry these exact rows without rediscovering an inbox or replaying DELETE.
    recovered = Runtime(world)
    assert recovered.journal.resume()["state"] == "succeeded"
    replayed = run(recovered, captured, "raids")
    assert replayed["local_actions"] == first["local_actions"]
    assert replayed["result"] == first["result"]
    assert world.writes == written
    assert replayed["local_actions"]["pending_promotion"]["pending_ids"] == [17]
