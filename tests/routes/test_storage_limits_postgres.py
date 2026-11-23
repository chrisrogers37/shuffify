"""Observe actual PostgreSQL contention between fenced worker storage and an HTTP save."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from config import TestConfig
from shuffify import create_app
from shuffify.models.db import PlaylistSnapshot, User, WorkshopSession, db
from tests.mutable_spotify import MutableSpotify, uri
from tests.queued_operations import run_operation
from tests.routes.test_routine_policy import sign_in
from tests.routes.test_storage_limits import PLAYLISTS, SNAPSHOTS, save, seed
from tests.services.operations.test_repository import postgres_repository as postgres_repository


@pytest.mark.integration
@pytest.mark.parametrize("other_store", [PlaylistSnapshot, WorkshopSession])
def test_fenced_workshop_snapshot_and_another_playlist_compete_for_last_slot(
    postgres_repository, other_store, tmp_path, sample_token, monkeypatch,
):
    engine = postgres_repository.engine
    application = "storage-race-" + uuid4().hex
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TestConfig, "REDIS_URL", None)
    monkeypatch.setattr(TestConfig, "SQLALCHEMY_DATABASE_URI", engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(TestConfig, "SQLALCHEMY_ENGINE_OPTIONS", {
        "execution_options": engine.get_execution_options(), "connect_args": {"application_name": application},
    })
    spotify = MutableSpotify(SimpleNamespace(spotify_id="alice"), target=[uri(1)])
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: spotify)
    app = create_app("testing")
    with app.app_context():
        user = User(spotify_id="alice")
        db.session.add(user)
        db.session.commit()
        playlists = ([f"p{i // 10}" for i in range(SNAPSHOTS - 1)] if other_store is PlaylistSnapshot
                     else [f"p{i}" for i in range(PLAYLISTS - 1)])
        seed(user.id, PlaylistSnapshot, playlists)
        worker_client, web_client = (sign_in(app, "alice", sample_token) for _ in range(2))
        accepted = worker_client.post("/api/workshop/target/commit",
                                      json={"baseline_track_uris": [uri(1)], "track_uris": [uri(2)]})
        assert accepted.status_code == 202
        receipt_pending, release = Event(), Event()

        def hold(_mapper, _connection, snapshot):
            if snapshot.job_execution_id == accepted.json["operation_id"]:
                receipt_pending.set()
                assert release.wait(10)

        event.listen(PlaylistSnapshot, "after_insert", hold)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                worker = pool.submit(run_operation, worker_client, spotify, accepted.json["operation_id"], accepted)
                try:
                    assert receipt_pending.wait(8)
                    other = pool.submit(save, web_client, other_store, "different")
                    blocked = False
                    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                        deadline = time.monotonic() + 3
                        while not other.done() and time.monotonic() < deadline:
                            blocked = observer.scalar(text(
                                "SELECT EXISTS (SELECT FROM pg_stat_activity WHERE application_name = :name "
                                "AND cardinality(pg_blocking_pids(pid)) > 0)"
                            ), {"name": application})
                            if blocked:
                                break
                            time.sleep(0.01)
                finally:
                    release.set()
                assert worker.result().operation["state"] == "succeeded"
                refused = other.result()
        finally:
            event.remove(PlaylistSnapshot, "after_insert", hold)
        assert refused.status_code == 409, refused.json
        assert blocked
        assert PlaylistSnapshot.query.count() == len(playlists) + 1 and WorkshopSession.query.count() == 0
        assert spotify.writes == [("update", "target", [uri(2)])]
        db.session.remove()
        db.engine.dispose()
