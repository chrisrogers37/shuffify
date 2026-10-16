"""Both dismissal admissions freeze only owned rows before any provider work."""

import pytest
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, JobExecution, PendingRaidTrack, RaidPlaylistLink, User, db
from shuffify.services.operations.commands.dismissal import pending_dismiss
from shuffify.worker import OperationWorker
from tests.services.operations.test_dismiss_commands import inbox as inbox
from tests.services.operations.test_routine_commands import routine as routine


@pytest.mark.parametrize("suffix", ["dismiss", "dismiss-all"])
def test_dismiss_admission_is_local_and_same_key_replays_before_pending_or_binding_changes(
    auth_client, monkeypatch, suffix
):
    user = User.query.filter_by(spotify_id="user123").one()
    rows = [
        PendingRaidTrack(
            user_id=user.id,
            target_playlist_id="target",
            track_uri=f"spotify:track:{index:022d}",
            track_name="Track",
            status="pending",
        )
        for index in (1, 2)
    ]
    db.session.add_all(
        [*rows, RaidPlaylistLink(user_id=user.id, target_playlist_id="target", raid_playlist_id="source")]
    )
    db.session.commit()
    ids = [row.id for row in rows]

    def forbidden(*args, **kwargs):
        pytest.fail("Dismiss admission cannot authenticate or contact Spotify")

    monkeypatch.setattr("shuffify.routes.require_auth", forbidden)
    monkeypatch.setattr("shuffify.spotify.api.SpotifyAPI.__init__", forbidden)
    body = {"track_ids": [ids[1], ids[0], ids[0], 9999]} if suffix == "dismiss" else None
    path = f"/api/playlist/target/pending-raids/{suffix}"
    accepted = auth_client.post(path, json=body, headers={"Idempotency-Key": "one-dismiss"})
    assert accepted.status_code == 202
    assert accepted.headers["Location"] == f"/api/operations/{accepted.json['operation_id']}"
    operation = db.session.get(JobExecution, accepted.json["operation_id"])
    assert operation.targets == ["source", "target"]
    assert [row["id"] for row in operation.payload["command"]["pending_rows"]] == ids
    assert PendingRaidTrack.query.filter_by(status="pending").count() == 2
    assert ActivityLog.query.count() == 0
    assert "payload" not in accepted.json["operation"] and "journal" not in accepted.json["operation"]
    with Session(db.engine) as storage:
        storage.query(PendingRaidTrack).update({"status": "dismissed"})
        storage.query(RaidPlaylistLink).update({"raid_playlist_id": "replacement"})
        storage.commit()
    replay = auth_client.post(path, json=body, headers={"Idempotency-Key": "one-dismiss"})
    assert replay.status_code == 202 and replay.json["operation_id"] == accepted.json["operation_id"]
    assert JobExecution.query.count() == 1


@pytest.mark.parametrize("suffix", ["dismiss", "dismiss-all"])
def test_accepted_dismissal_runs_real_observed_worker_then_status_get_reads_without_auth(
    inbox, sample_token, monkeypatch, suffix
):
    with inbox.app.test_client() as client:
        with client.session_transaction() as session:
            session["spotify_token"] = sample_token
            session["user_data"] = {"id": inbox.user.spotify_id}
            session["_db_user_id"] = inbox.user.id
        before = list(inbox.world.writes)
        response = client.post(
            f"/api/playlist/target/pending-raids/{suffix}",
            json={"track_ids": inbox.pending_ids} if suffix == "dismiss" else None,
            headers={"Idempotency-Key": "one-dismiss"},
        )
        assert response.status_code == 202
        assert inbox.world.writes == before and inbox.factories == []
        worker = OperationWorker(
            inbox.app,
            repository=inbox.repository,
            handlers={"pending_dismiss": pending_dismiss},
            api_factory=inbox.factory,
            heartbeat_interval=0,
        )
        assert worker.run_once(response.json["operation_id"])
        monkeypatch.setattr("shuffify.routes.require_auth", lambda: pytest.fail("Status GET is DB-only"))
        completed = client.get(response.headers["Location"])
        assert completed.status_code == 200 and completed.json["operation"]["state"] == "succeeded"
        assert completed.json["operation"]["result"]["dismissed_count"] == 2
        assert len(inbox.world.writes) == 1
        assert ActivityLog.query.count() == 1
