"""Definition notifications are transactional; bookkeeping does not refresh jobs."""

import importlib
from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from shuffify.models.db import Schedule, db
from tests.services.operations.test_scheduled_admission import NOW
from tests.services.operations.test_scheduled_admission import schedule as schedule


def test_orm_insert_update_delete_pokes_only_after_outer_commit(schedule, db_app, monkeypatch):
    module = importlib.import_module("shuffify.services.schedule_notifications")
    sent, poked = [], []
    monkeypatch.setattr(module, "publish", lambda storage: sent.append(storage.in_transaction()))
    monkeypatch.setattr(module, "poke", lambda app: poked.append(True))
    schedule.algorithm_params = {"keep_first": 2}
    db.session.flush()
    assert sent == [True] and poked == []
    db.session.commit()
    assert poked == [True]
    schedule.is_enabled = False
    db.session.flush()
    db.session.rollback()
    assert len(sent) == 2 and poked == [True]
    db.session.delete(schedule)
    db.session.commit()
    assert len(sent) == 3 and poked == [True, True]


def test_bookkeeping_and_savepoint_never_poke_before_commit(schedule, db_app, monkeypatch):
    module = importlib.import_module("shuffify.services.schedule_notifications")
    sent, poked = [], []
    monkeypatch.setattr(module, "publish", lambda storage: sent.append(True))
    monkeypatch.setattr(module, "poke", lambda app: poked.append(True))
    schedule.last_status, schedule.last_error = "success", None
    db.session.commit()
    assert sent == poked == []
    with db.session.begin_nested():
        schedule.is_enabled = False
        db.session.flush()
    assert sent == [True] and poked == []
    db.session.commit()
    assert poked == [True]


def test_explicit_session_and_bulk_definition_paths_notify(schedule, db_app, monkeypatch):
    module = importlib.import_module("shuffify.services.schedule_notifications")
    sent = []
    monkeypatch.setattr(module, "publish", lambda storage: sent.append(True))
    with Session(db.engine) as storage, storage.begin():
        storage.execute(update(Schedule).where(Schedule.id == schedule.id).values(is_enabled=False))
    assert sent == [True]
    with Session(db.engine) as storage, storage.begin():
        storage.execute(update(Schedule).where(Schedule.id == schedule.id).values(last_status="failed"))
    assert sent == [True]


def test_definition_floor_stamps_every_changed_row_and_ignores_bookkeeping(schedule, db_app, monkeypatch):
    module = importlib.import_module("shuffify.services.schedule_notifications")
    assert schedule.definition_changed_at is not None
    monkeypatch.setattr(module, "now_utc", lambda: NOW)
    other = Schedule(
        user_id=schedule.user_id, job_type="shuffle", target_playlist_id="second",
        algorithm_name="BasicShuffle", schedule_type="interval", schedule_value="daily",
    )
    db.session.add(other)
    schedule.is_enabled = False
    db.session.commit()
    assert schedule.definition_changed_at == other.definition_changed_at == NOW
    monkeypatch.setattr(module, "now_utc", lambda: NOW + timedelta(minutes=1))
    schedule.last_run_at, schedule.last_status = NOW, "success"
    other.last_error = "Known local error"
    db.session.commit()
    assert schedule.definition_changed_at == other.definition_changed_at == NOW


def test_definition_floor_and_notifications_rollback_together(schedule, db_app, monkeypatch):
    module = importlib.import_module("shuffify.services.schedule_notifications")
    before = schedule.definition_changed_at
    monkeypatch.setattr(module, "now_utc", lambda: NOW)
    schedule.is_enabled = False
    db.session.flush()
    assert schedule.definition_changed_at == NOW
    db.session.rollback()
    assert schedule.is_enabled
    assert schedule.definition_changed_at == before


def test_bulk_definition_floor_is_transactional_and_bookkeeping_leaves_it_unchanged(schedule, db_app, monkeypatch):
    module = importlib.import_module("shuffify.services.schedule_notifications")
    monkeypatch.setattr(module, "now_utc", lambda: NOW)
    with Session(db.engine) as storage, storage.begin():
        storage.execute(update(Schedule).where(Schedule.id == schedule.id).values(is_enabled=False))
        assert storage.get(Schedule, schedule.id).definition_changed_at == NOW
    with Session(db.engine) as storage, storage.begin():
        storage.execute(update(Schedule).where(Schedule.id == schedule.id).values(last_status="success"))
        assert storage.get(Schedule, schedule.id).definition_changed_at == NOW
    with Session(db.engine) as storage:
        storage.execute(update(Schedule).where(Schedule.id == schedule.id).values(schedule_value="weekly"))
        storage.rollback()
    db.session.expire_all()
    assert schedule.schedule_value == "daily"
    assert schedule.definition_changed_at == NOW


@pytest.mark.parametrize("explicit_bookkeeping", [False, True])
def test_parameterized_bulk_definition_floor_does_not_stamp_bookkeeping_rows(
    schedule, db_app, monkeypatch, explicit_bookkeeping
):
    module = importlib.import_module("shuffify.services.schedule_notifications")
    monkeypatch.setattr(module, "now_utc", lambda: NOW)
    other = Schedule(
        user_id=schedule.user_id, job_type="shuffle", target_playlist_id="second",
        algorithm_name="BasicShuffle", schedule_type="interval", schedule_value="daily",
    )
    db.session.add(other)
    db.session.commit()
    before = schedule.definition_changed_at
    monkeypatch.setattr(module, "now_utc", lambda: NOW + timedelta(minutes=1))
    with Session(db.engine) as storage, storage.begin():
        statement = update(Schedule)
        if explicit_bookkeeping:
            statement = statement.values(last_error="Known local error")
        storage.execute(statement, [
            {"id": schedule.id, "last_status": "success"}, {"id": other.id, "is_enabled": False},
        ])
    db.session.expire_all()
    assert schedule.definition_changed_at == before
    assert other.definition_changed_at == NOW + timedelta(minutes=1)


def test_bulk_bookkeeping_values_do_not_hide_parameterized_definition_changes(schedule, db_app, monkeypatch):
    module = importlib.import_module("shuffify.services.schedule_notifications")
    sent = []
    monkeypatch.setattr(module, "publish", lambda storage: sent.append(True))
    monkeypatch.setattr(module, "now_utc", lambda: NOW)
    with Session(db.engine) as storage, storage.begin():
        storage.execute(update(Schedule).values(last_status="success"), [{"id": schedule.id, "is_enabled": False}])
    db.session.expire_all()
    assert not schedule.is_enabled
    assert schedule.definition_changed_at == NOW
    assert sent == [True]
