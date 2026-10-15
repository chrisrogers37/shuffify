"""Unarchive admission binds local owned pair/history without authenticating Spotify."""

import pytest

from shuffify.models.db import ActivityLog, JobExecution, PlaylistPair, User, db
from shuffify.services.operations.repository import OperationRepository

A, B = ["spotify:track:" + letter * 22 for letter in "ab"]


@pytest.fixture
def paired(auth_client):
    user = User.query.filter_by(spotify_id="user123").one()
    pair = PlaylistPair(user_id=user.id, production_playlist_id="target", archive_playlist_id="archive")
    db.session.add(pair)
    db.session.commit()
    return user, pair


@pytest.fixture
def no_spotify(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unarchive admission cannot authenticate or contact Spotify")

    for path in [
        "shuffify.routes.require_auth",
        "shuffify.services.AuthService.get_authenticated_api",
        "shuffify.spotify.api.SpotifyAPI.__init__",
        "requests.sessions.Session.request",
        "shuffify.services.playlist_pair_service.PlaylistPairService.unarchive_tracks",
    ]:
        monkeypatch.setattr(path, forbidden)


def test_admission_captures_both_resources_and_replays_before_pair_or_history_recapture(
    auth_client, paired, no_spotify
):
    user, pair = paired
    payload = {"track_uris": [A, A, B], "archive_playlist_id": "notauthoritative"}
    path = "/api/playlist/target/pair/unarchive"
    response = auth_client.post(path, json=payload, headers={"Idempotency-Key": "archive-move"})
    assert response.status_code == 202
    row = JobExecution.query.one()
    assert row.kind == "unarchive" and row.user_id == user.id
    assert row.targets == ["archive", "target"]
    assert row.payload["command"] == {
        "pair_id": pair.id,
        "playlist_id": "target",
        "archive_playlist_id": "archive",
        "track_uris": [A, A, B],
    }
    assert set(row.payload["history"]["revisions"]) == {"archive", "target"}
    assert ActivityLog.query.count() == 0
    assert auth_client.get(response.headers["Location"]).json["operation"]["id"] == row.id
    conflict = auth_client.post(path, json={"track_uris": [A, B]}, headers={"Idempotency-Key": "archive-move"})
    assert conflict.status_code == 409 and conflict.json["error_code"] == "request_key_conflict"
    repository = OperationRepository()
    repository.finish(repository.claim(operation_id=row.id), "succeeded")
    db.session.delete(pair)
    db.session.commit()
    with auth_client.session_transaction() as session:
        session["playlist_states"] = {"target": {"states": "corrupt"}}
    replay = auth_client.post(path, json=payload, headers={"Idempotency-Key": "archive-move"})
    assert replay.status_code == 202 and replay.json["operation_id"] == row.id
    assert JobExecution.query.count() == 1


def test_pair_reservations_block_both_resources_without_a_second_operation(auth_client, paired, no_spotify):
    first = auth_client.post("/api/playlist/target/pair/unarchive", json={"track_uris": [A]})
    assert first.status_code == 202
    for resource in ["target", "archive"]:
        busy = auth_client.post(f"/api/playlist/{resource}/toggle-visibility", json={"public": False})
        assert busy.status_code == 409
        assert busy.json["operation_id"] == first.json["operation_id"]
    assert JobExecution.query.count() == 1


@pytest.mark.parametrize("payload", [{}, {"track_uris": []}, {"track_uris": ["invalid"]}, {"track_uris": True}])
def test_invalid_public_selection_never_enqueues(auth_client, paired, no_spotify, payload):
    response = auth_client.post("/api/playlist/target/pair/unarchive", json=payload)
    assert response.status_code == 400
    assert JobExecution.query.count() == 0


def test_unpaired_target_refuses_locally(auth_client, no_spotify):
    response = auth_client.post("/api/playlist/target/pair/unarchive", json={"track_uris": [A]})
    assert response.status_code == 404
    assert response.json["error_code"] == "pair_not_found"
    assert JobExecution.query.count() == 0


def test_broken_history_for_either_resource_refuses_locally(auth_client, paired, no_spotify):
    with auth_client.session_transaction() as session:
        session["playlist_states"] = {"archive": {"states": "corrupt"}}
    response = auth_client.post("/api/playlist/target/pair/unarchive", json={"track_uris": [A]})
    assert response.status_code == 409
    assert response.json["error_code"] == "history_unavailable"
    assert JobExecution.query.count() == 0
