"""Verified direct writes survive local publication failures without replay."""
# ruff: noqa: F811

from copy import deepcopy

import pytest
from sqlalchemy.exc import OperationalError

from shuffify.services.operations.commands.workshop import workshop_commit
from shuffify.services.operations.history import HistoryStore
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.state_service import StateService
from tests.services.operations.test_crash_recovery import uri
from tests.services.operations.test_direct_worker_integration import SCOPE, accept, setup, worker_for  # noqa: F401


@pytest.fixture(params=["shuffle", "workshop_commit", "undo", "snapshot_restore"])
def direct(request, setup, monkeypatch):
    app, user_id, repository, world, algorithms = setup
    before = list(world.playlists["target"]["track_uris"])
    desired = [uri(2), uri(1), uri(1)]
    monkeypatch.setattr("shuffify.services.live_playlist_service.ShuffleService.execute", lambda *a, **k: desired)
    legacy = {"target": {"states": [desired, before], "current_index": 1}}
    command = (
        {"algorithm": "BasicShuffle", "params": {}}
        if request.param == "shuffle"
        else {"baseline_track_uris": before, "track_uris": desired}
        if request.param == "workshop_commit"
        else {"selection": legacy["target"]}
        if request.param == "undo"
        else {"snapshot": {"id": 17, "user_id": user_id, "playlist_id": "target", "track_uris": desired}}
    )
    row = accept(repository, user_id, request.param, command, legacy=legacy if request.param == "undo" else None)
    runner = worker_for(app, repository, world)
    runner.handlers["workshop_commit"] = workshop_commit
    return app, user_id, repository, world, before, desired, row, runner, request.param


@pytest.mark.parametrize("fault", ["history_marker", "target_output"])
def test_verified_direct_effect_is_not_compensated_for_local_publication_outage(direct, monkeypatch, fault):
    app, user_id, repository, world, before, desired, row, runner, kind = direct
    checkpoint, failures = OperationRuntime.checkpoint, []

    def unavailable_history(*args, **kwargs):
        raise ValueError("private history unavailable")

    if fault == "history_marker":
        monkeypatch.setattr(StateService, "record_verified_change", unavailable_history)
        monkeypatch.setattr(StateService, "confirm_undo", unavailable_history)

    def unavailable(self, document):
        runtime = document.get("runtime", {})
        key = "direct.apply.v1" if kind == "workshop_commit" else "direct.target.v1"
        boundary = runtime.get("history_unconfirmed") if fault == "history_marker" else key in runtime.get("steps", {})
        if boundary and not failures:
            assert world.playlists["target"]["track_uris"] == desired
            assert len(world.writes) == 1
            failures.append(deepcopy(document))
            raise OperationalError("UPDATE execution journal", {}, Exception("private local database outage"))
        return checkpoint(self, document)

    monkeypatch.setattr(OperationRuntime, "checkpoint", unavailable)
    assert runner.run_once(row.id) is True
    completed = repository.get(row.id)
    assert len(failures) == 1
    assert completed.state == "needs_attention"
    body = completed.journal["result"]
    if kind == "workshop_commit":
        assert body["target_status"] == "saved"
        assert body["confirmed_batches"] == body["total_batches"] == 1
        assert body["current_track_uris"] == desired
    else:
        resource = body["operation"]["resources"][0]
        assert resource["status"] == "complete"
        assert resource["current_track_uris"] == desired
        assert resource["resource"] == "target"
    assert "playlist_state" not in body
    assert body["retryable"] is False
    assert "public_result" not in body
    assert "before" not in body
    assert "private" not in str(body)
    assert world.playlists["target"]["track_uris"] == desired
    assert len(world.writes) == 1
    assert HistoryStore(repository.engine).load(user_id, SCOPE, "target") == (None, None)


def test_history_failure_with_a_successful_marker_keeps_verified_direct_effect(direct, monkeypatch):
    app, user_id, repository, world, before, desired, row, runner, kind = direct

    def unavailable(*args, **kwargs):
        raise ValueError("private history unavailable")

    monkeypatch.setattr(StateService, "record_verified_change", unavailable)
    monkeypatch.setattr(StateService, "confirm_undo", unavailable)
    assert runner.run_once(row.id) is True
    completed = repository.get(row.id)
    assert completed.state == "needs_attention"
    assert world.playlists["target"]["track_uris"] == desired
    assert len(world.writes) == 1
    assert completed.journal["runtime"]["history_unconfirmed"] is True
    assert HistoryStore(repository.engine).load(user_id, SCOPE, "target") == (None, None)


def test_verified_visibility_output_checkpoint_outage_does_not_revert_metadata(setup, monkeypatch):
    app, user_id, repository, world, algorithms = setup
    row = accept(repository, user_id, "visibility", {"public": True})
    runner = worker_for(app, repository, world)
    checkpoint, failures = OperationRuntime.checkpoint, []

    def unavailable(self, document):
        if "visibility.target.v1" in document.get("runtime", {}).get("steps", {}) and not failures:
            assert world.playlists["target"]["metadata"]["public"] is True
            failures.append(True)
            raise OperationalError("UPDATE visibility output", {}, Exception("private database outage"))
        return checkpoint(self, document)

    monkeypatch.setattr(OperationRuntime, "checkpoint", unavailable)
    assert runner.run_once(row.id) is True
    completed = repository.get(row.id)
    assert failures == [True]
    assert completed.state == "needs_attention"
    assert world.playlists["target"]["metadata"]["public"] is True
    assert len(world.writes) == 1


def test_rich_shuffle_projection_failure_after_provider_proof_is_guarded(setup, monkeypatch):
    app, user_id, repository, world, _ = setup
    desired = [uri(2), uri(1), uri(1)]
    monkeypatch.setattr("shuffify.services.live_playlist_service.ShuffleService.execute", lambda *a, **k: desired)
    row = accept(repository, user_id, "shuffle", {"algorithm": "BasicShuffle", "params": {}})
    runner = worker_for(app, repository, world)
    from shuffify.services.live_playlist_service import LivePlaylistService

    original = LivePlaylistService._playlist

    def unavailable(*args, **kwargs):
        if world.writes:
            raise ValueError("private rich projection error")
        return original(*args, **kwargs)

    monkeypatch.setattr(LivePlaylistService, "_playlist", unavailable)
    assert runner.run_once(row.id) is True
    assert repository.get(row.id).state == "needs_attention"
    assert len(world.writes) == 1
    assert world.playlists["target"]["track_uris"] == desired


@pytest.mark.parametrize("receipt", ["output", "acceptance"])
def test_lost_publication_reply_recovers_durable_result_without_another_write(direct, monkeypatch, receipt):
    from shuffify.models.db import ActivityLog

    app, user_id, repository, world, before, desired, row, runner, kind = direct
    checkpoint, local_step, failures = OperationRuntime.checkpoint, repository.local_step, []

    def lost_output(runtime, document):
        value = checkpoint(runtime, document)
        key = "direct.apply.v1" if kind == "workshop_commit" else "direct.target.v1"
        if receipt == "output" and key in document.get("runtime", {}).get("steps", {}) and not failures:
            failures.append(True)
            raise OperationalError("lost output reply", {}, Exception("private detail"))
        return value

    def lost_acceptance(lease, name, callback, **options):
        value = local_step(lease, name, callback, **options)
        if receipt == "acceptance" and name == "accept.direct.publication.v1" and not failures:
            failures.append(True)
            raise OperationalError("lost acceptance reply", {}, Exception("private detail"))
        return value

    monkeypatch.setattr(OperationRuntime, "checkpoint", lost_output)
    monkeypatch.setattr(repository, "local_step", lost_acceptance)
    assert runner.run_once(row.id) is True
    assert failures == [True]
    completed = repository.get(row.id)
    assert completed.state == "succeeded"
    assert world.playlists["target"]["track_uris"] == desired
    assert len(world.writes) == 1
    entry, revision = HistoryStore(repository.engine).load(user_id, SCOPE, "target")
    assert revision == row.id
    assert entry["states"][entry["current_index"]] == desired
    assert ActivityLog.query.count() == int(kind != "undo")
    assert len(completed.journal["boundaries"]["accepted"]) == 1
    assert "private" not in str(completed.journal["result"])


def test_publication_outage_keeps_verified_operation_recoverable_without_compensation(direct, monkeypatch):
    from tests.services.operations.test_direct_worker_integration import expire

    app, user_id, repository, world, before, desired, row, runner, kind = direct
    checkpoint, get, blocked = OperationRuntime.checkpoint, repository.get, []

    def unavailable(self, document):
        key = "direct.apply.v1" if kind == "workshop_commit" else "direct.target.v1"
        if key in document.get("runtime", {}).get("steps", {}) and not blocked:
            blocked.append(True)
            raise OperationalError("output outage", {}, Exception("private detail"))
        return checkpoint(self, document)

    def unavailable_read(*args, **kwargs):
        if blocked:
            raise OperationalError("database outage", {}, Exception("private detail"))
        return get(*args, **kwargs)

    monkeypatch.setattr(OperationRuntime, "checkpoint", unavailable)
    monkeypatch.setattr(repository, "get", unavailable_read)
    assert runner.run_once(row.id) is False
    assert world.playlists["target"]["track_uris"] == desired
    assert len(world.writes) == 1
    monkeypatch.setattr(OperationRuntime, "checkpoint", checkpoint)
    monkeypatch.setattr(repository, "get", get)
    assert repository.get(row.id).state == "running"
    expire(row.id)
    assert runner.run_once(row.id) is True
    assert repository.get(row.id).state == "succeeded"
    assert world.playlists["target"]["track_uris"] == desired
    assert len(world.writes) == 1


@pytest.mark.parametrize("signal", ["lease", "defer", "abort"])
def test_controls_after_verified_target_proof_keep_their_direction(direct, monkeypatch, signal):
    from datetime import datetime, timedelta, timezone

    from shuffify.services.operations.journal import OperationAborted, OperationDeferred
    from shuffify.services.operations.repository import LeaseLost
    from tests.services.operations.test_direct_worker_integration import expire

    app, user_id, repository, world, before, desired, row, runner, kind = direct
    protect, stopped = OperationRuntime.protect_verified, []

    def interrupted(self, name, body):
        protect(self, name, body)
        if not stopped:
            stopped.append(True)
            if signal == "lease":
                raise LeaseLost("lost fence")
            if signal == "defer":
                raise OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=1))
            raise OperationAborted("explicit_abort")

    monkeypatch.setattr(OperationRuntime, "protect_verified", interrupted)
    assert runner.run_once(row.id) is (signal == "abort")
    if signal == "abort":
        assert repository.get(row.id).state == "rolled_back"
        assert world.playlists["target"]["track_uris"] == before
        assert len(world.writes) == 2
    else:
        assert world.playlists["target"]["track_uris"] == desired
        assert len(world.writes) == 1
        monkeypatch.setattr(OperationRuntime, "protect_verified", protect)
        expire(row.id)
        assert runner.run_once(row.id) is True
        assert repository.get(row.id).state == "succeeded"
        assert len(world.writes) == 1


@pytest.mark.parametrize("lost_ack", [False, True])
def test_visibility_publication_receipt_failure_retains_exact_metadata(setup, monkeypatch, lost_ack):
    app, user_id, repository, world, _ = setup
    row = accept(repository, user_id, "visibility", {"public": True})
    runner = worker_for(app, repository, world)
    checkpoint, failed = OperationRuntime.checkpoint, []

    def unavailable(self, document):
        boundary = "visibility.target.v1" in document.get("runtime", {}).get("steps", {})
        if boundary and not failed:
            failed.append(True)
            if lost_ack:
                checkpoint(self, document)
            raise OperationalError("visibility publication", {}, Exception("private detail"))
        return checkpoint(self, document)

    monkeypatch.setattr(OperationRuntime, "checkpoint", unavailable)
    assert runner.run_once(row.id) is True
    result = repository.get(row.id)
    assert result.state == ("succeeded" if lost_ack else "needs_attention")
    assert len(world.writes) == 1
    assert world.playlists["target"]["metadata"]["public"] is True
    assert result.journal["result"]["public"] is True
    assert result.journal["result"]["operation"]["resources"][0]["status"] == "complete"
    assert "private detail" not in str(result.journal["result"])


@pytest.mark.parametrize("stored_output", [False, True])
def test_recovery_checkpoint_failure_after_fresh_provider_proof_never_compensates(direct, monkeypatch, stored_output):
    from tests.services.operations.test_crash_recovery import Crash
    from tests.services.operations.test_direct_worker_integration import expire

    app, user_id, repository, world, before, desired, row, runner, kind = direct
    checkpoint, stopped = OperationRuntime.checkpoint, []

    def crash(self, document):
        key = "direct.apply.v1" if kind == "workshop_commit" else "direct.target.v1"
        if key in document.get("runtime", {}).get("steps", {}) and not stopped:
            stopped.append(True)
            if stored_output:
                checkpoint(self, document)
            raise Crash()
        return checkpoint(self, document)

    monkeypatch.setattr(OperationRuntime, "checkpoint", crash)
    with pytest.raises(Crash):
        runner.run_once(row.id)
    expire(row.id)
    failures = []

    def unavailable(self, document):
        if self.lease.recovering and document.get("status") == "succeeded" and not failures:
            assert world.playlists["target"]["track_uris"] == desired
            failures.append(True)
            raise OperationalError("recovery proof receipt", {}, Exception("private detail"))
        return checkpoint(self, document)

    monkeypatch.setattr(OperationRuntime, "checkpoint", unavailable)
    assert runner.run_once(row.id) is True
    assert failures == [True]
    assert repository.get(row.id).state == "needs_attention"
    assert world.playlists["target"]["track_uris"] == desired
    assert len(world.writes) == 1


@pytest.mark.parametrize("checkpoint_failure", [False, True])
def test_external_edit_after_service_proof_invalidates_retained_current_claims(direct, monkeypatch, checkpoint_failure):
    app, user_id, repository, world, before, desired, row, runner, kind = direct
    protect, checkpoint, edited, failed = OperationRuntime.protect_verified, OperationRuntime.checkpoint, [], []

    def edit_after_proof(self, name, body, **options):
        protect(self, name, body, **options)
        if not edited:
            edited.append(True)
            world.playlists["target"].update(track_uris=[uri(99)], snapshot_id="external-edit")

    def failed_conflict(self, document):
        if checkpoint_failure and document.get("status") == "needs_attention" and not failed:
            failed.append(True)
            raise OperationalError("conflict checkpoint", {}, Exception("private detail"))
        return checkpoint(self, document)

    monkeypatch.setattr(OperationRuntime, "protect_verified", edit_after_proof)
    monkeypatch.setattr(OperationRuntime, "checkpoint", failed_conflict)
    assert runner.run_once(row.id) is True
    result = repository.get(row.id)
    assert result.state == "needs_attention"
    assert world.playlists["target"]["track_uris"] == [uri(99)]
    assert len(world.writes) == 1
    body = result.journal["result"]
    assert body["error_code"] == "needs_attention"
    assert "playlist_state" not in body
    assert "playlist" not in body
    assert "current_track_uris" not in body
    assert "current_tracks" not in body
    assert "saved_track_uris" not in body
    assert all("current_track_uris" not in resource for resource in body.get("operation", {}).get("resources", []))
    assert HistoryStore(repository.engine).load(user_id, SCOPE, "target") == (None, None)


@pytest.mark.parametrize("checkpoint_failure", [False, True])
def test_metadata_conflict_after_proof_never_claims_old_visibility(setup, monkeypatch, checkpoint_failure):
    app, user_id, repository, world, _ = setup
    row = accept(repository, user_id, "visibility", {"public": True})
    runner = worker_for(app, repository, world)
    protect, checkpoint, edited, failed = OperationRuntime.protect_verified, OperationRuntime.checkpoint, [], []

    def edit_after_proof(self, name, body, **options):
        protect(self, name, body, **options)
        if not edited:
            edited.append(True)
            world.playlists["target"]["metadata"]["public"] = False
            world.playlists["target"]["snapshot_id"] = "external-edit"

    def failed_conflict(self, document):
        if checkpoint_failure and document.get("status") == "needs_attention" and not failed:
            failed.append(True)
            raise OperationalError("metadata conflict checkpoint", {}, Exception("private detail"))
        return checkpoint(self, document)

    monkeypatch.setattr(OperationRuntime, "protect_verified", edit_after_proof)
    monkeypatch.setattr(OperationRuntime, "checkpoint", failed_conflict)
    assert runner.run_once(row.id) is True
    result = repository.get(row.id)
    assert result.state == "needs_attention"
    assert world.playlists["target"]["metadata"]["public"] is False
    assert len(world.writes) == 1
    body = result.journal["result"]
    assert body["error_code"] == "needs_attention"
    assert "public" not in body
    assert all("current_metadata" not in resource for resource in body.get("operation", {}).get("resources", []))


def test_preverification_access_failure_never_installs_publication_guard(direct, monkeypatch):
    from shuffify.spotify.exceptions import SpotifyForbiddenError

    app, user_id, repository, world, before, desired, row, runner, kind = direct
    factory, protect, calls = runner.api_factory, OperationRuntime.protect_verified, []

    def no_access(user):
        api = factory(user)

        def forbidden(*args, **kwargs):
            raise SpotifyForbiddenError("private provider denial")

        api.get_playlist = forbidden
        return api

    def observed(self, name, body, **options):
        calls.append(name)
        return protect(self, name, body, **options)

    runner.api_factory = no_access
    monkeypatch.setattr(OperationRuntime, "protect_verified", observed)
    assert runner.run_once(row.id) is True
    result = repository.get(row.id)
    assert result.state == "failed"
    assert result.journal["result"].get("error_code") != "verified_step_publication_failed"
    assert world.writes == []
    assert calls == []
