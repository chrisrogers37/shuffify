"""Asset admission stays local; the worker waits for target locks before creation."""

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event, Thread

import pytest

from shuffify.models.db import JobExecution, PlaylistPair, RaidPlaylistLink, User, db
from shuffify.services.operations.commands.registry import COMMAND_HANDLERS
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.playlist_lock import playlist_configuration_lock, playlist_lock
from shuffify.worker import OperationWorker
from tests.paths import api_path
from tests.playlist_setup_fixtures import SetupSpotify
from tests.queued_operations import run_operation


@contextmanager
def held_target(app, target, lock):
    entered, release = Event(), Event()

    def hold():
        with app.app_context(), lock(target) as acquired:
            assert acquired
            entered.set()
            release.wait(timeout=8)

    holder = Thread(target=hold)
    holder.start()
    assert entered.wait(timeout=2)
    try:
        yield
    finally:
        release.set()
        holder.join(timeout=2)
        assert not holder.is_alive()


def forbidden(*args, **kwargs):
    raise AssertionError("No Spotify authorization or construction is allowed here")


@pytest.mark.parametrize("path,model", [("raid-link", RaidPlaylistLink), ("pair", PlaylistPair)])
@pytest.mark.parametrize("lock", [playlist_configuration_lock, playlist_lock])
def test_creation_defers_the_same_operation_before_spotify_while_a_target_lock_is_held(
    path,
    model,
    lock,
    auth_client,
    db_app,
    monkeypatch,
):
    monkeypatch.setattr("shuffify.routes.require_auth", forbidden)
    monkeypatch.setattr("shuffify.services.operations.commands.assets.CONFIG_LOCK_WAIT_S", 0.01)
    provider = SetupSpotify(playlists={"p1": {"name": "Mix"}})
    repository = OperationRepository()
    runner = OperationWorker(
        db_app, repository=repository, handlers=COMMAND_HANDLERS, api_factory=forbidden, heartbeat_interval=0
    )
    with held_target(db_app, "p1", lock):
        response = auth_client.post(
            api_path(f"/playlist/p1/{path}"), json={"create_new": True}, headers={"Idempotency-Key": "create-one-asset"}
        )
        assert response.status_code == 202
        operation_id = response.json["operation_id"]
        assert provider.calls == [] and model.query.count() == 0
        assert runner.run_once(operation_id) is False
        deferred = repository.get(operation_id)
        assert deferred.state == "queued" and deferred.lease_owner is None
        assert (deferred.journal or {}).get("plans", []) == []
        replay = auth_client.post(
            api_path(f"/playlist/p1/{path}"), json={"create_new": True}, headers={"Idempotency-Key": "create-one-asset"}
        )
        assert replay.status_code == 202 and replay.json["operation_id"] == operation_id
        assert JobExecution.query.count() == 1 and model.query.count() == 0
        assert provider.calls == []

    deadline = deferred.lease_expires_at.replace(tzinfo=timezone.utc)
    time.sleep(max(0, (deadline - datetime.now(timezone.utc)).total_seconds()) + 0.02)
    completed = run_operation(auth_client, provider, operation_id, response)
    assert completed.operation["state"] == "succeeded", completed.get_json()
    assert JobExecution.query.count() == 1 and model.query.count() == 1
    assert len([call for call in provider.calls if call[0] == "create_user_playlist"]) == 1


def test_existing_raid_link_is_accepted_once_and_finishes_without_spotify(auth_client, db_app, monkeypatch):
    user = User.query.filter_by(spotify_id="user123").one()
    db.session.add(RaidPlaylistLink(user_id=user.id, target_playlist_id="p1", raid_playlist_id="existing"))
    db.session.commit()
    monkeypatch.setattr("shuffify.routes.require_auth", forbidden)
    response = auth_client.post(api_path("/playlist/p1/raid-link"), json={"create_new": True})
    assert response.status_code == 202
    runner = OperationWorker(db_app, handlers=COMMAND_HANDLERS, api_factory=forbidden, heartbeat_interval=0)
    assert runner.run_once(response.json["operation_id"])
    result = auth_client.get(response.headers["Location"]).json["operation"]
    assert result["state"] == "succeeded"
    assert result["result"]["raid_link"]["raid_playlist_id"] == "existing"
    assert RaidPlaylistLink.query.count() == 1 and JobExecution.query.count() == 1


def test_existing_pair_rejects_before_spotify_creation(auth_client, monkeypatch):
    user = User.query.filter_by(spotify_id="user123").one()
    db.session.add(PlaylistPair(user_id=user.id, production_playlist_id="p1", archive_playlist_id="existing"))
    db.session.commit()
    monkeypatch.setattr("shuffify.routes.require_auth", forbidden)
    response = auth_client.post(api_path("/playlist/p1/pair"), json={"create_new": True})
    assert response.status_code == 409 and response.json["error_code"] == "already_paired"
    assert PlaylistPair.query.count() == 1 and JobExecution.query.count() == 0
