"""Account storage policy through the authenticated Flask routes and a real local worker."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from shuffify.models.db import ActivityLog, JobExecution, PlaylistSnapshot, User, UserSettings, WorkshopSession, db
from shuffify.services.workshop_commit_service import WorkshopCommitService
from tests.mutable_spotify import MutableSpotify, uri
from tests.queued_operations import post_and_run
from tests.routes.test_routine_policy import sign_in
from tests.services.operations.test_crash_recovery import Crash, expire_lease
from tests.services.operations.test_routine_commands import routine as routine

SNAPSHOTS, SESSIONS, PLAYLISTS = 200, 50, 50


def seed(user_id, model, playlists):
    for playlist in playlists:
        if model is PlaylistSnapshot:
            row = model(user_id=user_id, playlist_id=playlist, playlist_name=playlist, track_count=1)
        else:
            row = model(user_id=user_id, playlist_id=playlist, session_name="Draft")
        row.track_uris = [uri(1)]
        db.session.add(row)
    db.session.commit()


def save(client, model, playlist):
    if model is PlaylistSnapshot:
        return client.post(f"/api/playlist/{playlist}/snapshots", json={"playlist_name": playlist, "track_uris": []})
    return client.post(f"/api/workshop/{playlist}/sessions", json={"session_name": "Draft", "track_uris": []})


def record_url(model, row_id):
    return ("/api/snapshots/" if model is PlaylistSnapshot else "/api/workshop/sessions/") + str(row_id)


@pytest.fixture
def accounts(limiter_app, sample_token, monkeypatch):
    spotify = MutableSpotify(SimpleNamespace(spotify_id="alice"), target=[uri(2)])
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: spotify)
    with limiter_app.app_context():
        alice, bob = (User(spotify_id=name, display_name=name) for name in ("alice", "bob"))
        db.session.add_all([alice, bob])
        db.session.flush()
        db.session.add(UserSettings(user_id=alice.id, max_snapshots_per_playlist=10))
        db.session.commit()
        yield alice.id, sign_in(limiter_app, "alice", sample_token), sign_in(limiter_app, "bob", sample_token), spotify
        db.session.remove()
        db.engine.dispose()


@pytest.mark.parametrize("model,limit", [(PlaylistSnapshot, SNAPSHOTS), (WorkshopSession, SESSIONS)])
def test_record_capacity_is_per_owner_and_deletion_frees_one(accounts, model, limit):
    owner, alice, bob, _ = accounts
    seed(owner, model, [f"p{i // 10}" for i in range(limit - 1)])
    assert save(alice, model, "target").status_code == 200
    ids = {row.id for row in model.query.all()}
    activity = ActivityLog.query.count()
    refusal = save(alice, model, "another")
    assert refusal.status_code == 409 and str(limit) in refusal.json["message"]
    assert {row.id for row in model.query.all()} == ids and ActivityLog.query.count() == activity
    assert save(bob, model, "another").status_code == 200
    assert alice.delete(record_url(model, min(ids))).status_code == 200
    assert save(alice, model, "another").status_code == 200
    assert model.query.filter_by(user_id=owner).count() == limit


@pytest.mark.parametrize("model", [PlaylistSnapshot, WorkshopSession])
def test_playlist_capacity_counts_union_and_last_deletion(accounts, model):
    owner, alice, bob, _ = accounts
    seed(owner, PlaylistSnapshot, [f"p{i}" for i in range(25)])
    seed(owner, WorkshopSession, [f"p{i}" for i in range(25, PLAYLISTS - 1)])
    assert save(alice, PlaylistSnapshot, "shared").status_code == 200
    assert save(alice, WorkshopSession, "shared").status_code == 200
    first, second = (m.query.filter_by(playlist_id="shared").one().id for m in (PlaylistSnapshot, WorkshopSession))
    refused = save(alice, model, "another")
    assert refused.status_code == 409 and "50 playlists" in refused.json["message"]
    assert save(bob, model, "another").status_code == 200
    assert alice.delete(record_url(PlaylistSnapshot, first)).status_code == 200
    assert save(alice, model, "another").status_code == 409
    assert alice.delete(record_url(WorkshopSession, second)).status_code == 200
    assert save(alice, model, "another").status_code == 200


def test_legacy_overages_allow_session_edit_snapshot_rotation_and_restore(accounts):
    owner, alice, _, spotify = accounts
    seed(owner, WorkshopSession, ["draft"] + [f"legacy{i}" for i in range(SESSIONS)])
    seed(owner, PlaylistSnapshot, [f"p{i // 10}" for i in range(SNAPSHOTS)] + ["target"])
    draft = record_url(WorkshopSession, WorkshopSession.query.first().id)
    assert alice.get(draft).status_code == 200
    edited = alice.put(draft, json={"session_name": "Edited", "track_uris": []})
    assert edited.json["session"]["session_name"] == "Edited"
    assert save(alice, WorkshopSession, "draft").status_code == 409
    assert save(alice, PlaylistSnapshot, "target").status_code == 409
    oldest = PlaylistSnapshot.query.filter_by(playlist_id="p0").order_by(PlaylistSnapshot.id).first().id
    assert save(alice, PlaylistSnapshot, "p0").status_code == 200
    assert db.session.get(PlaylistSnapshot, oldest) is None
    saved = PlaylistSnapshot.query.filter_by(playlist_id="target").one().id
    url = record_url(PlaylistSnapshot, saved)
    assert alice.get(url).json["snapshot"]["track_uris"] == [uri(1)]
    result = post_and_run(alice, spotify, url + "/restore")
    assert result.operation["state"] == "succeeded"
    assert spotify.writes == [("update", "target", [uri(1)])]
    assert PlaylistSnapshot.query.count() == SNAPSHOTS + 1
    assert alice.delete(url).status_code == alice.delete(draft).status_code == 200
    # Deleting only the excess row leaves each inventory exactly full.
    assert save(alice, PlaylistSnapshot, "p19new").status_code == save(alice, WorkshopSession, "p0").status_code == 409


def test_workshop_commit_replays_saved_provider_outcome_with_refused_snapshot(accounts):
    owner, alice, _, spotify = accounts
    seed(owner, PlaylistSnapshot, [f"p{i // 10}" for i in range(SNAPSHOTS)])
    url = "/api/workshop/target/commit"
    options = {"json": {"baseline_track_uris": [uri(2)], "track_uris": [uri(3)]},
               "headers": {"Idempotency-Key": "workshop-at-capacity"}}
    applied = post_and_run(alice, spotify, url, **options)
    result = applied.get_json()
    assert applied.operation["state"] == "succeeded" and result["target_status"] == "saved"
    assert result["bookkeeping"]["snapshot"] == "failed"
    assert result["current_track_uris"] == [uri(3)]
    replay = post_and_run(alice, spotify, url, **options)
    assert replay.operation == applied.operation
    assert spotify.writes == [("update", "target", [uri(3)])]
    assert JobExecution.query.count() == 1 and PlaylistSnapshot.query.count() == SNAPSHOTS


def test_failed_rotation_keeps_original_snapshots_and_refuses_insert(accounts):
    owner, alice, _, _ = accounts
    seed(owner, PlaylistSnapshot, ["target"] * 10)
    ids = [row.id for row in PlaylistSnapshot.query.order_by(PlaylistSnapshot.id)]
    db.session.execute(text("CREATE TRIGGER refuse_trim BEFORE DELETE ON playlist_snapshots "
                            "BEGIN SELECT RAISE(ABORT, 'test trim failure'); END"))
    db.session.commit()
    assert save(alice, PlaylistSnapshot, "target").status_code == 500
    assert [row.id for row in PlaylistSnapshot.query.order_by(PlaylistSnapshot.id)] == ids


def test_direct_workshop_save_at_capacity_keeps_verified_order(accounts):
    owner, alice, _, spotify = accounts
    seed(owner, PlaylistSnapshot, [f"p{i // 10}" for i in range(SNAPSHOTS)])
    service = WorkshopCommitService(spotify)
    with alice.session_transaction() as history:
        user = db.session.get(User, owner)
        first = service.commit("target", user, history, [uri(2)], [uri(3)])
        again = service.commit("target", user, history, [uri(2)], [uri(3)])
    assert (first.target_status, again.target_status) == ("saved", "already_applied")
    assert spotify.writes == [("update", "target", [uri(3)])]
    assert PlaylistSnapshot.query.count() == SNAPSHOTS


def test_crash_after_collect_and_refused_drip_snapshots_never_repeats_provider_append(routine, monkeypatch):
    seed(routine.user.id, PlaylistSnapshot, [f"p{i // 10}" for i in range(SNAPSHOTS)])
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    local = routine.repository.local_step
    receipts = []

    def interrupt(lease, name, callback, **options):
        value = local(lease, name, callback, **options)
        if name == "routine.drip.snapshots.v1":
            receipts.extend(value)
            raise Crash()
        return value

    with monkeypatch.context() as patch:
        patch.setattr(routine.repository, "local_step", interrupt)
        with pytest.raises(Crash):
            routine.run(row)
    assert len(routine.world.writes) == 1
    assert receipts and all(item == {"status": "failed", "error_code": "snapshot_failed"} for item in receipts)
    collect = routine.world.writes[0]
    assert collect[:2] == ("POST", "/playlists/collection/items")
    expire_lease(routine.repository, row.id)
    assert routine.run(row).state == "succeeded"
    assert len(routine.world.writes) == 3 and routine.world.writes.count(collect) == 1
    assert PlaylistSnapshot.query.count() == SNAPSHOTS


@pytest.mark.parametrize("initial_rotation", [False, True])
def test_multi_destination_snapshot_refusal_preserves_every_original_row(routine, initial_rotation):
    owner = routine.user.id
    # One free slot for two requested snapshots, or one destination that can rotate at a full cap.
    count = SNAPSHOTS - 1
    playlists = [f"p{i // 10}" for i in range(count)]
    if initial_rotation:
        db.session.add(UserSettings(user_id=owner, max_snapshots_per_playlist=1))
        playlists.append("collection")
    seed(owner, PlaylistSnapshot, playlists)
    before = {row.id for row in PlaylistSnapshot.query.all()}
    row, _ = routine.enqueue("raid", direct=True)
    done = routine.run(row)
    assert done.state == "succeeded"
    receipts = done.journal["runtime"]["local_steps"]["routine.raid.snapshots.v1"]
    assert receipts == [{"status": "failed", "error_code": "snapshot_failed"}] * 2
    assert {row.id for row in PlaylistSnapshot.query.all()} == before
    assert len(routine.world.writes) == 1


def test_rotation_keeps_new_save_when_previous_writer_clock_was_ahead(accounts):
    owner, alice, _, _ = accounts
    seed(owner, PlaylistSnapshot, ["target"] * 10)
    PlaylistSnapshot.query.update({"created_at": datetime.now(timezone.utc) + timedelta(hours=1)})
    db.session.commit()
    response = save(alice, PlaylistSnapshot, "target")
    assert response.status_code == 200
    url = record_url(PlaylistSnapshot, response.json["snapshot"]["id"])
    assert alice.get(url).status_code == 200 and PlaylistSnapshot.query.count() == 10


@pytest.mark.parametrize("rotating", ["target", "collection"])
def test_worker_projects_whole_set_after_lowered_playlist_setting(routine, rotating):
    owner = routine.user.id
    # Even this grandfathered owner can save: 15 old records rotate down to 10 and the other destination adds one.
    seed(owner, PlaylistSnapshot, [rotating] * 15 + [f"p{i // 10}" for i in range(SNAPSHOTS - 10)])
    db.session.add(UserSettings(user_id=owner, max_snapshots_per_playlist=10))
    db.session.commit()
    row, _ = routine.enqueue("raid", direct=True)
    assert routine.run(row).state == "succeeded"
    assert PlaylistSnapshot.query.count() == SNAPSHOTS + 1
    assert PlaylistSnapshot.query.filter_by(playlist_id=rotating).count() == 10
    saved = PlaylistSnapshot.query.filter_by(job_execution_id=row.id).all()
    assert {s.playlist_id for s in saved} == {"target", "collection"}
