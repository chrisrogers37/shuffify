"""Workshop preparation stays pure and durable replay retains its original provenance."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask.sessions import SecureCookieSession
from requests import Response

from shuffify.models.db import ActivityLog, PlaylistSnapshot, TrackLock, User, UserSettings, db
from shuffify.services.operations.history import HistoryError, HistoryStore, get_history_scope
from shuffify.services.operations.journal import NeedsAttention, OperationAborted, OperationDeferred
from shuffify.services.operations.repository import LeaseLost, OperationRepository
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.workshop_commit_service import WorkshopCommitService
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.http_client import SpotifyHTTPClient
from tests.services.test_workshop_commit_service import A, B, C, PlaylistAPI

MODULE = "shuffify.services.workshop_commit_service"


@pytest.fixture
def context():
    api = PlaylistAPI([A, A, B])

    def occurrences(page):
        for index, entry in enumerate(page["items"]):
            entry["added_at"] = f"2026-01-0{page['offset'] + index + 1}T00:00:00Z"
        return page

    api.page_transform = occurrences
    with (
        patch(f"{MODULE}.PlaylistSnapshotService", autospec=True) as snapshots,
        patch(f"{MODULE}.TrackLockService", autospec=True) as locks,
        patch(f"{MODULE}.ActivityLogService", autospec=True) as activity,
    ):
        yield SimpleNamespace(
            api=api, user=SimpleNamespace(id=7, spotify_id="alice"),
            history=SecureCookieSession(), service=WorkshopCommitService(api),
            snapshots=snapshots, locks=locks, activity=activity,
        )


def prepare(context, baseline=None, desired=None):
    return context.service.prepare_commit(
        "playlist", context.user, [A, A, B] if baseline is None else baseline,
        [B, A, A] if desired is None else desired,
    )


def test_preparation_freezes_json_and_duplicate_occurrence_metadata_without_effects(context):
    baseline, desired = [A, A, B], [B, A, A]
    prepared = prepare(context, baseline, desired)
    assert json.loads(json.dumps(prepared, allow_nan=False)) == prepared
    assert prepared["before"]["track_uris"] == [A, A, B]
    assert [track["added_at"] for track in prepared["before"]["tracks"]] == [
        "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z",
    ]
    assert prepared["before"]["snapshot_id"] == "1"
    assert prepared["before"]["metadata"] == {"name": "Fictional mix", "public": None, "description": None}
    assert prepared["desired_track_uris"] == desired
    assert prepared["changed"] is True
    baseline.clear()
    desired.append(C)
    assert prepared["before"]["track_uris"] == [A, A, B]
    assert prepared["desired_track_uris"] == [B, A, A]
    assert context.api.writes == []
    assert context.history == {}
    context.snapshots.create_snapshot.assert_not_called()
    context.activity.log.assert_not_called()
    context.locks.safe_reconcile_positions.assert_not_called()


def test_prepare_already_applied_precedes_stale_baseline(context):
    prepared = prepare(context, baseline=[C], desired=[A, A, B])
    result = context.service.apply_prepared(prepared, context.user, context.history)
    assert prepared["changed"] is False
    assert result.target_status == "already_applied"
    assert result.confirmed_batches == result.total_batches == 0
    assert result.to_dict()["saved_track_uris"] == [A, A, B]
    assert context.api.writes == []
    context.snapshots.create_snapshot.assert_not_called()


def test_stale_draft_preparation_retains_refresh_fields_without_effects(context):
    prepared = prepare(context, baseline=[A, B])
    context.api.uris = [C]
    result = context.service.apply_prepared(prepared, context.user, context.history)
    assert result.error_code == "stale_draft"
    assert result.http_status == 409
    assert result.to_dict()["current_track_uris"] == [A, A, B]
    assert result.requires_refresh is True
    assert context.api.writes == []
    assert context.history == {}


@pytest.mark.parametrize("stage", ["read", "write", "verification", "history", "snapshot"])
@pytest.mark.parametrize("signal", [
    LeaseLost("lease gone"), NeedsAttention("external_edit"), OperationAborted("stopped"),
    OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=60)),
])
def test_worker_controls_are_never_converted_to_workshop_outcomes(context, stage, signal):
    prepared = None if stage == "read" else prepare(context)

    def fail(*args, **kwargs):
        raise signal

    if stage == "read":
        context.api.get_playlist_items_raw = fail
    elif stage == "write":
        context.api.write_hook = fail
    elif stage == "verification":
        def write(uris):
            context.api.uris = list(uris)
            context.api.get_playlist_items_raw = fail
            return True
        context.api.write_hook = write
    elif stage == "history":
        context.locks.safe_reconcile_positions.side_effect = signal
    else:
        context.snapshots.is_auto_snapshot_enabled.side_effect = signal
    with pytest.raises(type(signal)) as raised:
        if stage == "read":
            prepare(context)
        else:
            context.service.apply_prepared(prepared, context.user, context.history)
    assert raised.value is signal


class Crash(BaseException):
    """An interrupted process must bypass ordinary service exception handling."""


@pytest.fixture
def queued(db_app):
    user = User(spotify_id="alice")
    db.session.add(user)
    db.session.flush()
    db.session.add(UserSettings(user_id=user.id, auto_snapshot_enabled=True))
    db.session.add(TrackLock(user_id=user.id, spotify_playlist_id="playlist", track_uri=A, position=0))
    db.session.commit()
    world = PlaylistAPI([A, A, B])
    world.status = 200
    world.requests = []
    world.after_apply = lambda: None
    api = object.__new__(SpotifyAPI)
    api._cache, api._user_id = None, None
    api._ensure_valid_token = lambda: None
    api.get_playlist = world.get_playlist
    api.get_playlist_items_raw = world.get_playlist_items_raw
    api._http = SpotifyHTTPClient("fictional-access-token")

    def transport(method, url, **kwargs):
        world.requests.append((method, url, deepcopy(kwargs["json"])))
        response = Response()
        response.status_code = world.status(len(world.requests)) if callable(world.status) else world.status
        if response.status_code >= 400:
            response.headers["Retry-After"] = "120"
            response._content = b'{"error":{"message":"Please wait"}}'
            return response
        assert url.endswith("/playlists/playlist/items")
        if method == "PUT":
            world.uris = list(kwargs["json"]["uris"])
        else:
            assert method == "POST"
            world.uris.extend(kwargs["json"]["uris"])
        world.revision += 1
        world.after_apply()
        response._content = json.dumps({"snapshot_id": str(world.revision)}).encode()
        return response

    api._http._session.request = transport
    repository, store, scope = OperationRepository(), HistoryStore(), get_history_scope({})

    def accept(baseline=None, desired=None):
        command = {
            "playlist_id": "playlist", "baseline_track_uris": list(world.uris if baseline is None else baseline),
            "track_uris": [B, A, A] if desired is None else desired,
        }
        operation = repository.enqueue(
            kind="workshop_commit", user_id=user.id, targets=["playlist"],
            payload={"version": 1, "command": command,
                     "history": store.capture(user.id, scope, ["playlist"])},
        )
        lease = repository.claim(operation_id=operation.id)
        return operation, lease

    def runtime(lease):
        return OperationRuntime(repository, lease, api_factory=lambda _user: api)

    yield SimpleNamespace(world=world, user=user, api=api, repository=repository, store=store,
                          scope=scope, accept=accept, runtime=runtime)
    api.close()


def run(runtime):
    from shuffify.services.operations.commands.workshop import workshop_commit

    with runtime.journal.observe():
        return workshop_commit(runtime)


def test_durable_save_replays_after_lost_step_output_with_original_history_and_local_effects_once(queued):
    operation, lease = queued.accept()
    runtime = queued.runtime(lease)
    checkpoint = runtime.checkpoint

    def crash_before_step_output(document):
        if "direct.apply.v1" in document.get("runtime", {}).get("steps", {}):
            raise Crash()
        return checkpoint(document)

    runtime.checkpoint = crash_before_step_output
    with pytest.raises(Crash):
        run(runtime)
    assert queued.world.uris == [B, A, A]
    assert len(queued.world.requests) == 1
    result = run(queued.runtime(lease))
    assert result.state == "succeeded"
    assert result.result["target_status"] == "saved"
    assert result.result["playlist_state"] == {"states": [[A, A, B], [B, A, A]], "current_index": 1}
    assert len(queued.world.requests) == 1
    assert PlaylistSnapshot.query.filter_by(job_execution_id=operation.id).count() == 1
    assert ActivityLog.query.filter_by(user_id=queued.user.id).count() == 1
    # The existing URI-scoped lock service maps a duplicate to its final occurrence.
    assert TrackLock.query.filter_by(user_id=queued.user.id).one().position == 2
    latest = queued.runtime(lease)
    latest.stage_history("succeeded")
    queued.repository.finish(lease, "succeeded", result=result.result)
    assert queued.store.load(queued.user.id, queued.scope, "playlist")[0] == result.result["playlist_state"]


def test_prepared_save_refuses_external_edit_in_journal_freeze_without_dispatch(queued):
    _operation, lease = queued.accept()
    runtime = queued.runtime(lease)
    runtime.prepare("direct.action.v1", lambda: WorkshopCommitService(queued.api).prepare_commit(
        "playlist", runtime.user, [A, A, B], [B, A, A],
    ))
    queued.world.uris, queued.world.revision = [C], 2
    with pytest.raises(NeedsAttention, match="prepared_baseline_changed"):
        run(queued.runtime(lease))
    assert queued.world.uris == [C]
    assert queued.world.requests == []
    assert PlaylistSnapshot.query.count() == ActivityLog.query.count() == 0


@pytest.mark.parametrize("desired", [[], [C], [C] * 101, [C] * 250])
def test_durable_batches_include_empty_put_and_report_frozen_journal_counts(queued, desired):
    _operation, lease = queued.accept(desired=desired)
    result = run(queued.runtime(lease))
    expected = max(1, (len(desired) + 99) // 100)
    assert result.state == "succeeded"
    assert result.result["saved_track_uris"] == desired
    assert result.result["confirmed_batches"] == result.result["total_batches"] == expected
    assert len(queued.world.requests) == expected
    assert queued.world.requests[0][0] == "PUT"
    assert all(request[0] == "POST" for request in queued.world.requests[1:])


def test_durable_already_applied_has_zero_batches_snapshot_activity_and_stale_baseline_is_ignored(queued):
    _operation, lease = queued.accept(baseline=[C], desired=[A, A, B])
    result = run(queued.runtime(lease))
    assert result.state == "succeeded"
    assert result.result["target_status"] == "already_applied"
    assert result.result["confirmed_batches"] == result.result["total_batches"] == 0
    assert queued.world.requests == []
    assert PlaylistSnapshot.query.count() == ActivityLog.query.count() == 0


def test_real_persisted_rate_limit_passes_service_and_reentry_performs_no_io_before_hint(queued):
    operation, lease = queued.accept()
    queued.world.status = 429
    with pytest.raises(OperationDeferred):
        run(queued.runtime(lease))
    saved = queued.repository.get(operation.id).journal
    assert saved["plans"][0]["batches"][0]["retry_after"] == 120
    assert "direct.apply.v1" not in saved.get("runtime", {}).get("steps", {})
    assert queued.world.uris == [A, A, B]
    prior = (len(queued.world.reads), len(queued.world.requests))
    with pytest.raises(OperationDeferred):
        run(queued.runtime(lease))
    assert (len(queued.world.reads), len(queued.world.requests)) == prior


def test_optional_bookkeeping_failure_does_not_compensate_or_repeat_verified_write(queued):
    _operation, lease = queued.accept()
    with patch("shuffify.services.operations.bookkeeping.OperationBookkeeping.activity", autospec=True,
               side_effect=RuntimeError("optional database failure")) as activity:
        result = run(queued.runtime(lease))
        again = run(queued.runtime(lease))
    assert result == again
    assert result.state == "succeeded"
    assert result.result["target_status"] == "saved"
    assert result.result["bookkeeping"]["activity"] == "failed"
    assert activity.call_count == 1
    assert len(queued.world.requests) == 1


@pytest.mark.parametrize("stage", ["prepared", "acknowledged", "cached", "snapshot", "locks", "activity"])
def test_crash_after_each_durable_boundary_keeps_one_effect_and_original_before(queued, stage):
    _operation, lease = queued.accept()
    runtime = queued.runtime(lease)
    checkpoint, local_step = runtime.checkpoint, runtime.local_step

    def interrupted_checkpoint(document):
        result = checkpoint(document)
        state = document.get("runtime", {})
        if stage == "prepared" and "direct.action.v1" in state.get("prepared", {}):
            raise Crash()
        if stage == "acknowledged" and any(
            item["state"] == "acknowledged" for plan in document.get("plans", []) for item in plan["batches"]
        ):
            raise Crash()
        if stage == "cached" and "direct.apply.v1" in state.get("steps", {}):
            raise Crash()
        return result

    def interrupted_local(name, callback):
        result = local_step(name, callback)
        if name == f"workshop.{stage}.v1":
            raise Crash()
        return result

    runtime.checkpoint, runtime.local_step = interrupted_checkpoint, interrupted_local
    with pytest.raises(Crash):
        run(runtime)
    result = run(queued.runtime(lease))
    assert result.state == "succeeded"
    assert result.result["target_status"] == "saved"
    assert result.result["playlist_state"] == {"states": [[A, A, B], [B, A, A]], "current_index": 1}
    assert len(queued.world.requests) == 1
    assert PlaylistSnapshot.query.count() == ActivityLog.query.count() == 1


def test_lost_response_after_actual_effect_reconciles_without_second_write(queued):
    operation, lease = queued.accept()

    def interrupted():
        raise Crash()

    queued.world.after_apply = interrupted
    with pytest.raises(Crash):
        run(queued.runtime(lease))
    assert queued.repository.get(operation.id).journal["plans"][0]["batches"][0]["state"] == "dispatched"
    assert queued.world.uris == [B, A, A]
    queued.world.after_apply = lambda: None
    result = run(queued.runtime(lease))
    assert result.result["target_status"] == "saved"
    assert result.result["playlist_state"]["states"] == [[A, A, B], [B, A, A]]
    assert len(queued.world.requests) == 1


def test_verified_save_with_failed_history_keeps_saved_facts_and_stages_no_history(queued):
    operation, lease = queued.accept()
    runtime = queued.runtime(lease)
    with patch(f"{MODULE}.StateService.record_verified_change", autospec=True, side_effect=RuntimeError("history")):
        result = run(runtime)
    assert result.result["target_status"] == "saved"
    assert result.result["saved_track_uris"] == [B, A, A]
    assert result.result["bookkeeping"]["history"] == "failed"
    assert queued.world.uris == [B, A, A]
    assert len(queued.world.requests) == 1
    assert queued.repository.get(operation.id).journal["runtime"]["history_unconfirmed"] is True
    with pytest.raises(HistoryError):
        queued.runtime(lease).stage_history("succeeded")
    assert queued.store.load(queued.user.id, queued.scope, "playlist") == (None, None)


@pytest.mark.parametrize("version", [True, 1.0, "1", None])
def test_invalid_envelope_is_rejected_before_history_or_api(queued, version):
    _operation, lease = queued.accept()
    runtime = queued.runtime(lease)
    runtime.operation.payload = {**runtime.operation.payload, "version": version}
    from shuffify.services.operations.commands.workshop import workshop_commit

    with pytest.raises(ValueError):
        workshop_commit(runtime)
    assert runtime._history_loaded is False
    assert queued.world.reads == queued.world.requests == []


@pytest.mark.parametrize("status", [400, 401, 403])
def test_durable_later_batch_rejection_preserves_confirmed_partial_counts(queued, status):
    _operation, lease = queued.accept(desired=[C] * 101)
    queued.world.status = lambda count: 200 if count == 1 else status
    with pytest.raises(OperationAborted) as aborted:
        run(queued.runtime(lease))
    result = aborted.value.result
    assert result["target_status"] == "partial"
    assert result["error_code"] == "partial_write"
    assert result["confirmed_batches"] == 1
    assert result["total_batches"] == 2
    assert "playlist_state" not in result
    assert "saved_track_uris" not in result


def test_target_binding_is_validated_before_runtime_work():
    from shuffify.services.operations.commands.workshop import workshop_commit
    from tests.services.operations.test_direct_command_guards import GuardRuntime

    runtime = GuardRuntime({"version": 1, "command": {
        "playlist_id": "playlist", "baseline_track_uris": [], "track_uris": [],
    }}, targets=["other"])
    with pytest.raises(ValueError, match="target"):
        workshop_commit(runtime)
