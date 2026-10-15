"""Crash recovery crosses the real queue, worker, API batching, and journal seams."""

import json as json_module
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
import requests
from sqlalchemy import update

from shuffify.models.db import JobExecution, User, db
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.spotify.exceptions import SpotifyWriteRejectedError
from shuffify.worker import OperationWorker

PLAYLIST = "A" * 22


class ProcessStopped(BaseException):
    pass


class SpotifyWorld:
    def __init__(self, uris):
        self.uris = list(uris)
        self.snapshot = 1
        self.calls = 0
        self.attempts = 0
        self.cut = None
        self.retry_after = None
        self.stop_before_apply = False
        self.settled = True
        self.reject_once = False
        self.reject_on_attempt = None
        self.crash_read = False
        self.metadata = {"name": "Alice mix", "public": False, "description": "Example playlist"}

    def observe(self, playlist_id):
        assert playlist_id == PLAYLIST
        if self.crash_read:
            self.crash_read = False
            raise ProcessStopped()
        return {"track_uris": list(self.uris), "snapshot_id": str(self.snapshot),
                "metadata": dict(self.metadata), "settled": self.settled}

    def send(self, method, url, *, params=None, json=None, timeout=None):
        path = urlsplit(url).path.removeprefix("/v1")
        assert timeout > 0
        self.attempts += 1
        if self.reject_once or self.attempts == self.reject_on_attempt:
            self.reject_once = False
            response = requests.Response()
            response.status_code = 400
            response._content = b'{"error":{"status":400,"message":"Example rejection"}}'
            return response
        if self.retry_after is not None:
            response = requests.Response()
            response.status_code = 429
            response.headers["Retry-After"] = str(self.retry_after)
            response._content = b'{"error":{"status":429,"message":"Example rate limit"}}'
            return response
        if self.stop_before_apply:
            self.stop_before_apply = False
            raise ProcessStopped()
        if path.endswith("/items"):
            if method == "PUT":
                self.uris = list(json["uris"])
            elif method == "POST":
                position = json.get("position", len(self.uris))
                self.uris[position:position] = json["uris"]
            else:
                raise AssertionError(f"Unexpected method {method}")
        else:
            assert method == "PUT" and path == f"/playlists/{PLAYLIST}"
            self.metadata.update(json)
        self.snapshot += 1
        self.calls += 1
        if self.calls == self.cut:
            raise ProcessStopped()
        response = requests.Response()
        response.status_code = 200
        response._content = json_module.dumps({"snapshot_id": str(self.snapshot)}).encode()
        return response


@pytest.fixture
def setup_worker(db_app, monkeypatch, sample_token):
    user = User(spotify_id="alice", display_name="Alice")
    db.session.add(user)
    db.session.commit()
    repository = OperationRepository()

    def build(world, handler):
        def api_factory(_user):
            api = SpotifyAPI(TokenInfo.from_dict(sample_token), auto_refresh=False)
            monkeypatch.setattr(api._http._session, "request", world.send)
            return api

        monkeypatch.setattr(OperationRuntime, "read_resource", lambda self, key: world.observe(key))
        return OperationWorker(db_app, repository=repository, handlers={"example": handler},
                               api_factory=api_factory, heartbeat_interval=0)

    def accept():
        return repository.enqueue(kind="example", user_id=user.id, targets=[PLAYLIST], payload={"version": 1})

    def expire(operation_id):
        with repository.engine.begin() as connection:
            connection.execute(update(JobExecution).where(JobExecution.id == operation_id).values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
            ))

    return repository, build, accept, expire


@pytest.mark.parametrize("cut", [1, 2, 3])
def test_worker_recovery_reuses_frozen_replacement_after_response_loss(setup_worker, cut):
    repository, build, accept, expire = setup_worker
    baseline = [f"spotify:track:{index:022d}" for index in range(250)]
    desired = list(reversed(baseline))
    world = SpotifyWorld(baseline)
    world.cut = cut

    def handler(runtime):
        frozen = runtime.prepare("desired", lambda: desired)
        return runtime.step("replace", lambda: {"success": runtime.api.update_playlist_tracks(PLAYLIST, frozen)})

    accepted = accept()
    with pytest.raises(ProcessStopped):
        build(world, handler).run_once()
    expire(accepted.id)
    world.cut = None
    assert build(world, handler).run_once() is True
    assert repository.get(accepted.id).state == "succeeded"
    assert world.uris == desired
    assert world.calls == 3


def test_cached_step_advances_its_plans_before_resuming_the_next_step(setup_worker):
    repository, build, accept, expire = setup_worker
    world = SpotifyWorld(["spotify:track:baseline"])
    world.cut = 2

    def handler(runtime):
        runtime.step("first", lambda: {"success": runtime.api.playlist_add_items(PLAYLIST, ["spotify:track:first"])})
        return runtime.step("second", lambda: {
            "success": runtime.api.playlist_add_items(PLAYLIST, ["spotify:track:second"]),
        })

    accepted = accept()
    with pytest.raises(ProcessStopped):
        build(world, handler).run_once()
    expire(accepted.id)
    world.cut = None
    assert build(world, handler).run_once() is True
    assert repository.get(accepted.id).state == "succeeded"
    assert world.uris == ["spotify:track:baseline", "spotify:track:first", "spotify:track:second"]
    assert world.calls == 2


def test_worker_recovery_preserves_an_external_edit(setup_worker):
    repository, build, accept, expire = setup_worker
    world = SpotifyWorld(["spotify:track:baseline"])
    world.cut = 1

    def handler(runtime):
        return {"success": runtime.api.update_playlist_tracks(PLAYLIST, ["spotify:track:desired"])}

    accepted = accept()
    with pytest.raises(ProcessStopped):
        build(world, handler).run_once()
    expire(accepted.id)
    world.cut = None
    world.uris = ["spotify:track:external-edit"]
    world.snapshot += 1
    assert build(world, handler).run_once() is True
    assert repository.get(accepted.id).state == "needs_attention"
    assert world.uris == ["spotify:track:external-edit"]
    assert world.calls == 1


def test_worker_crash_during_compensation_resumes_only_the_rollback(setup_worker):
    repository, build, accept, expire = setup_worker
    baseline = ["spotify:track:baseline"]
    world = SpotifyWorld(baseline)
    world.cut = 2
    handler_calls = []

    def handler(runtime):
        handler_calls.append(True)
        runtime.api.update_playlist_tracks(PLAYLIST, ["spotify:track:desired"])
        raise ValueError("Example failure after the confirmed write")

    accepted = accept()
    with pytest.raises(ProcessStopped):
        build(world, handler).run_once()
    assert repository.get(accepted.id).state == "compensating"
    expire(accepted.id)
    world.cut = None
    assert build(world, handler).run_once() is True
    assert repository.get(accepted.id).state == "rolled_back"
    assert world.uris == baseline
    assert handler_calls == [True]


def test_worker_defers_a_rate_limited_plan_until_its_durable_deadline(setup_worker):
    repository, build, accept, expire = setup_worker
    world = SpotifyWorld(["spotify:track:baseline"])
    world.retry_after = 120

    def handler(runtime):
        return runtime.step("replace", lambda: {
            "success": runtime.api.update_playlist_tracks(PLAYLIST, ["spotify:track:desired"]),
        })

    accepted = accept()
    assert build(world, handler).run_once() is False
    row = repository.get(accepted.id)
    assert row.state == "running"
    assert row.completed_at is None and row.lease_owner is None
    assert row.lease_expires_at > datetime.now(timezone.utc) + timedelta(seconds=100)
    assert world.attempts == 1 and world.calls == 0
    assert build(world, handler).run_once() is False
    assert world.attempts == 1

    # Another wake and an expired claim cannot override the persisted provider hint.
    expire(accepted.id)
    assert build(world, handler).run_once() is False
    assert world.attempts == 1
    world.retry_after = None
    row = repository.get(accepted.id)
    journal = row.journal
    for plan in journal["plans"]:
        for batch in plan["batches"]:
            batch["next_attempt_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with repository.engine.begin() as connection:
        connection.execute(update(JobExecution).where(JobExecution.id == accepted.id).values(journal=journal))
    expire(accepted.id)
    assert build(world, handler).run_once() is True
    assert repository.get(accepted.id).state == "succeeded"
    assert world.uris == ["spotify:track:desired"]
    assert world.attempts == 2


def test_worker_defers_rate_limited_compensation_without_finishing_it(setup_worker):
    repository, build, accept, expire = setup_worker
    baseline = ["spotify:track:baseline"]
    world = SpotifyWorld(baseline)

    def handler(runtime):
        runtime.api.update_playlist_tracks(PLAYLIST, ["spotify:track:desired"])
        world.retry_after = 120
        raise ValueError("Example failure requiring compensation")

    accepted = accept()
    assert build(world, handler).run_once() is False
    row = repository.get(accepted.id)
    assert row.state == "compensating"
    assert row.completed_at is None and row.lease_owner is None
    assert row.lease_expires_at > datetime.now(timezone.utc) + timedelta(seconds=100)
    assert world.calls == 1 and world.attempts == 2


def test_ambiguous_baseline_without_provider_settlement_stays_untouched(setup_worker):
    repository, build, accept, expire = setup_worker
    baseline = ["spotify:track:baseline"]
    world = SpotifyWorld(baseline)
    world.stop_before_apply = True
    world.settled = False

    def handler(runtime):
        return {"success": runtime.api.update_playlist_tracks(PLAYLIST, ["spotify:track:desired"])}

    accepted = accept()
    with pytest.raises(ProcessStopped):
        build(world, handler).run_once()
    expire(accepted.id)
    assert build(world, handler).run_once() is True
    assert repository.get(accepted.id).state == "needs_attention"
    assert world.uris == baseline
    assert world.calls == 0 and world.attempts == 1


def test_caught_service_failure_never_resumes_the_rejected_forward_write(setup_worker):
    repository, build, accept, expire = setup_worker
    baseline = ["spotify:track:baseline"]
    world = SpotifyWorld(baseline)
    world.reject_once = True

    def handler(runtime):
        try:
            runtime.api.update_playlist_tracks(PLAYLIST, ["spotify:track:desired"])
        except SpotifyWriteRejectedError:
            return {"success": False, "message": "The save was rejected.", "target_status": "not_written"}
        raise AssertionError("The first write must be rejected")

    accepted = accept()
    assert build(world, handler).run_once() is True
    row = repository.get(accepted.id)
    assert row.state == "failed"
    assert row.journal["result"]["target_status"] == "not_written"
    assert world.uris == baseline
    assert world.calls == 0 and world.attempts == 1


def test_crash_before_compensation_planning_retains_the_abort_direction(setup_worker):
    repository, build, accept, expire = setup_worker
    baseline = [f"spotify:track:{index:022d}" for index in range(250)]
    desired = list(reversed(baseline))
    world = SpotifyWorld(baseline)
    world.reject_on_attempt = 2
    handler_calls = []

    def handler(runtime):
        handler_calls.append(True)
        try:
            runtime.api.update_playlist_tracks(PLAYLIST, desired)
        except SpotifyWriteRejectedError:
            world.crash_read = True
            return {"success": False, "message": "The save did not complete."}
        raise AssertionError("The second batch must be rejected")

    accepted = accept()
    with pytest.raises(ProcessStopped):
        build(world, handler).run_once()
    assert repository.get(accepted.id).journal["phase"] == "forward"
    assert world.calls == 1
    expire(accepted.id)
    assert build(world, handler).run_once() is True
    assert repository.get(accepted.id).state == "rolled_back"
    assert world.uris == baseline
    assert handler_calls == [True]
