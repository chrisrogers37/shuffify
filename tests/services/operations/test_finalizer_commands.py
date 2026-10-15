"""Source finalizers retain the saved target and accept one exact local receipt."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import requests
from sqlalchemy import update
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, JobExecution, PendingRaidTrack, RaidPlaylistLink, db
from shuffify.services.operations.commands.finalizer_capture import capture_append, capture_raids, capture_restore
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import Crash, uri
from tests.services.operations.test_routine_commands import routine  # noqa: F401


@pytest.fixture
def cleanup(routine):  # noqa: F811
    from shuffify.services.operations.commands import finalizers

    pending = [
        PendingRaidTrack(
            user_id=routine.user.id,
            target_playlist_id="target",
            track_uri=uri(i),
            track_name="Example track",
            status="pending",
        )
        for i in (1, 2)
    ]
    db.session.add_all(pending)
    db.session.commit()
    routine.world.playlists["collection"]["track_uris"] = [uri(1), uri(1), uri(2), uri(4)]
    routine.world.playlists["archive"]["track_uris"] = [uri(1), uri(1), uri(2), uri(6)]
    routine.pending_ids = [row.id for row in pending]

    def enqueue(kind="raids", **overrides):
        values = {
            "source_playlist_id": "collection" if kind == "raids" else "archive",
            "target_track_uris": [uri(1), uri(2), uri(3)],
        }
        if kind == "raids":
            values["track_ids"] = routine.pending_ids
        else:
            values["track_uris"] = [uri(1), uri(2)]
        if kind == "append":
            values["baseline_track_uris"] = list(routine.world.playlists["archive"]["track_uris"])
        values.update(overrides)
        with Session(db.engine) as storage:
            command = {"raids": capture_raids, "append": capture_append, "restore": capture_restore}[kind](
                storage, routine.user.id, "target", **values
            )
        return routine.repository.enqueue(
            kind=command.kind,
            user_id=routine.user.id,
            targets=command.reserved_targets,
            payload={"version": 1, "command": command.model_dump(mode="json")},
        )

    def run(row, factory=None):
        runner = OperationWorker(
            routine.app,
            repository=routine.repository,
            handlers=finalizers.HANDLERS,
            api_factory=factory or routine.factory,
            heartbeat_interval=0,
        )
        assert runner.run_once(row.id)
        return routine.repository.get(row.id)

    routine.enqueue_cleanup, routine.run_cleanup = enqueue, run
    return routine


@pytest.mark.parametrize(
    "kind,count_key", [("raids", "promoted_count"), ("append", "archived_count"), ("restore", "restored_count")]
)
def test_finalizer_accepts_one_source_step_without_changing_saved_target(cleanup, kind, count_key):
    target = deepcopy(cleanup.world.playlists["target"])
    row = cleanup.enqueue_cleanup(kind)
    completed = cleanup.run_cleanup(row)
    result = completed.journal["result"]
    assert completed.state == "succeeded"
    assert result["success"] is True
    assert result[count_key] == 2
    assert result["resource"] == ("collection" if kind == "raids" else "archive")
    assert cleanup.world.playlists["target"] == target
    assert all(item[1] != "/playlists/target/items" for item in cleanup.world.writes)
    assert len(completed.journal["boundaries"]["accepted"]) == 1
    assert ActivityLog.query.count() == 1
    if kind == "raids":
        assert result["database_status"] == result["source_status"] == "complete"
        assert cleanup.world.playlists["collection"]["track_uris"] == [uri(4)]
        assert PendingRaidTrack.query.filter_by(status="promoted").count() == 2
        receipt = completed.journal["boundaries"]["accepted"][0]["result"]["receipt"]
        assert receipt["promoted_ids"] == cleanup.pending_ids
    elif kind == "append":
        assert cleanup.world.playlists["archive"]["track_uris"] == [uri(1), uri(1), uri(2), uri(6), uri(1), uri(2)]
    else:
        assert cleanup.world.playlists["archive"]["track_uris"] == [uri(6)]


def test_owned_binding_change_after_admission_refuses_before_authentication(cleanup):
    row = cleanup.enqueue_cleanup()
    RaidPlaylistLink.query.one().raid_playlist_id = "replacement"
    db.session.commit()
    completed = cleanup.run_cleanup(row)
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "source_changed"
    assert cleanup.factories == cleanup.world.writes == []
    assert PendingRaidTrack.query.filter_by(status="pending").count() == 2


@pytest.mark.parametrize(
    "kind,count_key",
    [
        ("raids", "promoted_count"),
        ("restore", "restored_count"),
        ("append", "archived_count"),
    ],
)
def test_confirmed_finalizer_rollback_reports_restored_source_and_no_database_transition(cleanup, kind, count_key):
    from shuffify.spotify.exceptions import SpotifyWriteRejectedError

    PendingRaidTrack.query.delete()
    selected = [uri(index) for index in range(101)]
    pending = [
        PendingRaidTrack(
            user_id=cleanup.user.id,
            target_playlist_id="target",
            track_uri=value,
            track_name="Example track",
            status="pending",
        )
        for value in selected
    ]
    db.session.add_all(pending)
    db.session.commit()
    cleanup.pending_ids = [item.id for item in pending]
    cleanup.world.playlists["target"]["track_uris"] = list(selected)
    source_id = "collection" if kind == "raids" else "archive"
    cleanup.world.playlists[source_id]["track_uris"] = [uri(999)] if kind == "append" else selected + [uri(999)]
    target = deepcopy(cleanup.world.playlists["target"])
    source = deepcopy(cleanup.world.playlists[source_id])
    original, calls = cleanup.world.dispatch, []

    def dispatch(method, path, body):
        if method == ("POST" if kind == "append" else "DELETE"):
            calls.append(path)
            if len(calls) == 2:
                raise SpotifyWriteRejectedError("Example batch refused")
        return original(method, path, body)

    cleanup.world.dispatch = dispatch

    def factory(user):
        api = cleanup.factory(user)
        original_page = api.get_playlist_items_raw

        def page(resource, limit=100, offset=0):
            value = original_page(resource, limit=limit, offset=offset)
            end = offset + len(value["items"])
            value["next"] = f"https://fixture.invalid/?offset={end}" if end < value["total"] else None
            return value

        api.get_playlist_items_raw = page
        return api

    selection = {} if kind == "raids" else {"track_uris": selected}
    completed = cleanup.run_cleanup(
        cleanup.enqueue_cleanup(kind, target_track_uris=selected, **selection), factory=factory
    )
    assert completed.state == "rolled_back", completed.journal["result"]
    result = completed.journal["result"]
    assert result["source_status"] == "restored"
    if kind == "raids":
        assert result["database_status"] == "not_started"
    assert result[count_key] == result["affected_count"] == 0
    assert cleanup.world.playlists[source_id]["track_uris"] == source["track_uris"]
    assert cleanup.world.playlists["target"] == target
    assert PendingRaidTrack.query.filter_by(status="pending").count() == 101
    assert ActivityLog.query.count() == 0
    assert completed.journal["boundaries"]["accepted"] == []


@pytest.mark.parametrize("change", ["status", "uri", "owner", "deleted"])
def test_changed_selected_identity_refuses_before_authentication(cleanup, change):
    row = cleanup.enqueue_cleanup()
    pending = db.session.get(PendingRaidTrack, cleanup.pending_ids[0])
    if change == "status":
        pending.status = "dismissed"
    elif change == "uri":
        pending.track_uri = uri(9)
    elif change == "owner":
        pending.target_playlist_id = "other"
    else:
        db.session.delete(pending)
    db.session.commit()
    completed = cleanup.run_cleanup(row)
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "invalid_track_selection"
    assert cleanup.factories == cleanup.world.writes == []


def test_later_inbox_rows_never_expand_the_captured_selection(cleanup):
    row = cleanup.enqueue_cleanup()
    later = PendingRaidTrack(
        user_id=cleanup.user.id,
        target_playlist_id="target",
        track_uri=uri(4),
        track_name="Later example",
        status="pending",
    )
    db.session.add(later)
    db.session.commit()
    later_id = later.id
    assert cleanup.run_cleanup(row).state == "succeeded"
    db.session.expire_all()
    assert db.session.get(PendingRaidTrack, later_id).status == "pending"
    assert cleanup.world.playlists["collection"]["track_uris"] == [uri(4)]


def test_target_change_refuses_source_work_and_does_not_promote(cleanup):
    row = cleanup.enqueue_cleanup()
    cleanup.world.playlists["target"]["track_uris"].reverse()
    completed = cleanup.run_cleanup(row)
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "target_changed"
    assert cleanup.world.writes == []
    assert PendingRaidTrack.query.filter_by(status="pending").count() == 2


def test_missing_collection_is_a_db_only_exact_promotion_after_target_check(cleanup):
    db.session.delete(RaidPlaylistLink.query.one())
    db.session.commit()
    row = cleanup.enqueue_cleanup(source_playlist_id=None)
    completed = cleanup.run_cleanup(row)
    result = completed.journal["result"]
    assert completed.state == "succeeded"
    assert result["resource"] is None
    assert result["source_status"] == "not_required"
    assert result["database_status"] == "complete"
    assert cleanup.world.writes == []
    assert PendingRaidTrack.query.filter_by(status="promoted").count() == 2


def test_required_promotion_failure_preserves_verified_source_without_compensation(cleanup, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("private database failure")

    monkeypatch.setattr("shuffify.services.operations.commands.finalizers.promote_pending", unavailable)
    row = cleanup.enqueue_cleanup()
    completed = cleanup.run_cleanup(row)
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert result["status"] == "unknown"
    assert result["source_status"] == "complete"
    assert result["database_status"] == "failed"
    assert result["error_code"] == "database_update_failed"
    assert result["promoted_count"] == 0
    assert result["retryable"] is True
    assert cleanup.world.playlists["collection"]["track_uris"] == [uri(4)]
    assert len(cleanup.world.writes) == 1
    assert completed.journal["boundaries"]["accepted"] == []
    assert PendingRaidTrack.query.filter_by(status="pending").count() == 2
    assert ActivityLog.query.count() == 0
    assert "private" not in str(result)


def _expire(cleanup, row):
    with cleanup.repository.engine.begin() as connection:
        connection.execute(
            update(JobExecution)
            .where(JobExecution.id == row.id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )


def test_crash_after_atomic_promotion_resumes_same_receipt_and_frozen_statuses(cleanup, monkeypatch):
    from shuffify.services.operations.runtime import OperationRuntime

    local, cut = OperationRuntime.local_step, []

    def crash(self, name, callback, **kwargs):
        value = local(self, name, callback, **kwargs)
        if name == "accept.finalize_raids" and not cut:
            cut.append(True)
            raise Crash()
        return value

    monkeypatch.setattr(OperationRuntime, "local_step", crash)
    row = cleanup.enqueue_cleanup()
    with pytest.raises(Crash):
        cleanup.run_cleanup(row)
    assert PendingRaidTrack.query.filter_by(status="promoted").count() == 2
    _expire(cleanup, row)
    completed = cleanup.run_cleanup(row)
    assert completed.state == "succeeded"
    assert len(cleanup.world.writes) == 1
    assert ActivityLog.query.count() == 1
    frozen = completed.journal["runtime"]["prepared"]["finalizer.binding.v1"]["command"]
    assert frozen["pending_rows"][0]["status"] == "pending"


def test_rejected_source_write_never_changes_target_or_promotes(cleanup):
    def factory(user):
        api = cleanup.factory(user)
        send = api._http._session.request

        def reject(method, url, **kwargs):
            if method == "DELETE":
                response = requests.Response()
                response.status_code, response._content = 400, b"{}"
                return response
            return send(method, url, **kwargs)

        api._http._session.request = reject
        return api

    row = cleanup.enqueue_cleanup()
    completed = cleanup.run_cleanup(row, factory)
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "source_write_rejected"
    assert completed.journal["result"]["resource_name"] == "Collection"
    assert cleanup.world.writes == []
    assert PendingRaidTrack.query.filter_by(status="pending").count() == 2


def test_optional_activity_failure_does_not_undo_verified_source_or_promotion(cleanup, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("private optional record failure")

    monkeypatch.setattr("shuffify.services.operations.bookkeeping.OperationBookkeeping.activity", unavailable)
    completed = cleanup.run_cleanup(cleanup.enqueue_cleanup())
    assert completed.state == "succeeded"
    assert completed.journal["result"]["bookkeeping"]["activity"]["status"] == "failed"
    assert PendingRaidTrack.query.filter_by(status="promoted").count() == 2
    assert len(cleanup.world.writes) == 1


def test_lease_loss_during_promotion_remains_recoverable_without_repeating_source_write(cleanup, monkeypatch):
    from shuffify.services.operations.commands import finalizers
    from shuffify.services.operations.repository import LeaseLost

    promote, cut = finalizers.promote_pending, []

    def lose(*args, **kwargs):
        if not cut:
            cut.append(True)
            raise LeaseLost("Example lease handoff")
        return promote(*args, **kwargs)

    monkeypatch.setattr(finalizers, "promote_pending", lose)
    row = cleanup.enqueue_cleanup()
    runner = OperationWorker(
        cleanup.app,
        repository=cleanup.repository,
        handlers=finalizers.HANDLERS,
        api_factory=cleanup.factory,
        heartbeat_interval=0,
    )
    assert runner.run_once(row.id) is False
    interrupted = cleanup.repository.get(row.id)
    assert "failure" not in interrupted.journal.get("runtime", {})
    assert interrupted.journal["boundaries"]["accepted"] == []
    assert PendingRaidTrack.query.filter_by(status="pending").count() == 2
    assert len(cleanup.world.writes) == 1
    _expire(cleanup, row)
    completed = cleanup.run_cleanup(row)
    assert completed.state == "succeeded"
    assert PendingRaidTrack.query.filter_by(status="promoted").count() == 2
    assert len(cleanup.world.writes) == 1
    assert ActivityLog.query.count() == 1


def test_external_edit_after_accepted_receipt_downgrades_terminal_publication_without_overwrite(cleanup, monkeypatch):
    finish, cut = cleanup.repository.finish, []

    def unavailable(*args, **kwargs):
        if not cut:
            cut.append(True)
            raise ValueError("Example unavailable publication")
        return finish(*args, **kwargs)

    monkeypatch.setattr(cleanup.repository, "finish", unavailable)
    from shuffify.services.operations.commands import finalizers

    row = cleanup.enqueue_cleanup()
    runner = OperationWorker(
        cleanup.app,
        repository=cleanup.repository,
        handlers=finalizers.HANDLERS,
        api_factory=cleanup.factory,
        heartbeat_interval=0,
    )
    assert runner.run_once(row.id) is False
    cleanup.world.playlists["collection"]["track_uris"].append(uri(9))
    edited = deepcopy(cleanup.world.playlists["collection"])
    _expire(cleanup, row)
    completed = cleanup.run_cleanup(row)
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert result["status"] == result["source_status"] == "unknown"
    assert result["promoted_count"] is result["affected_count"] is None
    assert result["retryable"] is False
    assert result["database_status"] == "complete"
    assert cleanup.world.playlists["collection"] == edited
    assert len(cleanup.world.writes) == 1
    assert PendingRaidTrack.query.filter_by(status="promoted").count() == 2
    assert ActivityLog.query.count() == 1


@pytest.mark.parametrize(
    "failure,code,status,retryable",
    [
        ("auth", "authentication_required", 401, False),
        ("access", "playlist_access_denied", 409, False),
        ("unsupported", "unsupported_playlist_items", 409, False),
        ("incomplete", "upstream_read_failed", 502, True),
    ],
)
def test_initial_provider_read_rejection_preserves_authored_source_contract(cleanup, failure, code, status, retryable):
    from shuffify.services.playlist_service import PlaylistAccessError
    from shuffify.services.workshop_commit_service import IncompletePlaylistRead, UnsupportedPlaylistItems
    from shuffify.spotify.exceptions import SpotifyAuthError

    def factory(user):
        api = cleanup.factory(user)

        def fail(*args, **kwargs):
            raise {
                "auth": SpotifyAuthError,
                "access": PlaylistAccessError,
                "unsupported": UnsupportedPlaylistItems,
                "incomplete": IncompletePlaylistRead,
            }[failure]("private observation error")

        api.get_playlist = fail
        return api

    completed = cleanup.run_cleanup(cleanup.enqueue_cleanup(), factory)
    result = completed.journal["result"]
    assert completed.state == "failed"
    assert (result["error_code"], result["http_status"], result["retryable"]) == (code, status, retryable)
    assert result["status"] == "failed"
    assert result["affected_count"] == result["promoted_count"] == 0
    assert cleanup.world.writes == []
    assert "private" not in str(result)


@pytest.mark.parametrize(
    "kind,resource,count_key",
    [
        ("raids", "collection", "promoted_count"),
        ("append", "archive", "archived_count"),
        ("restore", "archive", "restored_count"),
    ],
)
def test_external_edit_after_acknowledged_crash_keeps_source_identity_and_unknown_counts(
    cleanup,
    monkeypatch,
    kind,
    resource,
    count_key,
):
    from shuffify.services.operations.runtime import OperationRuntime

    checkpoint, cut = OperationRuntime.checkpoint, []

    def crash(self, document):
        value = checkpoint(self, document)
        if not cut and any(
            batch.get("state") == "acknowledged" for plan in document.get("plans", []) for batch in plan["batches"]
        ):
            cut.append(True)
            raise Crash()
        return value

    monkeypatch.setattr(OperationRuntime, "checkpoint", crash)
    row = cleanup.enqueue_cleanup(kind)
    with pytest.raises(Crash):
        cleanup.run_cleanup(row)
    cleanup.world.playlists[resource]["track_uris"].append(uri(9))
    edited = deepcopy(cleanup.world.playlists[resource])
    _expire(cleanup, row)
    result = cleanup.run_cleanup(row).journal["result"]
    assert result["resource"] == resource
    assert result["resource_name"] == resource.title()
    assert result["status"] == "unknown" and result["retryable"] is False
    assert result[count_key] is result["affected_count"] is None
    assert cleanup.world.playlists[resource] == edited
    assert len(cleanup.world.writes) == 1
    if kind == "raids":
        assert result["source_status"] == "unknown"
        assert result["database_status"] == "pending"
        assert PendingRaidTrack.query.filter_by(status="pending").count() == 2


def test_attention_candidate_recovery_invalidates_source_fact_after_external_edit(cleanup, monkeypatch):
    from shuffify.services.operations.commands import finalizers

    finish, cut, promotions = cleanup.repository.finish, [], []

    def promotion_failed(*args, **kwargs):
        promotions.append(True)
        raise ValueError("Example mandatory DB outage")

    def publication_failed(*args, **kwargs):
        if not cut:
            cut.append(True)
            raise ValueError("Example terminal publication outage")
        return finish(*args, **kwargs)

    monkeypatch.setattr(finalizers, "promote_pending", promotion_failed)
    monkeypatch.setattr(cleanup.repository, "finish", publication_failed)
    row = cleanup.enqueue_cleanup()
    worker = OperationWorker(
        cleanup.app,
        repository=cleanup.repository,
        handlers=finalizers.HANDLERS,
        api_factory=cleanup.factory,
        heartbeat_interval=0,
    )
    assert worker.run_once(row.id) is False
    candidate = cleanup.repository.get(row.id).journal["runtime"]["terminal_finalization"]
    assert candidate["state"] == "needs_attention" and candidate["done"] is True
    assert candidate["result"]["source_status"] == "complete"
    cleanup.world.playlists["collection"]["track_uris"].append(uri(9))
    edited = deepcopy(cleanup.world.playlists["collection"])
    _expire(cleanup, row)
    result = cleanup.run_cleanup(row).journal["result"]
    assert result["resource"] == "collection"
    assert result["source_status"] == "unknown"
    assert result["status"] == "unknown"
    assert result["database_status"] == "failed"
    assert result["retryable"] is False
    assert result["promoted_count"] is result["affected_count"] is None
    assert len(promotions) == 1
    assert len(cleanup.world.writes) == 1
    assert cleanup.world.playlists["collection"] == edited
