"""Every direct writer admits a command without authenticating or calling Spotify."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from shuffify.models.db import ActivityLog, JobExecution, PlaylistSnapshot, User, db
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.state_service import StateService


def uri(index):
    return "spotify:track:" + str(index).zfill(22)


@pytest.fixture
def local_sources(auth_client):
    user = User.query.filter_by(spotify_id="user123").one()
    snapshot = PlaylistSnapshot(user_id=user.id, playlist_id="target", playlist_name="Example", track_count=1)
    snapshot.track_uris = [uri(1)]
    db.session.add(snapshot)
    db.session.commit()
    with auth_client.session_transaction() as session:
        StateService.initialize_playlist_state(session, "target", [uri(1)])
        StateService.record_new_state(session, "target", [uri(2)])
    return user.id, snapshot.id


@pytest.fixture
def forbid_spotify(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("A writer request must never authenticate, calculate, or contact Spotify")

    for name in ("shuffify.routes.require_auth", "shuffify.services.AuthService.get_authenticated_api",
                 "shuffify.spotify.api.SpotifyAPI.__init__", "requests.sessions.Session.request",
                 "shuffify.services.shuffle_service.ShuffleService.execute"):
        monkeypatch.setattr(name, forbidden)


def writer_case(kind, snapshot_id):
    if kind == "shuffle":
        return "/api/shuffle/target", {"data": {"algorithm": "BasicShuffle"}}
    if kind == "undo":
        return "/api/undo/target", {}
    if kind == "visibility":
        return "/api/playlist/target/toggle-visibility", {"json": {"public": False}}
    if kind == "snapshot_restore":
        return f"/api/snapshots/{snapshot_id}/restore", {"json": {"track_uris": [uri(99)]}}
    return "/api/workshop/target/commit", {"json": {"baseline_track_uris": [uri(2)], "track_uris": []}}


@pytest.mark.parametrize("kind", ["shuffle", "undo", "visibility", "snapshot_restore", "workshop_commit"])
def test_writers_accept_one_row_with_zero_spotify_calls(auth_client, local_sources, forbid_spotify, kind):
    user_id, snapshot_id = local_sources
    path, options = writer_case(kind, snapshot_id)
    response = auth_client.post(path, headers={"Idempotency-Key": "example-request"}, **options)
    assert response.status_code == 202
    accepted = response.json
    schema = Path(__file__).resolve().parents[2] / "web/schemas/operation-accepted.schema.json"
    Draft7Validator(json.loads(schema.read_text())).validate(accepted)
    assert accepted["operation"]["state"] == "queued"
    assert response.headers["Location"] == f"/api/operations/{accepted['operation_id']}"
    rows = JobExecution.query.all()
    assert len(rows) == 1
    row = rows[0]
    assert (row.kind, row.user_id, row.targets) == (kind, user_id, ["target"])
    assert row.payload["version"] == 1
    assert row.payload["command"]["playlist_id"] == "target"
    assert not any(secret in json.dumps(row.payload) for secret in ("spotify_token", "access_token", "user_data"))
    assert ActivityLog.query.count() == 0
    assert PlaylistSnapshot.query.count() == 1
    assert auth_client.get(response.headers["Location"]).json["operation"]["id"] == row.id
    replayed = auth_client.post(path, headers={"Idempotency-Key": "example-request"}, **options)
    assert replayed.json["operation_id"] == row.id
    busy = auth_client.post(path, headers={"Idempotency-Key": "different-request"}, **options)
    assert busy.status_code == 409
    assert busy.json["operation_id"] == row.id


def test_snapshot_replay_uses_accepted_order_after_source_is_deleted(auth_client, local_sources, forbid_spotify):
    _user_id, snapshot_id = local_sources
    path, options = writer_case("snapshot_restore", snapshot_id)
    accepted = auth_client.post(path, headers={"Idempotency-Key": "retry-snapshot"}, **options)
    assert accepted.status_code == 202
    repository = OperationRepository()
    lease = repository.claim(operation_id=accepted.json["operation_id"])
    repository.finish(lease, "succeeded")
    db.session.delete(db.session.get(PlaylistSnapshot, snapshot_id))
    db.session.commit()
    replayed = auth_client.post(path, headers={"Idempotency-Key": "retry-snapshot"}, **options)
    assert replayed.status_code == 202
    assert replayed.json["operation_id"] == accepted.json["operation_id"]
    assert repository.get(replayed.json["operation_id"]).payload["command"]["snapshot"]["track_uris"] == [uri(1)]


def test_undo_without_history_is_rejected_before_queue(auth_client, forbid_spotify):
    response = auth_client.post("/api/undo/target")
    assert response.status_code == 400
    assert response.json["operation"]["error_code"] == "history_unavailable"
    assert JobExecution.query.count() == 0
