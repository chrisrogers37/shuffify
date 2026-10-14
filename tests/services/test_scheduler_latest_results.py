"""Batched latest execution reads for exact routine management cards."""

from datetime import datetime, timedelta, timezone

from shuffify.models.db import JobExecution, Schedule, User, db
from shuffify.services.scheduler_service import SchedulerService


def test_latest_executions_are_owned_scoped_and_tiebroken_by_id(db_app):
    owner, other = User(spotify_id="results-owner"), User(spotify_id="results-other")
    db.session.add_all([owner, other])
    db.session.flush()

    def routine(user, target):
        row = Schedule(
            user_id=user.id, job_type="shuffle", target_playlist_id=target,
            target_playlist_name=target, schedule_type="interval", schedule_value="daily",
        )
        db.session.add(row)
        db.session.flush()
        return row

    a, no_run, elsewhere, foreign = (
        routine(owner, "target"), routine(owner, "target"),
        routine(owner, "elsewhere"), routine(other, "target"),
    )
    now = datetime.now(timezone.utc)
    old = JobExecution(schedule_id=a.id, started_at=now - timedelta(days=1), status="success")
    tie_one = JobExecution(schedule_id=a.id, started_at=now, status="failed")
    tie_two = JobExecution(schedule_id=a.id, started_at=now, status="success")
    db.session.add_all([
        old, tie_one, tie_two,
        JobExecution(schedule_id=elsewhere.id, started_at=now, status="success"),
        JobExecution(schedule_id=foreign.id, started_at=now, status="success"),
    ])
    db.session.commit()

    scoped = SchedulerService.get_latest_executions(owner.id, "target")
    assert set(scoped) == {a.id, no_run.id}
    assert scoped[a.id]["id"] == tie_two.id
    assert scoped[no_run.id] is None
    assert set(SchedulerService.get_latest_executions(owner.id)) == {a.id, no_run.id, elsewhere.id}
