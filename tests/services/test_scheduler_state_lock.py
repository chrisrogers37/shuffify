"""Legacy routine state changes remain available during an execution."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from shuffify.models.db import Schedule, User, db
from shuffify.services.playlist_lock import playlist_lock
from shuffify.services.scheduler_service import ScheduleBusyError, SchedulerService


@pytest.fixture
def threaded_app(tmp_path, monkeypatch):
    from config import TestConfig
    from shuffify import create_app

    monkeypatch.setattr(TestConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{tmp_path / 'state-lock.db'}")
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.engine.dispose()


def test_state_only_update_proceeds_during_execution_but_definition_edit_waits(threaded_app):
    user = User(spotify_id="state-lock-owner")
    db.session.add(user)
    db.session.commit()
    schedule = Schedule(
        user_id=user.id, job_type="shuffle", target_playlist_id="target",
        target_playlist_name="Target", schedule_type="interval", schedule_value="daily",
        algorithm_name="BasicShuffle", is_enabled=True,
    )
    db.session.add(schedule)
    db.session.commit()
    schedule_id, user_id = schedule.id, user.id

    def mutate(**values):
        with threaded_app.app_context():
            return SchedulerService.update_schedule(schedule_id, user_id, **values).is_enabled

    with (
        patch.object(SchedulerService, "_sync_apscheduler_job"),
        patch("shuffify.services.scheduler_service.LOCK_WAIT_S", 0.01),
        playlist_lock("target") as acquired,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        assert acquired
        assert pool.submit(mutate, is_enabled=False).result(timeout=5) is False
        with pytest.raises(ScheduleBusyError):
            pool.submit(mutate, is_enabled=True, schedule_value="weekly").result(timeout=5)

    db.session.expire_all()
    assert schedule.is_enabled is False
    assert schedule.schedule_value == "daily"
