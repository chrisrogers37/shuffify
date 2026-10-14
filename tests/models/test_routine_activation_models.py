"""Persistence invariants for routine activation acceptance."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from shuffify.models.db import JobExecution, RoutineActivationReceipt, Schedule, User, db


def test_receipt_survives_schedule_deletion_and_key_is_unique(db_app):
    with db_app.app_context():
        user = User(spotify_id="routine-owner")
        db.session.add(user)
        db.session.flush()
        schedule = Schedule(
            user_id=user.id, job_type="shuffle", target_playlist_id="target",
            schedule_type="interval", schedule_value="daily", timezone="UTC",
        )
        db.session.add(schedule)
        db.session.flush()
        receipt = RoutineActivationReceipt(
            user_id=user.id, request_key="request-1", payload_hash="a" * 64,
            accepted_at=datetime.now(timezone.utc), schedule_id=schedule.id,
            schedule_created_at=schedule.created_at,
        )
        db.session.add(receipt)
        db.session.commit()
        accepted_id = schedule.id
        accepted_created_at = schedule.created_at
        db.session.delete(schedule)
        db.session.commit()
        saved = RoutineActivationReceipt.query.filter_by(user_id=user.id, request_key="request-1").one()
        assert (saved.schedule_id, saved.schedule_created_at) == (accepted_id, accepted_created_at)
        db.session.add(RoutineActivationReceipt(
            user_id=user.id, request_key="request-1", payload_hash="b" * 64,
            schedule_id=accepted_id, schedule_created_at=accepted_created_at,
        ))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_legacy_execution_detail_is_unavailable(db_app):
    with db_app.app_context():
        execution = JobExecution(status="success", result_json=None)
        db.session.add(execution)
        db.session.commit()
        assert execution.to_dict()["result_detail"] == {
            "detail_status": "unavailable", "version": None, "steps": None
        }
