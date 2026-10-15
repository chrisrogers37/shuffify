"""Source cleanup admission is session/local-only and reserves its read-only target too."""

import pytest

from shuffify.models.db import ActivityLog, JobExecution, PendingRaidTrack, PlaylistPair, RaidPlaylistLink, User, db
from shuffify.services.operations.repository import OperationRepository

A = "spotify:track:" + "a" * 22


@pytest.fixture
def sources(auth_client):
    user = User.query.filter_by(spotify_id="user123").one()
    pending = PendingRaidTrack(
        user_id=user.id, target_playlist_id="target", track_uri=A, track_name="Example track", status="pending"
    )
    db.session.add_all(
        [
            pending,
            PlaylistPair(user_id=user.id, production_playlist_id="target", archive_playlist_id="archive"),
            RaidPlaylistLink(user_id=user.id, target_playlist_id="target", raid_playlist_id="collection"),
        ]
    )
    db.session.commit()
    return user, pending


@pytest.fixture
def forbid_spotify(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A source writer admission must not authenticate or contact Spotify")

    for name in (
        "shuffify.routes.require_auth",
        "shuffify.services.AuthService.get_authenticated_api",
        "shuffify.spotify.api.SpotifyAPI.__init__",
        "requests.sessions.Session.request",
    ):
        monkeypatch.setattr(name, forbidden)


@pytest.mark.parametrize(
    "path,kind,source",
    [
        ("pending-raids/finalize", "workshop_finalize_raids", "collection"),
        ("pending-raids/finalize-all", "workshop_finalize_raids", "collection"),
        ("pair/archive", "workshop_archive_append", "archive"),
        ("pair/finalize-restore", "workshop_finalize_restore", "archive"),
    ],
)
def test_exact_task_accepts_one_owned_operation_and_replays_before_recapture(
    auth_client,
    sources,
    forbid_spotify,
    path,
    kind,
    source,
):
    user, pending = sources
    values = {"source_playlist_id": source, "target_track_uris": [A]}
    if path.startswith("pending"):
        values["track_ids"] = [pending.id]
    else:
        values["track_uris"] = [A, A]
    if path == "pair/archive":
        values["baseline_track_uris"] = []
    url = f"/api/playlist/target/{path}"
    accepted = auth_client.post(url, json=values, headers={"Idempotency-Key": "source-task"})
    assert accepted.status_code == 202
    row = JobExecution.query.one()
    assert (row.kind, row.user_id, row.targets) == (kind, user.id, [source, "target"])
    assert row.payload["command"]["target_track_uris"] == [A]
    assert "inspect_only" not in row.payload["command"]
    assert auth_client.get(accepted.headers["Location"]).json["operation"]["id"] == row.id
    assert auth_client.post(url, json=values, headers={"Idempotency-Key": "source-task"}).json["operation_id"] == row.id
    busy = auth_client.post("/api/playlist/target/toggle-visibility", json={"public": False})
    assert busy.status_code == 409 and busy.json["operation_id"] == row.id
    conflict = auth_client.post(
        "/api/playlist/target/toggle-visibility", json={"public": False}, headers={"Idempotency-Key": "source-task"}
    )
    assert conflict.status_code == 409 and conflict.json["error_code"] == "request_key_conflict"
    repository = OperationRepository()
    repository.finish(repository.claim(operation_id=row.id), "succeeded")
    db.session.delete(pending)
    db.session.delete(PlaylistPair.query.one())
    db.session.delete(RaidPlaylistLink.query.one())
    db.session.commit()
    replay = auth_client.post(url, json=values, headers={"Idempotency-Key": "source-task"})
    assert replay.status_code == 202 and replay.json["operation_id"] == row.id
    assert JobExecution.query.count() == 1
    assert ActivityLog.query.count() == 0


def test_source_changed_admission_has_authored_rich_refusal_without_a_row(auth_client, sources, forbid_spotify):
    response = auth_client.post(
        "/api/playlist/target/pair/archive",
        json={
            "source_playlist_id": "other",
            "target_track_uris": [A],
            "track_uris": [A],
            "baseline_track_uris": [],
        },
    )
    assert response.status_code == 409
    assert response.json["error_code"] == "source_changed"
    assert response.json["status"] == "failed"
    assert response.json["affected_count"] == response.json["archived_count"] == 0
    assert response.json["retryable"] is False
    assert JobExecution.query.count() == 0
