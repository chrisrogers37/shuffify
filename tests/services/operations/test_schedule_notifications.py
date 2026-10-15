"""Definition notifications are transactional; bookkeeping does not refresh jobs."""

import importlib

from sqlalchemy import update
from sqlalchemy.orm import Session

from shuffify.models.db import Schedule, db
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
