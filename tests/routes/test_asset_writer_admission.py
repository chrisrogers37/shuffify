"""Asset admissions freeze one local command with no provider/authentication work."""

import pytest

from shuffify.models.db import JobExecution, PlaylistPair, User, db

CASES = [
    ("/api/playlist/target/pair", {"create_new": True, "production_playlist_name": "Mix"}, "pair_create"),
    ("/api/playlist/target/raid-link", {"create_new": True, "target_playlist_name": "Mix"}, "raid_link_create"),
    ("/api/playlist/target/setup/spotify-playlists", {"purpose": "archive", "name": "Chosen"}, "setup_playlist_create"),
    (
        "/api/playlist/target/pair",
        {"archive_playlist_id": "archive", "archive_playlist_name": "Archive"},
        "pair_create",
    ),
    ("/api/playlist/target/raid-link", {"create_new": False, "raid_playlist_id": "collection"}, "raid_link_create"),
]


@pytest.mark.parametrize("path,body,kind", CASES)
def test_asset_admission_is_one_queued_row_and_zero_spotify(auth_client, monkeypatch, path, body, kind):
    def forbidden(*args, **kwargs):
        raise AssertionError("Admission must never authenticate or contact Spotify")

    monkeypatch.setattr("shuffify.routes.require_auth", forbidden)
    monkeypatch.setattr("shuffify.spotify.api.SpotifyAPI.__init__", forbidden)
    response = auth_client.post(path, json=body, headers={"Idempotency-Key": "one-asset"})
    assert response.status_code == 202
    accepted = response.json["operation"]
    assert response.headers["Location"] == f"/api/operations/{accepted['id']}"
    row = db.session.get(JobExecution, accepted["id"])
    assert row.kind == kind and row.state == "queued"
    assert row.targets == sorted(
        {
            "target",
            *([body["archive_playlist_id"]] if "archive_playlist_id" in body else []),
            *([body["raid_playlist_id"]] if not body.get("create_new", True) else []),
        }
    )
    assert JobExecution.query.count() == 1
    assert "journal" not in accepted and "payload" not in accepted


def test_asset_key_replays_before_changed_local_binding(auth_client, monkeypatch):
    path, body, _ = CASES[0]
    first = auth_client.post(path, json=body, headers={"Idempotency-Key": "one-asset"})
    assert first.status_code == 202
    user = User.query.filter_by(spotify_id="user123").one()
    db.session.add(PlaylistPair(user_id=user.id, production_playlist_id="target", archive_playlist_id="other"))
    db.session.commit()
    replay = auth_client.post(path, json=body, headers={"Idempotency-Key": "one-asset"})
    assert replay.status_code == 202 and replay.json["operation_id"] == first.json["operation_id"]
    conflict = auth_client.post(
        path, json={**body, "production_playlist_name": "Changed"}, headers={"Idempotency-Key": "one-asset"}
    )
    assert conflict.status_code == 409 and conflict.json["error_code"] == "request_key_conflict"
    assert JobExecution.query.count() == 1
