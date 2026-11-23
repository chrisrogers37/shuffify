"""Exercise interactive and scheduled admission through the worker's normal drain."""

from datetime import datetime, timedelta, timezone

import pytest

from shuffify.models.db import JobExecution, Schedule, User, db
from shuffify.services.operations.commands.registry import COMMAND_HANDLERS
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.scheduled_admission import admit_scheduled, build_cadence
from shuffify.worker import OperationWorker
from tests.queued_operations import worker_api
from tests.services.test_live_metadata_service import MetadataSpotify
from tests.services.test_workshop_commit_service import PlaylistAPI


@pytest.mark.parametrize("scheduled_bob", [False, True], ids=["interactive", "scheduled-fire"])
def test_worker_gives_bob_a_turn_before_alices_backlog_drains(
    signed_in_limiter_client, limiter_app, sample_token, scheduled_bob,
):
    def submit(client, target):
        response = client.post(f"/api/playlist/{target}/toggle-visibility", json={"public": True})
        assert response.status_code == 202, response.json
        return response.json["operation_id"]

    alice = signed_in_limiter_client("alice")
    bob = signed_in_limiter_client("bob")
    alice_ids = [submit(alice, f"alice{i}") for i in range(1, 6)]
    if scheduled_bob:
        with limiter_app.app_context():
            due = datetime.now(timezone.utc)
            user = User.query.filter_by(spotify_id="bob").one()
            schedule = Schedule(user_id=user.id, job_type="shuffle", target_playlist_id="bob1",
                                algorithm_name="BasicShuffle", schedule_type="interval", schedule_value="daily",
                                timezone="UTC", created_at=due - timedelta(days=2))
            db.session.add(schedule)
            db.session.commit()
            fire = admit_scheduled(OperationRepository(), schedule.id, build_cadence(schedule).fingerprint, due)
            assert fire.state == "admitted"
            bob_id = fire.operation_id
    else:
        bob_id = submit(bob, "bob1")

    def fake_api(user):
        provider = PlaylistAPI([]) if scheduled_bob and user.spotify_id == "bob" else MetadataSpotify()
        provider.owner = user.spotify_id
        return worker_api(provider, sample_token)

    runner = OperationWorker(limiter_app, handlers=COMMAND_HANDLERS, api_factory=fake_api, heartbeat_interval=0)
    assert runner.drain() == 6
    with limiter_app.app_context():
        # A single draining worker stamps started_at on each claim and finishes it before the next.
        rows = JobExecution.query.order_by(JobExecution.started_at, JobExecution.id).all()
        assert [row.id for row in rows] == [alice_ids[0], bob_id, *alice_ids[1:]]
        assert [row.state for row in rows] == ["succeeded"] * 6
