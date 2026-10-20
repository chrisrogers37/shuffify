"""Queued creation accepts privacy and association exactly once, retaining orphan identities."""

import json
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, PlaylistPair, RaidPlaylistLink, User, db
from shuffify.services.operations.commands.asset_capture import capture_asset
from shuffify.services.operations.commands.assets import HANDLERS
from shuffify.services.operations.repository import OperationRepository
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.spotify.exceptions import SpotifyForbiddenError
from shuffify.worker import OperationWorker
from tests.services.operations.test_creation_executor import CreationWorld


@pytest.fixture
def asset_operation(db_app, sample_token):
    user = User(spotify_id="owner")
    db.session.add(user)
    db.session.commit()
    world = CreationWorld(ignored_private=True)
    repository = OperationRepository()
    factories = []

    def factory(current):
        factories.append(current.id)
        api = SpotifyAPI(TokenInfo.from_dict(sample_token), auto_refresh=False)

        def metadata(resource, **kwargs):
            value = world.read(resource)
            return {
                "id": resource,
                "owner": {"id": "owner"},
                "collaborative": False,
                "snapshot_id": value["snapshot_id"],
                "description": None,
                **value["metadata"],
            }

        def send(method, url, **kwargs):
            value = world.dispatch(method, urlsplit(url).path.removeprefix("/v1"), kwargs["json"])
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(value).encode() if value else b""
            return response

        api.get_playlist = metadata
        api._http._session.request = send
        return api

    def enqueue(kind="pair_create", **options):
        with Session(db.engine) as storage:
            command = capture_asset(storage, user.id, "target", kind=kind, name="Chosen", **options)
        return repository.enqueue(
            kind=command.kind,
            user_id=user.id,
            targets=command.reserved_targets,
            payload={"version": 1, "command": command.model_dump(mode="json")},
        )

    def run(row):
        worker = OperationWorker(
            db_app, repository=repository, handlers=HANDLERS, api_factory=factory, heartbeat_interval=0
        )
        assert worker.run_once(row.id)
        return repository.get(row.id)

    return SimpleNamespace(
        user=user,
        world=world,
        repository=repository,
        enqueue=enqueue,
        run=run,
        factories=factories,
        api_factory=factory,
    )


@pytest.mark.parametrize("kind", ["pair_create", "raid_link_create", "setup_playlist_create"])
def test_created_asset_is_private_and_accepts_one_local_receipt(asset_operation, kind):
    case = asset_operation
    target = deepcopy(case.world.playlists["target"])
    completed = case.run(case.enqueue(kind))
    result = completed.journal["result"]
    assert completed.state == "succeeded"
    assert result["playlist"]["id"] == "created-1"
    assert result["privacy_confirmed"] is True
    assert case.world.playlists["created-1"]["metadata"]["public"] is False
    assert case.world.playlists["target"] == target
    assert len(case.world.writes) == 2
    assert ActivityLog.query.count() == 1
    assert PlaylistPair.query.count() == int(kind == "pair_create")
    assert RaidPlaylistLink.query.count() == int(kind == "raid_link_create")
    assert len(completed.journal["boundaries"]["accepted"]) == 1


@pytest.mark.parametrize("lost_reply", [False, True])
def test_receipt_outage_retains_created_identity_without_duplicate_creation(asset_operation, monkeypatch, lost_reply):
    case = asset_operation
    original, failures = case.repository.local_step, []

    def fail(lease, name, callback, **kwargs):
        if name == "accept.asset" and not failures:
            value = original(lease, name, callback, **kwargs) if lost_reply else None
            assert len(case.world.writes) == 2
            failures.append(value)
            raise OperationalError("COMMIT acceptance", {}, Exception("private database reply failure"))
        return original(lease, name, callback, **kwargs)

    monkeypatch.setattr(case.repository, "local_step", fail)
    completed = case.run(case.enqueue())
    result = completed.journal["result"]
    assert len(failures) == 1
    assert completed.state == ("succeeded" if lost_reply else "needs_attention")
    assert result["playlist"]["id"] == "created-1"
    assert result["privacy_confirmed"] is True
    assert PlaylistPair.query.count() == int(lost_reply)
    assert ActivityLog.query.count() == int(lost_reply)
    assert len(case.world.writes) == 2
    assert "private database" not in str(result)


def test_existing_raid_link_is_worker_local_and_cannot_create_a_second_playlist(asset_operation):
    case = asset_operation
    db.session.add(RaidPlaylistLink(user_id=case.user.id, target_playlist_id="target", raid_playlist_id="collection"))
    db.session.commit()
    completed = case.run(case.enqueue("raid_link_create"))
    assert completed.state == "succeeded"
    assert completed.journal["result"]["raid_link"]["raid_playlist_id"] == "collection"
    assert case.factories == []
    assert case.world.writes == []
    assert ActivityLog.query.count() == 0


def test_known_privacy_refusal_retains_orphan_identity_without_association(asset_operation, monkeypatch):
    case = asset_operation
    dispatch = case.world.dispatch

    def refuse(method, path, body):
        if method == "PUT":
            raise SpotifyForbiddenError("private provider refusal")
        return dispatch(method, path, body)

    monkeypatch.setattr(case.world, "dispatch", refuse)
    completed = case.run(case.enqueue())
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert result["playlist"]["id"] == "created-1"
    assert result["privacy_confirmed"] is False
    assert result["creation_unknown"] is False
    assert PlaylistPair.query.count() == 0
    assert len(case.world.writes) == 1
    assert case.world.playlists["created-1"]["metadata"]["public"] is True
    assert "private provider" not in str(result)


def test_lost_creation_response_is_unknown_and_never_adopted_by_name(asset_operation, monkeypatch):
    case = asset_operation
    dispatch = case.world.dispatch

    def lost(method, path, body):
        value = dispatch(method, path, body)
        if method == "POST":
            raise requests.ConnectionError("private provider response lost")
        return value

    monkeypatch.setattr(case.world, "dispatch", lost)
    completed = case.run(case.enqueue())
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert result["creation_unknown"] is True
    assert result["outcome"] == "unknown"
    assert "playlist" not in result
    assert len(case.world.writes) == 1
    assert "created-1" in case.world.playlists
    assert PlaylistPair.query.count() == 0


def test_late_read_failure_cannot_undo_confirmed_private_visibility(asset_operation, monkeypatch):
    case = asset_operation
    read = case.world.read

    def unavailable(resource):
        if len(case.world.writes) == 2:
            raise ValueError("private provider read unavailable")
        return read(resource)

    monkeypatch.setattr(case.world, "read", unavailable)
    completed = case.run(case.enqueue())
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert result["playlist"]["id"] == "created-1"
    assert result["privacy_confirmed"] is False
    assert case.world.playlists["created-1"]["metadata"]["public"] is False
    assert len(case.world.writes) == 2
    assert PlaylistPair.query.count() == 0


def test_binding_drift_refuses_before_authentication_or_creation(asset_operation):
    case = asset_operation
    row = case.enqueue()
    db.session.add(PlaylistPair(user_id=case.user.id, production_playlist_id="target", archive_playlist_id="other"))
    db.session.commit()
    completed = case.run(row)
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "association_changed"
    assert case.factories == []
    assert case.world.writes == []
    assert PlaylistPair.query.one().archive_playlist_id == "other"


def test_optional_activity_failure_preserves_required_association(asset_operation, monkeypatch):
    case = asset_operation

    def unavailable(*args, **kwargs):
        raise ValueError("private activity database failure")

    monkeypatch.setattr("shuffify.services.activity_log_service.ActivityLogService.log", unavailable)
    completed = case.run(case.enqueue())
    assert completed.state == "succeeded"
    assert PlaylistPair.query.count() == 1
    assert ActivityLog.query.count() == 0
    assert len(case.world.writes) == 2
    receipt = completed.journal["boundaries"]["accepted"][0]["result"]["receipt"]
    assert receipt["activity"] == {"status": "failed", "error_code": "activity_record_failed"}


def test_terminal_recovery_refuses_intervening_privacy_edit_without_duplicate_activity(asset_operation, monkeypatch):
    from tests.services.operations.test_crash_recovery import Crash, expire_lease

    case = asset_operation
    original = case.repository.finish

    def interrupted(*args, **kwargs):
        raise Crash("terminal publication interrupted")

    monkeypatch.setattr(case.repository, "finish", interrupted)
    row = case.enqueue()
    with pytest.raises(Crash):
        case.run(row)
    assert PlaylistPair.query.count() == ActivityLog.query.count() == 1
    case.world.playlists["created-1"]["metadata"]["public"] = True
    case.world.playlists["created-1"]["snapshot_id"] = "external-edit"
    expire_lease(case.repository, row.id)
    monkeypatch.setattr(case.repository, "finish", original)
    completed = case.run(row)
    assert completed.state == "needs_attention"
    assert completed.journal["result"]["playlist"]["id"] == "created-1"
    assert completed.journal["result"]["privacy_confirmed"] is False
    assert case.world.playlists["created-1"]["metadata"]["public"] is True
    assert len(case.world.writes) == 2
    assert PlaylistPair.query.count() == ActivityLog.query.count() == 1


def test_configuration_lock_defers_same_creation_before_auth_and_completes_after_release(
    asset_operation,
    db_app,
    monkeypatch,
):
    import threading
    import time
    from datetime import datetime, timezone

    from shuffify.services.playlist_lock import playlist_configuration_lock

    case = asset_operation
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr("shuffify.services.operations.commands.assets.CONFIG_LOCK_WAIT_S", 0.03)

    def hold():
        with db_app.app_context(), playlist_configuration_lock("target") as acquired:
            assert acquired
            entered.set()
            assert release.wait(3)

    holder = threading.Thread(target=hold)
    holder.start()
    assert entered.wait(2)
    row = case.enqueue()
    worker = OperationWorker(
        db_app, repository=case.repository, handlers=HANDLERS, api_factory=case.api_factory, heartbeat_interval=0
    )
    try:
        assert not worker.run_once(row.id)
        deferred = case.repository.get(row.id)
        assert deferred.lease_owner is None and deferred.lease_expires_at > datetime.now(timezone.utc)
        assert case.factories == [] and case.world.writes == []
        assert PlaylistPair.query.count() == ActivityLog.query.count() == 0
    finally:
        release.set()
        holder.join(2)
    while datetime.now(timezone.utc) <= deferred.lease_expires_at:
        time.sleep(0.02)
    assert worker.run_once(row.id)
    completed = case.repository.get(row.id)
    assert completed.state == "succeeded" and completed.id == row.id
    assert [method for method, _, _ in case.world.writes] == ["POST", "PUT"]
    assert PlaylistPair.query.count() == ActivityLog.query.count() == 1
