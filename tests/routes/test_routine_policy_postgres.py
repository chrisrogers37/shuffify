"""Force a reviewed activation and a raid auto-watch to compete for one saved routine slot."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from config import TestConfig
from shuffify import create_app
from shuffify.models.db import RoutineActivationReceipt, Schedule, UpstreamSource, User, db
from tests.routes.test_routine_policy import activate, raid_watch, review, sign_in
from tests.routes.test_routine_policy import provider as provider
from tests.services.operations.test_repository import postgres_repository as postgres_repository


@pytest.mark.integration
@pytest.mark.parametrize("scope", ["user", "target"])
def test_two_writers_compete_for_last_routine_slot(scope, postgres_repository, sample_token, provider,
                                                  monkeypatch, tmp_path):
    isolated = postgres_repository.engine
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TestConfig, "REDIS_URL", None)
    monkeypatch.setattr(TestConfig, "SQLALCHEMY_DATABASE_URI", isolated.url.render_as_string(hide_password=False))
    app_name = f"routine-race-{uuid4().hex}"
    monkeypatch.setattr(TestConfig, "SQLALCHEMY_ENGINE_OPTIONS", {
        "execution_options": isolated.get_execution_options(), "connect_args": {"application_name": app_name},
    })
    app = create_app("testing")
    inserting, release = Event(), Event()

    def before_insert(_mapper, _connection, schedule):
        if schedule.job_type == "shuffle":
            inserting.set()
            assert release.wait(8), "The competing request did not reach its write"

    with app.app_context():
        alice = User(spotify_id="user123", display_name="Alice")
        db.session.add_all([alice, User(spotify_id="bob", display_name="Bob")])
        db.session.flush()
        count = 19 if scope == "user" else 5
        db.session.add_all(Schedule(user_id=alice.id, job_type="shuffle", is_enabled=False,
                                    target_playlist_id=f"mix{i}" if scope == "user" else "mix20") for i in range(count))
        db.session.commit()
        first = sign_in(app, "user123", sample_token)
        second = sign_in(app, "user123" if scope == "user" else "bob", sample_token)
        response, body = review(first, "mix20", create_another=True)
        assert response.status_code == 200, response.json
        event.listen(Schedule, "before_insert", before_insert)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                winner = pool.submit(activate, first, "mix20", body)
                try:
                    assert inserting.wait(4)
                    loser = pool.submit(raid_watch, second, "mix21" if scope == "user" else "mix20")
                    # Wait for real server-side contention or for an unprotected second insert.
                    with isolated.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                        blocked = False
                        deadline = time.monotonic() + 1.5
                        while not loser.done() and time.monotonic() < deadline:
                            blocked = observer.scalar(text(
                                "SELECT EXISTS (SELECT FROM pg_stat_activity WHERE application_name = :name "
                                "AND cardinality(pg_blocking_pids(pid)) > 0)"
                            ), {"name": app_name})
                            if blocked:
                                break
                            time.sleep(0.01)
                finally:
                    release.set()
                assert winner.result().status_code == 200
                refusal = loser.result()
        finally:
            event.remove(Schedule, "before_insert", before_insert)
        assert refusal.status_code == 409, refusal.json
        assert refusal.json["error_code"] == f"routine_{scope}_limit"
        assert blocked and Schedule.query.count() == count + 1
        assert RoutineActivationReceipt.query.count() == 1 and UpstreamSource.query.count() == 0
        db.session.remove()
        db.engine.dispose()
