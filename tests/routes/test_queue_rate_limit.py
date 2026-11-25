"""Exercise interactive admission through the Flask routes and a private durable queue."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from shuffify.models.db import JobExecution, Schedule, User, db
from shuffify.services import AuthService
from shuffify.services.operations.repository import ACTIVE_STATES, OperationRepository
from shuffify.services.scheduled_admission import admit_scheduled, build_cadence

SHARED_IP = {"REMOTE_ADDR": "192.0.2.10"}
OTHER_IP = {"REMOTE_ADDR": "198.51.100.20"}
SHUFFLE = "/api/shuffle/target"
VISIBILITY = "/api/playlist/target/toggle-visibility"
TRACK = "spotify:track:" + "a" * 22

# Each finalizer accepts both an enqueue and a read-only inspection of the same task.
SOURCE = {"source_playlist_id": "source", "target_track_uris": [TRACK]}
FINALIZERS = {
    "pending-raids/finalize": {**SOURCE, "track_ids": [1]},
    "pair/archive": {**SOURCE, "track_uris": [TRACK], "baseline_track_uris": []},
    "pair/finalize-restore": {**SOURCE, "track_uris": [TRACK]},
}


@pytest.fixture
def clock(monkeypatch):
    now = Mock(return_value=int(time.time()))
    monkeypatch.setattr(time, "time", now)
    return now


@pytest.fixture
def spotify_client(monkeypatch, mock_spotify_api):
    build = Mock(return_value=mock_spotify_api)
    monkeypatch.setattr(AuthService, "get_authenticated_api", build)
    return build


@pytest.fixture
def full_budget(signed_in_limiter_client, clock, spotify_client):
    alice = signed_in_limiter_client("alice")
    # Six shuffles also cross the former shuffle-only guard. Finish each row
    # locally so at most one operation is outstanding throughout this window.
    for i in range(10):
        shuffle = i % 2 == 0 or i == 9
        path = SHUFFLE if shuffle else VISIBILITY
        options = {} if shuffle else {"json": {"public": True}}
        headers = {"Idempotency-Key": f"action-{i}"}
        accepted = alice.post(path, headers=headers, environ_base=SHARED_IP, **options)
        assert accepted.status_code == 202, accepted.json
        operation_id = accepted.json["operation_id"]
        if i == 0:
            first = operation_id
            replay = alice.post(path, headers=headers)
            assert replay.status_code == 202 and replay.json["operation_id"] == first
            conflict = alice.post(VISIBILITY, json={"public": True}, headers=headers)
            assert conflict.status_code == 409 and conflict.json["error_code"] == "request_key_conflict"
        with alice.application.app_context():
            repo = OperationRepository()
            repo.finish(repo.claim(operation_id=operation_id), "succeeded")
    spotify_client.assert_not_called()
    return alice, first


def assert_limited(response):
    assert response.status_code == 429, (response.request.path, response.json)
    assert response.json["success"] is False
    assert response.json["error_code"] == "rate_limited"
    assert int(response.headers["Retry-After"]) > 0


def test_replay_identity_user_isolation_and_window_reset(full_budget, signed_in_limiter_client, clock, spotify_client):
    alice, first = full_budget
    same_user = signed_in_limiter_client("alice")
    bob = signed_in_limiter_client("bob")
    clock.return_value += 30
    original_key = {"Idempotency-Key": "action-0"}
    with patch("shuffify.routes.shuffle.capture_direct_history", autospec=True) as capture:
        blocked = alice.post(SHUFFLE, environ_base=SHARED_IP)
        assert_limited(blocked)
        assert int(blocked.headers["Retry-After"]) == 30
        assert_limited(same_user.post(SHUFFLE, environ_base=OTHER_IP))
        replay = same_user.post(SHUFFLE, headers=original_key, environ_base=OTHER_IP)
        assert replay.status_code == 202 and replay.json["operation_id"] == first
        conflict = same_user.post(VISIBILITY, json={"public": True}, headers=original_key)
        assert conflict.status_code == 409 and conflict.json["error_code"] == "request_key_conflict"
        assert alice.get(replay.headers["Location"]).json["operation"]["id"] == first
        capture.assert_not_called()
    with alice.application.app_context():
        assert JobExecution.query.count() == 10
        assert JobExecution.query.filter(JobExecution.state.in_(ACTIVE_STATES)).count() == 0

    other = bob.post("/api/shuffle/other", environ_base=SHARED_IP)
    assert other.status_code == 202
    clock.return_value += 31
    assert same_user.post(SHUFFLE, environ_base=OTHER_IP).status_code == 202
    spotify_client.assert_not_called()
    assert not spotify_client.return_value.mock_calls


def test_scheduled_fires_can_fill_capacity_while_the_interactive_window_is_exhausted(full_budget, spotify_client):
    alice, _ = full_budget
    with alice.application.app_context():
        user = User.query.filter_by(spotify_id="alice").one()
        due = datetime.now(timezone.utc)
        repo = OperationRepository()
        for i in range(5):
            schedule = Schedule(user_id=user.id, job_type="shuffle", target_playlist_id=f"scheduled{i}",
                                algorithm_name="BasicShuffle", schedule_type="interval", schedule_value="daily",
                                timezone="UTC", created_at=due - timedelta(days=2))
            db.session.add(schedule)
            db.session.commit()
            fire = admit_scheduled(repo, schedule.id, build_cadence(schedule).fingerprint, due)
            assert fire.state == "admitted"

    at_capacity = alice.post(SHUFFLE)
    assert at_capacity.status_code == 429 and at_capacity.json["error_code"] == "outstanding_operation_limit"
    assert "Retry-After" not in at_capacity.headers
    with alice.application.app_context():
        repo.finish(repo.claim(operation_id=fire.operation_id), "succeeded")
    assert_limited(alice.post(SHUFFLE))
    spotify_client.assert_not_called()


def test_every_queue_route_obeys_the_exhausted_budget(full_budget, spotify_client):
    alice, parent = full_budget
    review = {"version": 1, "reviewed_scope": "a" * 64,
              "reviewed_resources": [{"playlist_id": "target", "digest": "b" * 64}]}
    paths = {
        SHUFFLE: {},
        VISIBILITY: {"public": True},
        "/api/undo/target": {},
        "/api/workshop/target/commit": {"baseline_track_uris": [], "track_uris": [TRACK]},
        "/api/snapshots/1/restore": {},
        f"/api/operations/{parent}/restore": {"reviewed": review},
        "/api/schedules/1/run": {},
    }
    playlist_paths = {
        "setup/spotify-playlists": {"purpose": "archive", "name": "Archive"},
        "pair": {"create_new": True},
        "pair/unarchive": {"track_uris": [TRACK]},
        "raid-link": {"create_new": True},
        "raid-now": {},
        "drip-now": {},
        "raid-and-drip": {},
        "pending-raids/dismiss": {"track_ids": [1]},
        "pending-raids/dismiss-all": {},
        **FINALIZERS,
    }
    paths.update({f"/api/playlist/target/{path}": body for path, body in playlist_paths.items()})
    for path, body in paths.items():
        assert_limited(alice.post(path, json=body))
    spotify_client.assert_not_called()

    for path, body in FINALIZERS.items():
        inspected = alice.post(f"/api/playlist/target/{path}", json={**body, "inspect_only": True})
        # Inspection reports the missing source link in this database.
        assert inspected.status_code == 409 and inspected.json["error_code"] == "source_changed"
    assert spotify_client.call_count == len(FINALIZERS)
    with alice.application.app_context():
        assert JobExecution.query.count() == 10
