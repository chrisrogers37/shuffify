"""Short, fenced database transactions for durable operation admission and execution."""

import json
import logging
import math
import re
import secrets
import threading
import time
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, db
from shuffify.services.execution_result_service import serialize_execution_detail
from shuffify.services.playlist_lock import (
    _held_targets,
    _local_guard,
    _local_locks,
    _mark_owned,
    _owner,
    _playlist_lock_key,
)

logger = logging.getLogger(__name__)
ACTIVE_STATES = frozenset({"queued", "running", "compensating"})
LEASED_STATES = frozenset({"running", "compensating"})
TERMINAL_STATES = frozenset({"succeeded", "failed", "rolled_back", "needs_attention"})
_STATUS = {"succeeded": "success", "failed": "failed", "rolled_back": "failed_rolled_back", "needs_attention": "failed"}
_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_PRIVATE_FIELDS = frozenset({
    "journal", "payload", "lease_owner", "lease_expires_at", "heartbeat_at", "authorization", "client_secret",
    "access_token", "refresh_token", "spotify_token", "csrf_token", "token_info", "encrypted_refresh_token",
})


class BusyOperation(RuntimeError):
    """An accepted operation already reserves at least one requested target."""

    def __init__(self, operation_id):
        self.operation_id = operation_id
        super().__init__("A playlist operation is already active.")


class RequestKeyConflict(RuntimeError):
    """An idempotency or scheduled-fire key belongs to a different command."""


class LeaseLost(RuntimeError):
    """The caller no longer owns an unexpired execution lease."""


@dataclass(frozen=True)
class Lease:
    operation_id: int
    owner: str
    targets: tuple[str, ...]
    recovering: bool


def _json_copy(value):
    """Require JSON object keys and finite JSON values, then detach mutable input."""
    def check(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings.")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise ValueError("Operation data must contain JSON values.")

    check(value)
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _targets(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("Targets must be a sequence of resource IDs.")
    values = tuple(values)
    if any(not isinstance(value, str) or not value or len(value) > 255 for value in values):
        raise ValueError("Each target must be a nonempty resource ID of at most 255 characters.")
    return tuple(sorted(set(values)))


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _positive_seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Lease duration must be a positive finite number.")
    return value


def _public_value(value):
    if isinstance(value, dict):
        return {key: _public_value(child) for key, child in value.items()
                if key.lower().replace("-", "_") not in _PRIVATE_FIELDS}
    if isinstance(value, list):
        return [_public_value(child) for child in value]
    return deepcopy(value)


def public_operation(row):
    """Expose result and bounded progress, never command payloads or private journals."""
    journal = row.journal if isinstance(row.journal, dict) else {}
    source_progress = journal.get("progress")
    progress = {}
    if isinstance(source_progress, dict):
        for key in ("batches_done", "total_batches"):
            value = source_progress.get(key)
            if type(value) is int and value >= 0:
                progress[key] = value
        if source_progress.get("phase") in {"forward", "compensating"}:
            progress["phase"] = source_progress["phase"]
    source_recovery = journal.get("recovery")
    recovery = {"available": False, "action": None, "reason": None}
    if isinstance(source_recovery, dict):
        recovery["available"] = source_recovery.get("available") is True
        if recovery["available"] and source_recovery.get("action") == "restore":
            recovery["action"] = "restore"
        reason = source_recovery.get("reason")
        if isinstance(reason, str) and _CODE.fullmatch(reason):
            recovery["reason"] = reason
    return {
        "id": row.id, "kind": row.kind, "state": row.state, "targets": list(row.targets or []),
        "started_at": row.started_at.isoformat() if row.state != "queued" and row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "progress": progress, "result": _public_value(journal.get("result")), "recovery": recovery,
    }


class OperationRepository:
    """Keep admission, lease ownership and journal persistence outside Flask's scoped session."""

    def __init__(self, engine=None, *, lease_seconds=60):
        self.engine = engine if engine is not None else db.engine
        self.lease_seconds = _positive_seconds(lease_seconds)
        self._postgres = self.engine.dialect.name == "postgresql"

    @contextmanager
    def _transaction(self, *, write=False):
        with Session(self.engine, expire_on_commit=False) as session, session.begin():
            if write and self.engine.dialect.name == "sqlite":
                # SQLite has no row/advisory transaction locks. Serialize admissions
                # and claims before their first read, including across local processes.
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield session

    def _now(self, session):
        return _utc(session.scalar(select(func.clock_timestamp()))) if self._postgres else datetime.now(timezone.utc)

    @staticmethod
    def _detach(session, row):
        if row is None:
            return None
        session.flush()
        for field in ("payload", "targets", "journal", "result_json"):
            setattr(row, field, deepcopy(getattr(row, field)))
        session.expunge(row)
        return row

    def _admission_locks(self, session, *, targets, user_id, request_key, schedule_id, scheduled_for):
        if not self._postgres:
            return
        keys = [("target", target) for target in targets]
        if user_id is not None and request_key is not None:
            keys.append(("request", user_id, request_key))
        if schedule_id is not None and scheduled_for is not None:
            keys.append(("fire", schedule_id, scheduled_for.isoformat()))
        encoded = sorted(json.dumps(key, separators=(",", ":")) for key in keys)
        for key in encoded:
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                            {"key": _playlist_lock_key("queue-admission:" + key)})

    @staticmethod
    def _same_command(row, *, kind, user_id, targets, payload, schedule_id, scheduled_for):
        return (row.kind == kind and row.user_id == user_id and tuple(row.targets or []) == targets
                and json.dumps(row.payload, sort_keys=True, separators=(",", ":"))
                == json.dumps(payload, sort_keys=True, separators=(",", ":"))
                and row.schedule_id == schedule_id and row.scheduled_for == scheduled_for)

    def enqueue(self, *, kind, user_id, targets, payload, request_key=None, schedule_id=None, scheduled_for=None):
        """Reserve all targets and insert one command, or replay the identical keyed command."""
        if not isinstance(kind, str) or not kind or len(kind) > 80:
            raise ValueError("Operation kind must contain 1 to 80 characters.")
        if user_id is not None and (type(user_id) is not int or user_id <= 0):
            raise ValueError("User ID must be a positive integer.")
        if request_key is not None and (
            not isinstance(request_key, str) or not request_key or len(request_key) > 128
        ):
            raise ValueError("Request key must contain 1 to 128 characters.")
        if not isinstance(payload, dict):
            raise ValueError("An operation payload must be an object.")
        targets, payload, scheduled_for = _targets(targets), _json_copy(payload), _utc(scheduled_for)
        with self._transaction(write=True) as session:
            self._admission_locks(session, targets=targets, user_id=user_id, request_key=request_key,
                                  schedule_id=schedule_id, scheduled_for=scheduled_for)
            lookup = []
            if user_id is not None and request_key is not None:
                lookup.append(and_(JobExecution.user_id == user_id, JobExecution.request_key == request_key))
            if schedule_id is not None and scheduled_for is not None:
                lookup.append(and_(JobExecution.schedule_id == schedule_id,
                                   JobExecution.scheduled_for == scheduled_for))
            existing = session.scalars(select(JobExecution).where(or_(*lookup))).all() if lookup else []
            for row in existing:
                if not self._same_command(row, kind=kind, user_id=user_id, targets=targets, payload=payload,
                                          schedule_id=schedule_id, scheduled_for=scheduled_for):
                    raise RequestKeyConflict("This request key belongs to different changes.")
            if existing:
                if len({row.id for row in existing}) != 1:
                    raise RequestKeyConflict("The request and scheduled fire identify different operations.")
                return self._detach(session, existing[0])
            if targets:
                active = session.scalars(select(JobExecution).where(
                    JobExecution.kind.is_not(None), JobExecution.state.in_(ACTIVE_STATES)
                ).order_by(JobExecution.id)).all()
                for row in active:
                    if set(targets).intersection(row.targets or []):
                        raise BusyOperation(row.id)
            now = self._now(session)
            row = JobExecution(kind=kind, user_id=user_id, targets=list(targets), payload=payload,
                               request_key=request_key, schedule_id=schedule_id, scheduled_for=scheduled_for,
                               state="queued", status="queued", started_at=now, journal={})
            session.add(row)
            session.flush()
            if self._postgres:
                session.execute(text("SELECT pg_notify('shuffify_operations', :id)"), {"id": str(row.id)})
            return self._detach(session, row)

    def get(self, operation_id):
        """Return a private detached storage row to a worker."""
        with self._transaction() as session:
            return self._detach(session, session.get(JobExecution, operation_id))

    def get_owned(self, user_id, operation_id):
        """Return a sanitized operation DTO only to its persisted owner."""
        if user_id is None:
            return None
        with self._transaction() as session:
            row = session.scalar(select(JobExecution).where(
                JobExecution.id == operation_id, JobExecution.user_id == user_id, JobExecution.kind.is_not(None),
                JobExecution.state.is_not(None),
            ))
            return public_operation(row) if row is not None else None

    def next_ready_at(self):
        """Return the earliest known active deadline so an idle worker needs no database polling."""
        with self._transaction() as session:
            return _utc(session.scalar(select(func.min(JobExecution.lease_expires_at)).where(
                JobExecution.kind.is_not(None), JobExecution.state.in_(ACTIVE_STATES),
                JobExecution.lease_expires_at.is_not(None),
            )))

    def claim(self, *, operation_id=None, lease_seconds=None):
        """Atomically claim queued work or recover an expired execution with a fresh fence token."""
        duration = self.lease_seconds if lease_seconds is None else _positive_seconds(lease_seconds)
        with self._transaction(write=True) as session:
            now = self._now(session)
            ready = or_(
                and_(JobExecution.state == "queued", or_(JobExecution.lease_expires_at.is_(None),
                                                         JobExecution.lease_expires_at <= now)),
                and_(JobExecution.state.in_(LEASED_STATES), JobExecution.lease_expires_at <= now),
            )
            statement = select(JobExecution).where(JobExecution.kind.is_not(None), ready).order_by(
                JobExecution.started_at, JobExecution.id
            ).limit(1).with_for_update(skip_locked=True)
            if operation_id is not None:
                statement = statement.where(JobExecution.id == operation_id)
            row = session.scalar(statement)
            if row is None:
                return None
            now = self._now(session)
            recovering = row.state in LEASED_STATES
            owner = secrets.token_urlsafe(32)
            if not recovering:
                row.state, row.status, row.started_at = "running", "running", now
            row.lease_owner, row.heartbeat_at = owner, now
            row.lease_expires_at = now + timedelta(seconds=duration)
            return Lease(row.id, owner, _targets(row.targets or []), recovering)

    def _leased_row(self, session, lease):
        row = session.scalar(select(JobExecution).where(
            JobExecution.id == lease.operation_id, JobExecution.lease_owner == lease.owner,
            JobExecution.state.in_(LEASED_STATES), JobExecution.kind.is_not(None),
        ).with_for_update())
        now = self._now(session)
        if row is None or row.lease_expires_at is None or row.lease_expires_at <= now:
            raise LeaseLost("This operation's execution lease is no longer owned.")
        return row, now

    def renew(self, lease, *, lease_seconds=None):
        """Renew only the live fence token without changing journal or result state."""
        duration = self.lease_seconds if lease_seconds is None else _positive_seconds(lease_seconds)
        with self._transaction(write=True) as session:
            row, now = self._leased_row(session, lease)
            row.heartbeat_at, row.lease_expires_at = now, now + timedelta(seconds=duration)
            return self._detach(session, row)

    def checkpoint(self, lease, journal, *, state=None):
        """Replace the complete journal while holding the checked token's row lock."""
        if not isinstance(journal, dict):
            raise ValueError("An operation journal must be an object.")
        journal = _json_copy(journal)
        if state is not None and state not in LEASED_STATES:
            raise ValueError("Checkpoint state must be running or compensating.")
        with self._transaction(write=True) as session:
            row, _now = self._leased_row(session, lease)
            if state == "running" and row.state == "compensating":
                raise ValueError("A compensating operation cannot resume its forward execution.")
            if state is not None:
                row.state = state
            row.journal = journal
            return self._detach(session, row)

    def finish(self, lease, state, *, result=None, error_message=None, tracks_added=None, tracks_total=None):
        """Complete a live claim, preserve rich result and maintain execution-history compatibility."""
        if state not in TERMINAL_STATES:
            raise ValueError("An operation must finish in a terminal state.")
        if result is not None and not isinstance(result, dict):
            raise ValueError("An operation result must be an object.")
        result = _json_copy(result) if result is not None else None
        with self._transaction(write=True) as session:
            row, now = self._leased_row(session, lease)
            journal = deepcopy(row.journal or {})
            if result is not None:
                journal["result"] = result
                detail = result.get("result_detail")
                projection = serialize_execution_detail(detail)
                if projection["detail_status"] == "available":
                    row.result_json = {key: value for key, value in projection.items() if key != "detail_status"}
            row.journal, row.state, row.status, row.completed_at = journal, state, _STATUS[state], now
            row.lease_owner = row.lease_expires_at = None
            row.error_message = error_message
            if tracks_added is not None:
                row.tracks_added = tracks_added
            if tracks_total is not None:
                row.tracks_total = tracks_total
            return self._detach(session, row)

    def defer(self, lease, *, delay_seconds=1):
        """Release a claim until a known deadline without replaying an operation already in recovery."""
        delay = _positive_seconds(delay_seconds)
        with self._transaction(write=True) as session:
            row, now = self._leased_row(session, lease)
            if not lease.recovering and row.state == "running" and not row.journal:
                row.state, row.status = "queued", "queued"
            row.lease_owner = None
            row.lease_expires_at = now + timedelta(seconds=delay)
            return self._detach(session, row)

    @contextmanager
    def target_locks(self, targets, *, timeout_s=2):
        """Hold sorted execution locks across commits; nested domain-service locks share ownership."""
        targets = _targets(targets)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s):
            raise ValueError("Lock timeout must be finite.")
        deadline = time.monotonic() + max(0.0, timeout_s)
        owner = _owner()
        required = [target for target in targets if (owner, "execution", target) not in _held_targets.get()]
        with ExitStack() as stack:
            acquired = stack.enter_context(self._postgres_locks(required, deadline) if self._postgres
                                           else self._sqlite_locks(required, deadline))
            if acquired:
                for target in required:
                    stack.enter_context(_mark_owned((owner, "execution", target), True))
            yield acquired

    @contextmanager
    def _postgres_locks(self, targets, deadline):
        connection, acquired, complete = self.engine.connect(), [], True
        try:
            for target in targets:
                remaining = deadline - time.monotonic()
                try:
                    with connection.begin():
                        if remaining <= 0:
                            locked = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"),
                                                       {"key": _playlist_lock_key(target)})
                            if not locked:
                                complete = False
                                break
                        else:
                            connection.execute(text("SELECT set_config('lock_timeout', :timeout, true)"),
                                               {"timeout": f"{max(1, int(remaining * 1000))}ms"})
                            connection.execute(text("SELECT pg_advisory_lock(:key)"),
                                               {"key": _playlist_lock_key(target)})
                        acquired.append(target)
                except Exception as error:
                    if getattr(getattr(error, "orig", None), "pgcode", None) != "55P03":
                        raise
                    complete = False
                    break
            yield complete
        finally:
            try:
                with connection.begin():
                    for target in reversed(acquired):
                        connection.execute(text("SELECT pg_advisory_unlock(:key)"),
                                           {"key": _playlist_lock_key(target)})
            except Exception:
                logger.warning("Could not release operation target locks; discarding connection", exc_info=True)
                connection.invalidate()
            finally:
                connection.close()

    @contextmanager
    def _sqlite_locks(self, targets, deadline):
        entries, acquired, complete = [], [], True
        try:
            for target in targets:
                # Share process-local execution locks with legacy service helpers.
                # The worker's ownership markers make nested calls reentrant.
                key = target
                with _local_guard:
                    lock, users = _local_locks.get(key, (threading.Lock(), 0))
                    _local_locks[key] = (lock, users + 1)
                entries.append(key)
                if not lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                    complete = False
                    break
                acquired.append(lock)
            yield complete
        finally:
            for lock in reversed(acquired):
                lock.release()
            with _local_guard:
                for key in entries:
                    lock, users = _local_locks[key]
                    if users == 1:
                        del _local_locks[key]
                    else:
                        _local_locks[key] = (lock, users - 1)
