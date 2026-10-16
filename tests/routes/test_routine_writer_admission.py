"""Routine writer requests reserve every owned resource without Spotify authentication or I/O."""

import pytest

from shuffify.models.db import (
    ActivityLog,
    JobExecution,
    PlaylistPair,
    RaidPlaylistLink,
    Schedule,
    UpstreamSource,
    User,
    db,
)
from shuffify.services.operations.repository import OperationRepository


@pytest.fixture
def routine_sources(auth_client):
    user = User.query.filter_by(spotify_id="user123").one()
    db.session.add_all([
        PlaylistPair(user_id=user.id, production_playlist_id="target", archive_playlist_id="archive"),
        RaidPlaylistLink(user_id=user.id, target_playlist_id="target", raid_playlist_id="collection",
                         drip_enabled=True),
        UpstreamSource(user_id=user.id, target_playlist_id="target", source_playlist_id="source", source_type="own"),
    ])
    db.session.commit()
    return user


@pytest.fixture
def forbid_spotify(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A routine admission must never authenticate, resolve sources, or contact Spotify")

    for name in ("shuffify.routes.require_auth", "shuffify.services.AuthService.get_authenticated_api",
                 "shuffify.spotify.api.SpotifyAPI.__init__", "requests.sessions.Session.request",
                 "shuffify.services.executors.JobExecutorService.execute_now",
                 "shuffify.services.raid_sync_service.RaidSyncService.raid_now",
                 "shuffify.services.raid_sync_service.RaidSyncService.drip_now",
                 "shuffify.services.raid_sync_service.RaidSyncService.raid_and_drip_now"):
        monkeypatch.setattr(name, forbidden)


@pytest.mark.parametrize("job_type,targets", [
    ("shuffle", ["target"]), ("rotate", ["archive", "target"]),
    ("raid", ["archive", "collection", "target"]), ("drip", ["collection", "target"]),
    ("raid_and_shuffle", ["archive", "collection", "target"]),
    ("raid_and_drip", ["archive", "collection", "target"]),
])
def test_run_now_accepts_one_row_and_replays_before_capturing_changed_configuration(
    auth_client, routine_sources, forbid_spotify, job_type, targets,
):
    schedule = Schedule(user_id=routine_sources.id, job_type=job_type, target_playlist_id="target",
                        algorithm_name="BasicShuffle", algorithm_params={"target_size": 100}, is_enabled=False)
    db.session.add(schedule)
    db.session.commit()
    path = f"/api/schedules/{schedule.id}/run"
    response = auth_client.post(path, headers={"Idempotency-Key": "same-run"})
    assert response.status_code == 202
    operation_id = response.json["operation_id"]
    row = JobExecution.query.one()
    assert (row.id, row.kind, row.user_id, row.schedule_id, row.targets) == (
        operation_id, "schedule_run", routine_sources.id, schedule.id, targets,
    )
    assert row.payload["command"]["triggered_by"] == "manual"
    assert row.payload["command"]["definition"]["job_type"] == job_type
    assert response.headers["Location"] == f"/api/operations/{operation_id}"
    busy = auth_client.post(path, headers={"Idempotency-Key": "different-run"})
    assert busy.status_code == 409
    assert busy.json["operation_id"] == operation_id
    conflict = auth_client.post("/api/playlist/collection/toggle-visibility", json={"public": False},
                                headers={"Idempotency-Key": "same-run"})
    assert conflict.status_code == 409
    assert conflict.json["error_code"] == "request_key_conflict"
    repository = OperationRepository()
    repository.finish(repository.claim(operation_id=operation_id), "succeeded")
    schedule.target_playlist_id = "changed-target"
    db.session.commit()
    replay = auth_client.post(path, headers={"Idempotency-Key": "same-run"})
    assert replay.status_code == 202
    assert replay.json["operation_id"] == operation_id
    assert repository.get(operation_id).payload["command"]["playlist_id"] == "target"
    assert JobExecution.query.count() == 1
    assert ActivityLog.query.count() == 0


@pytest.mark.parametrize("path,kind,targets", [
    ("raid-now", "raid_now", ["archive", "collection", "target"]),
    ("drip-now", "drip_now", ["collection", "target"]),
    ("raid-and-drip", "raid_and_drip", ["archive", "collection", "target"]),
])
def test_explicit_writer_admission_has_one_identity_complete_reservations_and_no_spotify(
    auth_client, routine_sources, forbid_spotify, path, kind, targets,
):
    url = f"/api/playlist/target/{path}"
    response = auth_client.post(url, headers={"Idempotency-Key": "explicit-action"})
    assert response.status_code == 202
    row = JobExecution.query.one()
    assert (row.kind, row.targets, row.schedule_id) == (kind, targets, None)
    assert row.payload["command"]["definition"]["schedule_id"] is None
    assert row.payload["command"]["triggered_by"] == "manual"
    assert auth_client.post(url, headers={"Idempotency-Key": "explicit-action"}).json["operation_id"] == row.id
    conflict = auth_client.post("/api/playlist/collection/toggle-visibility", json={"public": False},
                                headers={"Idempotency-Key": "explicit-action"})
    assert conflict.status_code == 409
    assert conflict.json["error_code"] == "request_key_conflict"
    busy = auth_client.post("/api/playlist/collection/toggle-visibility", json={"public": False})
    assert busy.status_code == 409
    assert busy.json["operation_id"] == row.id
    assert auth_client.get(response.headers["Location"]).json["operation"]["id"] == row.id
    repository = OperationRepository()
    repository.finish(repository.claim(operation_id=row.id), "succeeded")
    link = RaidPlaylistLink.query.filter_by(user_id=routine_sources.id, target_playlist_id="target").one()
    link.raid_playlist_id = "changed-collection"
    db.session.commit()
    replay = auth_client.post(url, headers={"Idempotency-Key": "explicit-action"})
    assert replay.status_code == 202
    assert replay.json["operation_id"] == row.id
    assert replay.json["operation"]["targets"] == targets
    assert JobExecution.query.count() == 1
    assert ActivityLog.query.count() == 0


def test_missing_owned_routine_and_malformed_review_are_rejected_before_queue(auth_client, forbid_spotify):
    assert auth_client.post("/api/schedules/999/run").status_code == 404
    response = auth_client.post("/api/playlist/target/raid-now", json={"reviewed_scope": "invalid"})
    assert response.status_code == 400
    assert JobExecution.query.count() == 0
