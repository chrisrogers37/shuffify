"""Durable reads yield without rolling back known writes or truncating large replacements."""

import json as json_module
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
import requests
from sqlalchemy import update

from shuffify.models.db import JobExecution, User, db
from shuffify.services.operations.journal import OperationDeferred
from shuffify.services.operations.repository import OperationRepository
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.spotify.deadline import SpotifyDeadlineError
from shuffify.spotify.exceptions import SpotifyAPIError, SpotifyQuotaExceededError
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import World, uri

TARGET = "A" * 22


class Provider:
    """The real HTTP client sees complete pages and actual 100-item wire batches."""

    def __init__(self, tracks, clock):
        self.tracks, self.clock = list(tracks), clock
        self.snapshot, self.latency, self.rejections = 0, 0, 0
        self.calls, self.writes = Counter(), []
        self.read_failure, self.failure_remaining, self.reject_write = None, 0, None

    def send(self, method, url, *, params=None, json=None, **kwargs):
        path = urlsplit(url).path
        self.calls[(method, "items" if path.endswith("/items") else "metadata")] += 1
        self.clock[0] += self.latency if method == "GET" else 0.1
        if method == "GET" and self.writes and self.failure_remaining:
            self.failure_remaining -= 1
            if self.read_failure == "deadline":
                raise SpotifyDeadlineError("The read budget expired.")
            if self.read_failure == "network":
                raise requests.ConnectionError("Read connection lost.")
            if self.read_failure in {429, 503}:
                response = requests.Response()
                response.status_code = self.read_failure
                response._content = b'{"error":{"message":"Temporary read failure"}}'
                if self.read_failure == 429:
                    response.headers["Retry-After"] = "120"
                return response
        response = requests.Response()
        response.status_code = 200
        if method == "GET":
            if path.endswith("/items"):
                offset, limit = params["offset"], params["limit"]
                selected = self.tracks[offset:offset + limit]
                end = offset + len(selected)
                body = {"items": [{"track": {"uri": track, "type": "track"}} for track in selected],
                        "offset": offset, "total": len(self.tracks),
                        "next": f"https://api.spotify.com/v1/playlists/{TARGET}/items?offset={end}"
                        if end < len(self.tracks) else None}
            else:
                body = {"id": TARGET, "name": "Queue test", "owner": {"id": "alice"},
                        "collaborative": False, "snapshot_id": str(self.snapshot),
                        "public": False, "description": "", "tracks": {"total": len(self.tracks)}}
        else:
            if self.reject_write == len(self.writes) + 1 and not self.rejections:
                self.rejections += 1
                response.status_code = 400
                response._content = b'{"error":{"message":"Known batch rejection"}}'
                return response
            self.writes.append((method, deepcopy(json)))
            if method == "PUT":
                self.tracks = list(json["uris"])
            else:
                self.tracks.extend(json["uris"])
            self.snapshot += 1
            body = {"snapshot_id": str(self.snapshot)}
        response._content = json_module.dumps(body).encode()
        return response


@pytest.fixture
def environment(db_app, sample_token, monkeypatch):
    user = User(spotify_id="alice")
    db.session.add(user)
    db.session.commit()
    repository, clock = OperationRepository(), [0.0]
    monkeypatch.setattr("shuffify.spotify.deadline.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("shuffify.spotify.http_client.time.sleep",
                        lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def run(provider, desired, *, budget=240):
        def api_factory(_user):
            api = SpotifyAPI(TokenInfo.from_dict(sample_token), auto_refresh=False)
            monkeypatch.setattr(api._http._session, "request", provider.send)
            return api

        def handler(runtime):
            frozen = runtime.prepare("desired", lambda: desired)
            return runtime.step("write", lambda: {"success": runtime.api.update_playlist_tracks(TARGET, frozen)})

        return OperationWorker(db_app, repository=repository, handlers={"example": handler},
                               api_factory=api_factory, heartbeat_interval=0, operation_timeout=budget)

    def accept():
        return repository.enqueue(kind="example", user_id=user.id, targets=[TARGET], payload={"version": 1})

    def due(operation_id, *, clear_read_retry=True):
        row = repository.get(operation_id)
        document = deepcopy(row.journal)
        if clear_read_retry:
            document.pop("read_retry", None)
        with repository.engine.begin() as connection:
            connection.execute(update(JobExecution).where(JobExecution.id == operation_id).values(
                journal=document, lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ))

    return repository, clock, run, accept, due


def test_two_thousand_track_replacement_fits_budget_with_one_snapshot_guard_per_batch(environment):
    repository, clock, run, accept, _due = environment
    before = [uri(index) for index in range(2000)]
    desired = list(reversed(before))
    provider = Provider(before, clock)
    provider.latency = 0.5
    row = accept()
    assert run(provider, desired).run_once(row.id) is True
    assert repository.get(row.id).state == "succeeded"
    assert provider.tracks == desired
    assert len(provider.writes) == 20
    assert provider.calls[("GET", "items")] <= 320
    assert sum(n for (method, _), n in provider.calls.items() if method == "GET") <= 370
    assert clock[0] < 240


@pytest.mark.parametrize("failure,attempts", [("deadline", 1), (429, 1), (503, 5), ("network", 5)])
def test_read_failure_after_first_ack_defers_same_operation_then_completes(environment, failure, attempts):
    repository, clock, run, accept, due = environment
    before = [uri(index) for index in range(250)]
    desired = list(reversed(before))
    provider = Provider(before, clock)
    provider.read_failure, provider.failure_remaining = failure, attempts
    row = accept()
    runner = run(provider, desired)
    assert runner.run_once(row.id) is False
    deferred = repository.get(row.id)
    assert deferred.state == "running"
    assert deferred.completed_at is None
    assert provider.tracks == desired[:100]
    assert len(provider.writes) == 1
    assert deferred.journal["read_retry"]["next_attempt_at"]
    if failure == 429:
        deadline = datetime.fromisoformat(deferred.journal["read_retry"]["next_attempt_at"])
        assert (deadline - datetime.now(timezone.utc)).total_seconds() > 119
    due(row.id, clear_read_retry=False)
    calls = deepcopy(provider.calls)
    assert runner.run_once(row.id) is False
    assert provider.calls == calls
    due(row.id)
    assert runner.run_once(row.id) is True
    assert repository.get(row.id).state == "succeeded"
    assert provider.tracks == desired
    assert len(provider.writes) == 3


@pytest.mark.parametrize("failure,attempts", [(429, 1), (503, 5), ("network", 5), ("deadline", 1)])
def test_compensation_read_transient_yields_and_later_restores_exact_baseline(environment, failure, attempts):
    repository, clock, run, accept, due = environment
    before = [uri(index) for index in range(250)]
    desired = list(reversed(before))
    provider = Provider(before, clock)
    provider.reject_write = 2
    original = provider.send

    def send(*args, **kwargs):
        if provider.rejections and not provider.read_failure:
            provider.read_failure, provider.failure_remaining = failure, attempts
        return original(*args, **kwargs)

    provider.send = send
    row = accept()
    runner = run(provider, desired)
    assert runner.run_once(row.id) is False
    deferred = repository.get(row.id)
    assert deferred.state == "compensating"
    assert deferred.completed_at is None
    assert provider.tracks == desired[:100]
    assert len(provider.writes) == 1
    due(row.id)
    assert runner.run_once(row.id) is True
    assert repository.get(row.id).state == "rolled_back"
    assert provider.tracks == before
    assert len(provider.writes) == 4


def test_exhausted_read_transport_is_typed(monkeypatch):
    from shuffify.spotify.http_client import SpotifyHTTPClient

    client = SpotifyHTTPClient("fixture")
    attempts = []

    def send(*args, **kwargs):
        attempts.append(args)
        response = requests.Response()
        response.status_code = 503
        response._content = b'{}'
        return response

    monkeypatch.setattr(client._session, "request", send)
    monkeypatch.setattr("shuffify.spotify.http_client.time.sleep", lambda seconds: None)
    with pytest.raises(SpotifyAPIError) as failure:
        client.get("/playlists/target")
    assert type(failure.value).__name__ == "SpotifyReadTransientError"
    assert len(attempts) == 5


def test_read_quota_is_not_reclassified_as_transient():
    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(2)])
    journal.read_resource = lambda resource: (_ for _ in ()).throw(SpotifyQuotaExceededError("quota", retry_after=120))
    with pytest.raises(SpotifyQuotaExceededError) as error:
        journal.resume()
    assert not isinstance(error.value, OperationDeferred)
    assert "read_retry" not in (world.saved or {})


def test_snapshot_guard_matches_known_prefix_but_mismatch_requires_full_read_and_stops_next_batch():
    world = World([uri(1)])
    reads, guards = [], []
    original = world.read

    def read(resource):
        reads.append(resource)
        return original(resource)

    def snapshot(resource):
        guards.append(resource)
        if len(world.writes) == 1:
            # Even identical contents at a new revision belong to another writer.
            world.playlists[resource]["snapshot_id"] = "external-revision"
        return world.playlists[resource]["snapshot_id"]

    journal = world.journal()
    journal.read_resource, journal.read_snapshot = read, snapshot
    journal.freeze_replace("target", [uri(index) for index in range(250)])
    assert journal.resume()["state"] == "needs_attention"
    assert len(world.writes) == 1
    assert len(guards) == 2
    assert len(reads) == 3  # Frozen baseline, full reconciliation, mismatched snapshot fallback.
    assert world.playlists["target"]["snapshot_id"] == "external-revision"


def test_snapshot_guard_never_reconciles_an_ambiguous_append_or_metadata_only_write():
    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_add("target", [uri(2)])
    item = journal.doc["plans"][0]["batches"][0]
    item.update(state="dispatched", attempts=1)
    world.saved = deepcopy(journal.doc)
    world.playlists["target"]["settled"] = False
    reads = []
    journal.read_snapshot = lambda resource: pytest.fail("An unresolved dispatch needs complete content")
    journal.read_resource = lambda resource: reads.append(resource) or world.read(resource)
    assert journal.has_ambiguous_write() is True
    assert journal.resume()["state"] == "needs_attention"
    assert reads
    assert world.playlists["target"]["track_uris"] == [uri(1)]
    assert world.writes == []

    metadata_world = World([uri(1)])
    metadata = metadata_world.journal()
    metadata.read_metadata = lambda resource: {"metadata": metadata_world.read(resource)["metadata"]}
    metadata.freeze_details("target", public=True)
    metadata.read_snapshot = lambda resource: pytest.fail("Track revision cannot prove metadata")
    assert metadata.resume()["state"] == "succeeded"


def test_repeated_read_failure_persists_the_new_later_hint_instead_of_old_retry_metadata():
    world = World([uri(1)])
    journal = world.journal()
    with pytest.raises(OperationDeferred):
        journal.defer_read(SpotifyDeadlineError("First expired budget"))
    earlier = datetime.now(timezone.utc) - timedelta(seconds=1)
    world.saved["read_retry"]["next_attempt_at"] = earlier.isoformat()
    recovered = world.journal()
    from shuffify.spotify.exceptions import SpotifyRateLimitError

    with pytest.raises(OperationDeferred) as failure:
        recovered.defer_read(SpotifyRateLimitError("Wait", retry_after=120))
    assert world.saved["read_retry"]["next_attempt_at"] == failure.value.next_attempt_at.isoformat()
    assert datetime.fromisoformat(world.saved["read_retry"]["next_attempt_at"]) > earlier + timedelta(seconds=120)


@pytest.mark.parametrize("fault", ["network", "json"])
def test_exhausted_safe_read_failures_are_typed_without_changing_write_ambiguity(monkeypatch, fault):
    from shuffify.spotify.exceptions import SpotifyWriteUnverifiedError
    from shuffify.spotify.http_client import SpotifyHTTPClient

    client = SpotifyHTTPClient("fixture")
    calls = []

    def send(*args, **kwargs):
        calls.append(args)
        if fault == "network":
            raise requests.ConnectionError("connection lost")
        response = requests.Response()
        response.status_code = 200
        response._content = b"invalid JSON"
        return response

    monkeypatch.setattr(client._session, "request", send)
    monkeypatch.setattr("shuffify.spotify.http_client.time.sleep", lambda seconds: None)
    with pytest.raises(SpotifyAPIError) as error:
        client.get("/playlists/target")
    assert type(error.value).__name__ == "SpotifyReadTransientError"
    assert len(calls) == 5
    calls.clear()
    with pytest.raises(SpotifyWriteUnverifiedError):
        client.post("/playlists/target/items", json={"uris": [uri(1)]})
    assert len(calls) == 1


def test_typed_read_exhaustion_escapes_business_catch_via_active_observer():
    from shuffify.services.operations.control import propagate_control
    from shuffify.spotify.exceptions import SpotifyReadTransientError

    world = World([uri(1)])
    journal = world.journal()
    with journal.observe(), pytest.raises(OperationDeferred):
        try:
            raise SpotifyReadTransientError("Read retries exhausted")
        except SpotifyAPIError as error:
            propagate_control(error)
            pytest.fail("A service must not turn transient reads into a permanent outcome")
    assert world.saved["read_retry"]["phase"] == "forward"
    assert world.writes == []


@pytest.mark.parametrize("previous_attempts", [0, 1])
def test_expired_budget_before_actual_wire_dispatch_restores_known_state_and_defers(previous_attempts):
    from shuffify.spotify.deadline import spotify_deadline
    from shuffify.spotify.http_client import SpotifyHTTPClient

    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(2)])
    item = journal.doc["plans"][0]["batches"][0]
    prior_state = "rejected" if previous_attempts else "prepared"
    item.update(state=prior_state, attempts=previous_attempts)
    world.saved = deepcopy(journal.doc)
    client = SpotifyHTTPClient("fixture")
    client._session.request = lambda *args, **kwargs: pytest.fail("An expired budget cannot reach the provider")

    def send(method, path, body):
        with spotify_deadline(0):
            return client._request(method, path, json=body)

    journal.dispatch = send
    with pytest.raises(OperationDeferred):
        journal.resume()
    saved = world.saved["plans"][0]["batches"][0]
    assert saved["state"] == prior_state
    assert saved["attempts"] == previous_attempts
    assert journal.has_ambiguous_write() is False
    assert world.writes == []


def test_snapshot_guard_checks_metadata_from_same_get_before_another_track_batch(environment):
    repository, clock, run, accept, _due = environment
    before = [uri(index) for index in range(250)]
    provider = Provider(before, clock)
    original = provider.send

    def send(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if method == "GET" and not urlsplit(url).path.endswith("/items") and provider.writes:
            metadata = response.json()
            metadata["name"] = "Externally renamed playlist"
            response._content = json_module.dumps(metadata).encode()
        return response

    provider.send = send
    row = accept()
    assert run(provider, list(reversed(before))).run_once(row.id) is True
    assert repository.get(row.id).state == "needs_attention"
    assert len(provider.writes) == 1
    assert provider.tracks == list(reversed(before))[:100]
