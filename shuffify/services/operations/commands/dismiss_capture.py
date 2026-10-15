"""Explicit-session exact inbox capture and required local dismissal receipt.

Admission filters selected IDs to the owner's current pending intersection.
Application validates every captured identity and binding before any update.
The caller owns the transaction, operation acceptance and optional source cleanup.
"""

from datetime import datetime, timezone
from functools import wraps

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from shuffify.models.db import PendingRaidTrack, RaidPlaylistLink, User
from shuffify.services.operations.control import propagate_control

from .dismiss_schemas import DismissObservation, PendingDismissCommand


class DismissRejected(ValueError):
    def __init__(self, code, message, http_status=409, retryable=False):
        self.code, self.message, self.http_status, self.retryable = code, message, http_status, retryable
        super().__init__(message)


def _read_errors(function):
    @wraps(function)
    def read(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SQLAlchemyError as error:
            propagate_control(error)
            raise DismissRejected(
                "database_read_failed", "Could not read the selected inbox tracks.", 502, True
            ) from None

    return read


def _invalid():
    return DismissRejected("invalid_configuration", "Choose a valid captured inbox selection.", 422)


def _selection_changed():
    return DismissRejected("selection_changed", "The selected inbox tracks changed. Review the captured selection.")


def _owner(storage, user_id):
    if (
        type(user_id) is not int
        or user_id < 1
        or storage.execute(select(User.id).where(User.id == user_id)).scalar_one_or_none() is None
    ):
        raise DismissRejected("user_not_found", "The authenticated user is unavailable.", 404)


def _source(storage, user_id, target, *, lock=False):
    statement = (
        select(RaidPlaylistLink.__table__)
        .where(
            RaidPlaylistLink.user_id == user_id,
            RaidPlaylistLink.target_playlist_id == target,
        )
        .order_by(RaidPlaylistLink.id)
    )
    if lock:
        statement = statement.with_for_update()
    rows = storage.execute(statement).mappings().all()
    if not rows:
        return None
    if len(rows) != 1:
        raise DismissRejected("source_changed", "The linked collection changed. Review the captured source.")
    return {"id": rows[0]["id"], "playlist_id": rows[0]["raid_playlist_id"]}


def _rows(storage, user_id, target, ids=None, *, pending=False, lock=False):
    statement = (
        select(PendingRaidTrack.__table__)
        .where(
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id == target,
        )
        .order_by(PendingRaidTrack.id)
    )
    if ids is not None:
        statement = statement.where(PendingRaidTrack.id.in_(ids))
    if pending:
        statement = statement.where(PendingRaidTrack.status == "pending")
    if lock:
        statement = statement.with_for_update()
    return [
        dict(id=row["id"], track_uri=row["track_uri"], status=row["status"])
        for row in storage.execute(statement).mappings()
    ]


def _command(value):
    if type(value) is not PendingDismissCommand:
        raise _invalid()
    try:
        return PendingDismissCommand.model_validate(value.model_dump())
    except ValidationError:
        raise _invalid() from None


def _capture(storage, user_id, target, selection, ids):
    try:
        skeleton = PendingDismissCommand(playlist_id=target, selection=selection, source_binding=None, pending_rows=[])
    except ValidationError:
        raise _invalid() from None
    with storage.no_autoflush:
        _owner(storage, user_id)
        source = _source(storage, user_id, skeleton.playlist_id)
        rows = _rows(storage, user_id, skeleton.playlist_id, ids, pending=True)
    try:
        return PendingDismissCommand(playlist_id=target, selection=selection, source_binding=source, pending_rows=rows)
    except ValidationError:
        raise _invalid() from None


@_read_errors
def capture_dismiss(storage, user_id, playlist_id, *, track_ids):
    """Capture only owned pending intersection, ignoring foreign/nonpending IDs."""
    if not isinstance(track_ids, (list, tuple)) or any(type(value) is not int or value < 1 for value in track_ids):
        raise _invalid()
    return _capture(storage, user_id, playlist_id, "selected", sorted(set(track_ids)))


@_read_errors
def capture_dismiss_all(storage, user_id, playlist_id):
    """Freeze all currently owned pending rows once; empty is an explicit no-op."""
    return _capture(storage, user_id, playlist_id, "all", None)


def assert_revalidated(captured, current, *, allow_dismissed=False):
    captured = _command(captured)
    if type(allow_dismissed) is not bool or type(current) is not DismissObservation:
        raise _invalid()
    if (
        captured.playlist_id != current.playlist_id
        or captured.selection != current.selection
        or captured.source_binding != current.source_binding
        or captured.reserved_targets != current.reserved_targets
    ):
        raise DismissRejected("source_changed", "The linked collection changed. Review the captured source.")
    if captured.track_ids != current.track_ids or any(
        before.track_uri != after.track_uri
        or after.status not in ({"pending", "dismissed"} if allow_dismissed else {"pending"})
        for before, after in zip(captured.pending_rows, current.pending_rows, strict=True)
    ):
        raise _selection_changed()
    return current


def _observe(storage, user_id, captured, *, lock=False, allow_dismissed=False):
    captured = _command(captured)
    with storage.no_autoflush:
        _owner(storage, user_id)
        source = _source(storage, user_id, captured.playlist_id, lock=lock)
        rows = _rows(storage, user_id, captured.playlist_id, captured.track_ids, lock=lock)
    expected_source = captured.source_binding.model_dump() if captured.source_binding is not None else None
    if source != expected_source:
        raise DismissRejected("source_changed", "The linked collection changed. Review the captured source.")
    try:
        current = DismissObservation(
            playlist_id=captured.playlist_id, selection=captured.selection, source_binding=source, pending_rows=rows
        )
    except ValidationError:
        raise _selection_changed() from None
    return assert_revalidated(captured, current, allow_dismissed=allow_dismissed)


@_read_errors
def recapture(storage, user_id, captured, *, allow_dismissed=False):
    """Read only the frozen row IDs and binding; never expand or mutate the command."""
    return _observe(storage, user_id, captured, allow_dismissed=allow_dismissed)


def dismiss_captured(storage, user_id, captured, *, now=None):
    """Lock, validate and dismiss exact captured identities; flush without commit.

    Storage errors propagate so the caller cannot publish an unconfirmed required
    receipt. A repeated own receipt confirms already-dismissed rows with zero new
    affected count and retains their original resolved_at timestamps.
    """
    when = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
        raise _invalid()
    current = _observe(storage, user_id, captured, lock=True, allow_dismissed=True)
    changed = []
    with storage.no_autoflush:
        for row in current.pending_rows:
            if row.status == "dismissed":
                continue
            result = storage.execute(
                update(PendingRaidTrack.__table__)
                .where(
                    PendingRaidTrack.id == row.id,
                    PendingRaidTrack.user_id == user_id,
                    PendingRaidTrack.target_playlist_id == current.playlist_id,
                    PendingRaidTrack.track_uri == row.track_uri,
                    PendingRaidTrack.status == "pending",
                )
                .values(status="dismissed", resolved_at=when)
            )
            if result.rowcount != 1:
                raise _selection_changed()
            changed.append(row.id)
        storage.flush()
    return {
        "confirmed_ids": current.track_ids,
        "changed_ids": changed,
        "affected_count": len(changed),
        "track_uris": [row.track_uri for row in current.pending_rows],
    }
