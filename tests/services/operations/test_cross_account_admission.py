"""Exercise the account boundary from submission to the first authenticated write."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import urlsplit

import pytest
from flask import Flask
from requests import Response

from shuffify.models.db import Schedule, User, db
from shuffify.services.operations.repository import BusyOperation, OperationRepository
from shuffify.spotify.http_client import SpotifyHTTPClient
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import Crash, World, expire_lease, uri
from tests.services.operations.test_queued_direct_services import api_for
from tests.services.operations.test_repository import postgres_repository as postgres_repository
from tests.services.operations.test_repository import race
from tests.services.operations.test_repository import repository as repository
from tests.services.operations.test_scheduled_admission import submit


@pytest.mark.parametrize("submission", ["manual", "scheduled"])
@pytest.mark.parametrize("when", ["queued", "first_write"])
def test_owner_can_submit_while_another_account_lacks_edit_access(auth_client, db_app, submission, when):
    repo = OperationRepository()
    bob = User.query.filter_by(spotify_id="user123").one()
    alice = User(spotify_id="alice")
    db.session.add(alice)
    db.session.flush()
    schedule = Schedule(user_id=bob.id, job_type="shuffle", target_playlist_id="target",
                        algorithm_name="BasicShuffle", schedule_type="interval", schedule_value="daily")
    db.session.add(schedule)
    db.session.commit()
    attempts, admissions = [], []

    def submit_as_bob():
        if submission == "manual":
            response = auth_client.post("/api/shuffle/target", data={"algorithm": "BasicShuffle"})
            admissions.append(response.status_code)
        else:
            admissions.append(submit(repo, schedule).state)

    def forbidden(user, method, path, body):
        attempts.append(user.spotify_id)
        if when == "first_write":
            submit_as_bob()
        return 403

    row = repo.enqueue(kind="add", user_id=alice.id, targets=["target"], payload={})
    if when == "queued":
        submit_as_bob()
    runner, world = make_worker(db_app, repo, forbidden)
    assert runner.run_once(row.id)
    assert attempts == ["alice"]
    assert admissions == [202 if submission == "manual" else "admitted"]
    assert repo.get(row.id).state == "failed"
    assert not world.writes


def make_worker(app, repo, before_write):
    world = World()
    world.playlists["target"]["metadata"]["description"] = None

    def factory(user):
        api = api_for(world)
        metadata = api.get_playlist
        api.get_playlist = lambda *a, **kw: dict(metadata(*a, **kw), owner={"id": "user123"}, collaborative=True)
        api._http = SpotifyHTTPClient("fixture")

        def request(method, url, **kwargs):
            path, body = urlsplit(url).path.removeprefix("/v1"), kwargs["json"]
            response = Response()
            response.status_code = before_write(user, method, path, body)
            if response.ok:
                response._content = json.dumps(world.dispatch(method, path, body)).encode()
            else:
                response._content = b'{"error":{"message":"Not an editor"}}'
            return response

        api._http._session.request = request
        return api

    def add(runtime):
        runtime.api.playlist_add_items("target", [uri(runtime.user.id)])
        return {"success": True}

    return OperationWorker(app, repository=repo, handlers={"add": add}, api_factory=factory,
                           heartbeat_interval=0), world


@pytest.fixture(params=["repository", pytest.param("postgres_repository", marks=pytest.mark.integration)])
def accounts(request):
    repo = request.getfixturevalue(request.param)
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI=repo.engine.url, SQLALCHEMY_ENGINE_OPTIONS={
        "execution_options": dict(repo.engine.get_execution_options()),
    })
    db.init_app(app)
    with app.app_context():
        db.session.add_all([User(id=1, spotify_id="alice"), User(id=2, spotify_id="bob")])
        db.session.commit()
    yield app, repo
    with app.app_context():
        db.engine.dispose()


def test_two_collaborators_admitted_before_proof_write_in_sequence(accounts):
    app, repo = accounts
    submissions = race(lambda i: repo.enqueue(kind="add", user_id=i + 1, targets=["target"], payload={}))
    assert all(not isinstance(row, BusyOperation) for row in submissions)
    first, second = sorted(submissions, key=lambda row: row.id)
    writing, release = Event(), Event()
    writers = []

    def before_write(user, method, path, body):
        writers.append(user.id)
        if user.id == first.user_id:
            writing.set()
            assert release.wait(10)
        return 201

    runner, world = make_worker(app, repo, before_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(runner.run_once, first.id)
        try:
            assert writing.wait(10)
            assert pool.submit(runner.run_once, second.id).result(timeout=5) is False
            assert writers == [first.user_id]
        finally:
            release.set()
        assert active.result(timeout=10)
    expire_lease(repo, second.id)
    assert runner.run_once(second.id)
    assert [repo.get(row.id).state for row in (first, second)] == ["succeeded", "succeeded"]
    assert writers == [first.user_id, second.user_id]
    assert world.playlists["target"]["track_uris"] == [uri(first.user_id), uri(second.user_id)]


def test_confirmed_write_keeps_its_reservation_across_worker_loss(accounts, monkeypatch):
    app, repo = accounts
    # Either worker may get there first. A queued predecessor does not own a turn.
    waiting, first = [repo.enqueue(kind="add", user_id=i, targets=["target"], payload={}) for i in (1, 2)]
    runner, world = make_worker(app, repo, lambda *_: 201)
    checkpoint = repo.checkpoint

    def stop_after_ack(lease, journal, **kwargs):
        row = checkpoint(lease, journal, **kwargs)
        if any(batch.get("last_http_status") == 201 for step in journal.get("plans", []) for batch in step["batches"]):
            raise Crash
        return row

    with monkeypatch.context() as patch:
        patch.setattr(repo, "checkpoint", stop_after_ack)
        with pytest.raises(Crash):
            runner.run_once(first.id)
    with pytest.raises(BusyOperation) as error:
        repo.enqueue(kind="add", user_id=3, targets=["target"], payload={})
    assert error.value.operation_id == first.id
    assert runner.run_once(waiting.id) is False
    assert len(world.writes) == 1
    expire_lease(repo, first.id)
    assert runner.run_once(first.id)
    expire_lease(repo, waiting.id)
    assert runner.run_once(waiting.id)
    assert world.playlists["target"]["track_uris"] == [uri(first.user_id), uri(waiting.user_id)]
    assert len(world.writes) == 2


@pytest.mark.parametrize("written", [True, False])
def test_waiting_worker_respects_attention_raised_after_both_submissions(accounts, written):
    app, repo = accounts
    first, waiting = [repo.enqueue(kind="add", user_id=i, targets=["target"], payload={}) for i in (1, 2)]
    lease = repo.claim(operation_id=first.id)
    if written:
        repo.checkpoint(lease, {"plans": [{"resource": "target", "batches": [{"last_http_status": 200}]}]})
    repo.finish(lease, "needs_attention")
    runner, world = make_worker(app, repo, lambda *_: 201)
    assert runner.run_once(waiting.id)
    row = repo.get(waiting.id)
    if not written:
        assert row.state == "succeeded" and len(world.writes) == 1
        return
    assert (row.state, row.journal["result"]["error_code"]) == ("failed", "playlist_needs_review")
    result = row.journal["result"]
    assert (result["target_status"], result["confirmed_batches"], result["total_batches"]) == ("not_written", 0, 0)
    assert "operation_id" not in result
    assert not world.writes


def test_crash_before_permission_check_resumes_without_blocking_admission(accounts):
    app, repo = accounts
    first = repo.enqueue(kind="add", user_id=1, targets=["target"], payload={})
    lease = repo.claim(operation_id=first.id)
    with repo.target_locks(lease.targets) as acquired:
        assert acquired
        assert repo.begin_execution(lease, first) is None
    # Process loss immediately after opening the journal, before the first
    # provider read. Its successor can submit but must wait to start writing.
    second = repo.enqueue(kind="add", user_id=2, targets=["target"], payload={})
    runner, world = make_worker(app, repo, lambda *_: 201)
    assert runner.run_once(second.id) is False
    assert not world.writes
    expire_lease(repo, first.id)
    assert runner.run_once(first.id)
    expire_lease(repo, second.id)
    assert runner.run_once(second.id)
    assert world.playlists["target"]["track_uris"] == [uri(1), uri(2)]


def test_sqlite_workers_with_independent_target_locks_open_only_one_journal(repository):
    rows = [repository.enqueue(kind="add", user_id=i, targets=["target"], payload={}) for i in (1, 2)]
    leases = [repository.claim(operation_id=row.id) for row in rows]
    # Separate processes cannot see each other's in-memory target locks. Exercise
    # just the database arbitration on independent connections.
    turns = race(lambda i: repository.begin_execution(leases[i], rows[i]))
    assert turns.count(None) == turns.count("busy") == 1
    assert sum(bool(repository.get(row.id).journal) for row in rows) == 1
