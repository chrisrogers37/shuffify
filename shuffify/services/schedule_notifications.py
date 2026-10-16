"""Transactional schedule-definition invalidation with a best-effort postcommit poke."""

import logging
from datetime import datetime, timezone

from flask import current_app, has_app_context
from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
CHANNEL = "shuffify_schedules"
_FIELDS = frozenset(
    (
        "user_id",
        "job_type",
        "target_playlist_id",
        "target_playlist_name",
        "source_playlist_ids",
        "algorithm_name",
        "algorithm_params",
        "schedule_type",
        "schedule_value",
        "timezone",
        "is_enabled",
        "created_at",
    )
)
_KEY = "schedule_definition_notification"
_installed = False


def now_utc():
    return datetime.now(timezone.utc)


def publish(storage):
    if storage.get_bind().dialect.name == "postgresql":
        # PostgreSQL delivers only after commit and coalesces duplicate identical notifications.
        storage.connection().execute(text("SELECT pg_notify('shuffify_schedules', 'changed')"))


def poke(app):
    from shuffify.services.worker_control import WorkerControlClient

    WorkerControlClient(app).poke()


def _changed(storage):
    state = storage.info.setdefault(_KEY, {})
    state["pending"] = True
    if has_app_context():
        state["app"] = current_app._get_current_object()


def _before_flush(storage, *_args):
    from shuffify.models.db import Schedule

    changed = False
    for row in storage.new | storage.deleted | storage.dirty:
        if isinstance(row, Schedule) and (
            row in storage.new
            or row in storage.deleted
            or any(inspect(row).attrs[name].history.has_changes() for name in _FIELDS)
        ):
            if row not in storage.deleted:
                row.definition_changed_at = now_utc()
            changed = True
    if changed:
        _changed(storage)


def _after_flush(storage, *_args):
    state = storage.info.get(_KEY)
    if state and state.pop("pending", False):
        publish(storage)


def _bulk(execution):
    # Current definition writers use ORM rows; also cover explicit Session bulk UPDATE/DELETE.
    statement = execution.statement
    if not (execution.is_update or execution.is_delete) or getattr(statement.table, "name", None) != "schedules":
        return
    value_fields = {getattr(key, "key", str(key)) for key in (getattr(statement, "_values", None) or {})}
    values = execution.parameters or []
    if isinstance(values, dict):
        values = [values]
    fields = value_fields | {key for row in values if isinstance(row, dict) for key in row if key != "id"}
    if execution.is_delete or fields & _FIELDS or not fields:
        parameters = None
        if execution.is_update:
            timestamp = now_utc()
            if value_fields & _FIELDS or not execution.parameters:
                execution.statement = statement.values(definition_changed_at=timestamp)
            else:
                def stamp(values):
                    return {**values, "definition_changed_at": timestamp} if values.keys() & _FIELDS else values

                parameters = (
                    [stamp(values) for values in execution.parameters]
                    if isinstance(execution.parameters, list) else stamp(execution.parameters)
                )
        result = execution.invoke_statement(params=parameters)
        _changed(execution.session)
        _after_flush(execution.session)
        return result


def _after_commit(storage):
    if storage.in_nested_transaction():
        return
    state = storage.info.pop(_KEY, None)
    if state and state.get("app") is not None:
        try:
            # Outside the commit transaction. A failed/lost HTTP hint is recovered by LISTEN.
            poke(state["app"])
        except Exception:
            logger.debug("Schedule refresh poke unavailable")


def _after_rollback(storage, transaction):
    if transaction.parent is None:
        storage.info.pop(_KEY, None)


def install_schedule_notifications():
    global _installed
    if _installed:
        return
    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "after_flush_postexec", _after_flush)
    event.listen(Session, "do_orm_execute", _bulk, retval=True)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_soft_rollback", _after_rollback)
    _installed = True
