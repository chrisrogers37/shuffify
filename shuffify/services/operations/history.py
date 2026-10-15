"""Per-session Undo receipts committed with the operation that confirmed them.

Writer requests capture only the relevant legacy entries. Workers supply the
mapping returned by ``session_for`` to StateService, then checkpoint ``stage``'s
document before finishing the same operation row. Reads ignore staged receipts
until that row has a matching confirmed terminal state. Polling never writes
history back into the browser session.

Call ``assert_revision`` before an Undo's first effect while holding the worker's
complete target lock set. ``stage`` repeats the check, but target reservations
and locks remain necessary to serialize confirmation with another writer.
Retention must exclude ``protected_operation_ids`` from deletion; the most recent
receipt (including a clear) is the durable history record, not a UI acknowledgement.
"""

import re
import secrets
from contextlib import nullcontext
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, db
from shuffify.services.state_service import PLAYLIST_STATES_KEY, StaleHistoryError, StateError

HISTORY_SCOPE_KEY = "history_scope"
_SCOPE = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
_CONFIRMED_STATES = frozenset({"succeeded", "rolled_back"})


class HistoryError(StateError):
    """A history receipt or attempted transition cannot be safely confirmed."""


def get_history_scope(session):
    """Create one opaque server-side session scope without copying authentication."""
    scope = session.get(HISTORY_SCOPE_KEY)
    if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
        scope = secrets.token_urlsafe(32)
        session[HISTORY_SCOPE_KEY] = scope
    return scope


def _identity(user_id, scope):
    if type(user_id) is not int or user_id <= 0:
        raise HistoryError("History requires an authenticated user.")
    if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
        raise HistoryError("The saved history scope is unavailable.")


def _targets(playlist_ids):
    if isinstance(playlist_ids, (str, bytes)):
        raise HistoryError("History requires a collection of playlist IDs.")
    values = tuple(playlist_ids)
    if any(not isinstance(value, str) or not value or len(value) > 255 for value in values):
        raise HistoryError("The saved playlist identity is unavailable.")
    return tuple(sorted(set(values)))


def _entry(value):
    """Copy precisely StateService's format, preserving order and duplicate URIs."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HistoryError("The saved playlist history is unavailable.")
    states, index = value.get("states"), value.get("current_index")
    if (not isinstance(states, list) or not states or type(index) is not int or not 0 <= index < len(states)
            or any(not isinstance(uris, list) or any(not isinstance(uri, str) for uri in uris) for uris in states)):
        raise HistoryError("The saved playlist history is unavailable.")
    return {"states": deepcopy(states), "current_index": index}


def _assert_confirmed_writes(journal, state):
    expected_phase = "compensating" if state == "rolled_back" else "forward"
    if ("status" in journal and journal["status"] != state
            or "phase" in journal and journal["phase"] != expected_phase):
        raise HistoryError("An unresolved playlist write cannot confirm history.")
    plan_key = "compensation" if state == "rolled_back" else "plans"
    plans = journal.get(plan_key, [])
    if not isinstance(plans, list):
        raise HistoryError("The operation's write confirmation is unavailable.")
    if plans:
        if journal.get("phase") != expected_phase or journal.get("status") != state:
            raise HistoryError("The operation's playlist writes are not yet confirmed.")
    for plan in plans:
        if not isinstance(plan, dict) or not isinstance(plan.get("batches"), list):
            raise HistoryError("The operation's write confirmation is unavailable.")
        if any(not isinstance(batch, dict) or batch.get("state") != "acknowledged" for batch in plan["batches"]):
            raise HistoryError("An unconfirmed playlist write cannot advance history.")


class HistorySession(dict):
    """A credentials-free StateService mapping with frozen identity and before-state."""

    def __init__(self, user_id, scope, entries, revisions):
        super().__init__({PLAYLIST_STATES_KEY: {key: deepcopy(value) for key, value in entries.items()
                                              if value is not None}})
        self.user_id = user_id
        self.scope = scope
        self.modified = False
        self._before = deepcopy(entries)
        self._revisions = dict(revisions)

    @property
    def before(self):
        return deepcopy(self._before)

    @property
    def revisions(self):
        return dict(self._revisions)

    def restore(self, working):
        """Restore checkpointed worker progress while retaining the original rollback baseline."""
        if not isinstance(working, dict) or set(working) != {PLAYLIST_STATES_KEY}:
            raise HistoryError("The command's working history is unavailable.")
        entries = working[PLAYLIST_STATES_KEY]
        if not isinstance(entries, dict) or set(entries) - set(self._before):
            raise HistoryError("Working history includes an unexpected playlist.")
        self[PLAYLIST_STATES_KEY] = {key: _entry(value) for key, value in entries.items() if value is not None}
        self.modified = self[PLAYLIST_STATES_KEY] != {key: value for key, value in self._before.items()
                                                     if value is not None}


class HistoryStore:
    """Read confirmed receipts using short independent database sessions."""

    def __init__(self, engine=None, *, session=None):
        self.engine = engine if engine is not None else db.engine
        self.session = session

    @staticmethod
    def _receipt(row):
        if row.state not in _CONFIRMED_STATES or row.completed_at is None:
            return None
        receipt = row.history_receipt
        if (not isinstance(receipt, dict) or type(receipt.get("version")) is not int or receipt["version"] != 1
                or receipt.get("confirmed") is not True or receipt.get("state") != row.state
                or type(receipt.get("user_id")) is not int or receipt["user_id"] != row.user_id
                or not isinstance(receipt.get("scope"), str) or not _SCOPE.fullmatch(receipt["scope"])
                or not isinstance(receipt.get("entries"), dict)):
            return None
        return receipt

    @staticmethod
    def _rows(session, *, user_id=None, scope=None):
        receipt = JobExecution.journal["history"]
        query = select(
            JobExecution.id, JobExecution.user_id, JobExecution.state, JobExecution.completed_at,
            receipt.label("history_receipt"),
        ).where(
            JobExecution.kind.is_not(None), JobExecution.state.in_(_CONFIRMED_STATES),
            JobExecution.completed_at.is_not(None), JobExecution.user_id.is_not(None),
        ).order_by(JobExecution.completed_at.desc(), JobExecution.id.desc())
        if user_id is not None:
            query = query.where(JobExecution.user_id == user_id)
        if scope is not None:
            query = query.where(receipt["scope"].as_string() == scope)
        return session.execute(query)

    def _load_many(self, user_id, scope, playlist_ids, legacy=None):
        """Resolve all requested targets from one scoped receipt projection."""
        _identity(user_id, scope)
        targets = _targets(playlist_ids)
        remaining = set(targets)
        entries, revisions = {}, {}
        legacy = legacy if isinstance(legacy, dict) else {}
        if remaining:
            with (nullcontext(self.session) if self.session is not None else Session(self.engine)) as session:
                for row in self._rows(session, user_id=user_id, scope=scope):
                    receipt = self._receipt(row)
                    if receipt is None or receipt["scope"] != scope:
                        continue
                    for playlist_id in remaining.intersection(receipt["entries"]):
                        change = receipt["entries"][playlist_id]
                        if not isinstance(change, dict) or "entry" not in change:
                            raise HistoryError("The confirmed playlist history is unavailable.")
                        entries[playlist_id], revisions[playlist_id] = _entry(change["entry"]), row.id
                    remaining.difference_update(entries)
                    if not remaining:
                        break
        for playlist_id in remaining:
            entries[playlist_id], revisions[playlist_id] = _entry(legacy.get(playlist_id)), None
        return entries, revisions

    def load(self, user_id, scope, playlist_id, legacy=None):
        """Return the latest confirmed entry and receipt ID, with a one-time legacy fallback.

        An explicit null entry is a durable clear: its nonnull revision prevents
        an older Flask-session entry from being imported again.
        """
        entries, revisions = self._load_many(user_id, scope, [playlist_id], {playlist_id: legacy})
        return entries[playlist_id], revisions[playlist_id]

    def capture(self, user_id, scope, playlist_ids, legacy=None):
        """Freeze revisions plus relevant legacy imports for an accepted command payload."""
        entries, revisions = self._load_many(user_id, scope, playlist_ids, legacy)
        imports = {key: value for key, value in entries.items() if revisions[key] is None and value is not None}
        return {"scope": scope, "imports": imports, "revisions": revisions}

    def assert_revision(self, user_id, scope, playlist_id, expected_revision):
        """Reject a stale Undo selection before dispatch, even when its URIs happen to match."""
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision <= 0):
            raise HistoryError("The selected history revision is unavailable.")
        _saved, current_revision = self.load(user_id, scope, playlist_id)
        if current_revision != expected_revision:
            raise StaleHistoryError("The selected saved version changed. Review the current history.")

    def session_for(self, user_id, scope, playlist_ids, legacy=None, expected_revisions=None):
        """Construct a detached mapping compatible with all existing StateService methods."""
        entries, revisions = self._load_many(user_id, scope, playlist_ids, legacy)
        for playlist_id in entries:
            if expected_revisions is not None:
                if playlist_id not in expected_revisions:
                    raise HistoryError("The command's saved history revision is unavailable.")
                expected = expected_revisions[playlist_id]
                if expected is not None and (type(expected) is not int or expected <= 0):
                    raise HistoryError("The selected history revision is unavailable.")
                if expected != revisions[playlist_id]:
                    raise StaleHistoryError("The selected saved version changed. Review the current history.")
        return HistorySession(user_id, scope, entries, revisions)

    def stage(self, journal, session, *, state, confirmed):
        """Stage a receipt for repository.finish; never independently commit history.

        A rolled-back command records its frozen before-state, including a null
        tombstone when there was no history. The advanced worker mapping is never
        retained after compensation restored the original playlist.
        """
        if confirmed is not True or state not in _CONFIRMED_STATES:
            raise HistoryError("Only a verified successful or rolled-back operation can confirm history.")
        if not isinstance(session, HistorySession) or not isinstance(journal, dict):
            raise HistoryError("History must be staged from its detached worker mapping.")
        _assert_confirmed_writes(journal, state)
        _identity(session.user_id, session.scope)
        current = session.get(PLAYLIST_STATES_KEY)
        if not isinstance(current, dict) or set(current) - set(session._before):
            raise HistoryError("History includes a playlist outside the command's target set.")
        changes = {}
        for playlist_id, before in session._before.items():
            self.assert_revision(session.user_id, session.scope, playlist_id, session._revisions[playlist_id])
            after = _entry(current.get(playlist_id))
            # Record an actual change, a first legacy import, or the deliberate
            # before-state of a command whose later step required compensation.
            if after != before or (before is not None and session._revisions[playlist_id] is None):
                changes[playlist_id] = {
                    "entry": deepcopy(before) if state == "rolled_back" else after,
                    "parent_revision": session._revisions[playlist_id],
                }
        result = deepcopy(journal)
        if changes:
            result["history"] = {"version": 1, "user_id": session.user_id, "scope": session.scope,
                                 "state": state, "confirmed": True, "entries": changes}
        else:
            result.pop("history", None)
        return result

    def protected_operation_ids(self):
        """Protect latest receipts, active work, and receipts referenced by that work."""
        protected, seen = set(), set()
        with Session(self.engine) as session:
            for row in self._rows(session):
                receipt = self._receipt(row)
                if receipt is None:
                    continue
                for playlist_id in receipt["entries"]:
                    key = (row.user_id, receipt["scope"], playlist_id)
                    if key not in seen:
                        protected.add(row.id)
                        seen.add(key)
            active = session.scalars(select(JobExecution).where(
                JobExecution.kind.is_not(None), JobExecution.state.in_({"queued", "running", "compensating"}),
            ))
            for row in active:
                protected.add(row.id)
                binding = (row.payload or {}).get("history")
                references = binding.get("revisions", {}) if isinstance(binding, dict) else {}
                if isinstance(references, dict):
                    protected.update(value for value in references.values() if type(value) is int and value > 0)
                staged = (row.journal or {}).get("history")
                changes = staged.get("entries", {}) if isinstance(staged, dict) else {}
                if isinstance(changes, dict):
                    protected.update(change["parent_revision"] for change in changes.values()
                                     if isinstance(change, dict) and type(change.get("parent_revision")) is int
                                     and change["parent_revision"] > 0)
        return protected
