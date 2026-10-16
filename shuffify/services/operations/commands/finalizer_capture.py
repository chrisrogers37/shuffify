"""Explicit-session capture and revalidation of exact Workshop source cleanup.

All reads use Core mappings and suppress autoflush. This module cannot construct
Spotify/auth clients, save ORM changes, resolve provenance, or expand an inbox.
The caller parses public HTTP requests and owns the transaction boundary.

Before the first provider preparation, freeze the newly recaptured command in
Runtime.prepare. Construct every subsequent helper config from that frozen
value: a promoted status observed after an acknowledged DB receipt must never
change an already prepared helper config during recovery.
"""

from functools import wraps

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from shuffify.models.db import PendingRaidTrack, PlaylistPair, RaidPlaylistLink, User
from shuffify.services.operations.control import propagate_control

from .finalizer_schemas import ArchiveAppendCommand, FinalizeRaidsCommand, FinalizeRestoreCommand


class FinalizerCaptureRejected(ValueError):
    """Authored rejection suitable for the existing finalizer response mapper."""

    def __init__(self, code, message, http_status=409, retryable=False):
        self.code, self.message = code, message
        self.http_status, self.retryable = http_status, retryable
        super().__init__(message)


class FinalizerCaptureNotFound(FinalizerCaptureRejected):
    def __init__(self):
        super().__init__("user_not_found", "The authenticated user is unavailable.", 404)


def _database_read(function):
    @wraps(function)
    def captured(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SQLAlchemyError as error:
            propagate_control(error)
            raise FinalizerCaptureRejected(
                "database_read_failed",
                "Could not read the captured source changes.",
                502,
                True,
            ) from None

    return captured


def _rows(storage, model, *conditions):
    return storage.execute(select(model.__table__).where(*conditions).order_by(model.id)).mappings().all()


def _owner(storage, user_id):
    if (
        type(user_id) is not int
        or user_id < 1
        or storage.execute(select(User.id).where(User.id == user_id)).scalar_one_or_none() is None
    ):
        raise FinalizerCaptureNotFound()


def _source_changed():
    return FinalizerCaptureRejected("source_changed", "The linked source changed. Review the original source.")


def _invalid_selection():
    return FinalizerCaptureRejected(
        "invalid_track_selection",
        "Some selected tracks are unavailable for this playlist.",
    )


def _binding(storage, user_id, playlist_id, source_playlist_id, *, raid):
    model, target, source = (
        (RaidPlaylistLink, RaidPlaylistLink.target_playlist_id, "raid_playlist_id")
        if raid
        else (PlaylistPair, PlaylistPair.production_playlist_id, "archive_playlist_id")
    )
    rows = _rows(storage, model, model.user_id == user_id, target == playlist_id)
    binding = {"id": rows[0]["id"], "playlist_id": rows[0][source]} if rows else None
    linked = binding["playlist_id"] if binding is not None else None
    if source_playlist_id != linked or not raid and linked is None:
        raise _source_changed()
    if playlist_id == source_playlist_id:
        raise FinalizerCaptureRejected("invalid_source", "The source must be different from the saved playlist.")
    return binding


def _pending(storage, user_id, playlist_id, track_ids):
    if (
        not isinstance(track_ids, list)
        or not track_ids
        or any(type(value) is not int or value < 1 for value in track_ids)
    ):
        raise _invalid_selection()
    selected = sorted(set(track_ids))
    rows = _rows(
        storage,
        PendingRaidTrack,
        PendingRaidTrack.id.in_(selected),
        PendingRaidTrack.user_id == user_id,
        PendingRaidTrack.target_playlist_id == playlist_id,
        PendingRaidTrack.status.in_(["pending", "promoted"]),
    )
    if [row["id"] for row in rows] != selected:
        raise _invalid_selection()
    return [{key: row[key] for key in ("id", "track_uri", "status")} for row in rows]


def _command(schema, values):
    try:
        return schema.model_validate(values)
    except ValidationError:
        raise FinalizerCaptureRejected(
            "invalid_request",
            "Review the source changes again; the captured task is incomplete or invalid.",
            400,
        ) from None


@_database_read
def capture_raids(storage, user_id, playlist_id, *, source_playlist_id, target_track_uris, track_ids):
    """Freeze the exact selected owned pending/promoted IDs; repeated IDs select one row."""
    with storage.no_autoflush:
        _owner(storage, user_id)
        binding = _binding(storage, user_id, playlist_id, source_playlist_id, raid=True)
        rows = _pending(storage, user_id, playlist_id, track_ids)
    return _command(
        FinalizeRaidsCommand,
        {
            "playlist_id": playlist_id,
            "source_playlist_id": source_playlist_id,
            "source_binding": binding,
            "target_track_uris": target_track_uris,
            "pending_rows": rows,
        },
    )


def _archive(storage, user_id, playlist_id, schema, *, source_playlist_id, target_track_uris, track_uris, **extra):
    with storage.no_autoflush:
        _owner(storage, user_id)
        binding = _binding(storage, user_id, playlist_id, source_playlist_id, raid=False)
    return _command(
        schema,
        {
            "playlist_id": playlist_id,
            "source_playlist_id": source_playlist_id,
            "source_binding": binding,
            "target_track_uris": target_track_uris,
            "track_uris": track_uris,
            **extra,
        },
    )


@_database_read
def capture_append(
    storage, user_id, playlist_id, *, source_playlist_id, target_track_uris, track_uris, baseline_track_uris
):
    """Retain the exact baseline plus ordered occurrence payload; perform no target read."""
    return _archive(
        storage,
        user_id,
        playlist_id,
        ArchiveAppendCommand,
        source_playlist_id=source_playlist_id,
        target_track_uris=target_track_uris,
        track_uris=track_uris,
        baseline_track_uris=baseline_track_uris,
    )


@_database_read
def capture_restore(storage, user_id, playlist_id, *, source_playlist_id, target_track_uris, track_uris):
    """Capture cleanup of an already saved target, distinct from an actual unarchive move."""
    return _archive(
        storage,
        user_id,
        playlist_id,
        FinalizeRestoreCommand,
        source_playlist_id=source_playlist_id,
        target_track_uris=target_track_uris,
        track_uris=track_uris,
    )


def assert_revalidated(captured, current):
    """Permit pending→promoted retries; every URI, owned binding and intent stays exact."""
    if type(current) is not type(captured):
        raise _source_changed()
    if current.source_binding != captured.source_binding or current.reserved_targets != captured.reserved_targets:
        raise _source_changed()
    if isinstance(captured, FinalizeRaidsCommand):
        previous = {row.id: row for row in captured.pending_rows}
        observed = {row.id: row for row in current.pending_rows}
        if previous.keys() != observed.keys() or any(
            before.track_uri != observed[row_id].track_uri
            or before.status == "promoted"
            and observed[row_id].status != "promoted"
            for row_id, before in previous.items()
        ):
            raise _invalid_selection()
        same = captured.model_dump(exclude={"pending_rows"}) == current.model_dump(exclude={"pending_rows"})
    else:
        same = captured == current
    if not same:
        raise _source_changed()
    return current


@_database_read
def recapture(storage, user_id, captured):
    """Fresh owned reads before first preparation; never follow a replacement source."""
    values = {"source_playlist_id": captured.source_playlist_id, "target_track_uris": captured.target_track_uris}
    if isinstance(captured, FinalizeRaidsCommand):
        current = capture_raids(storage, user_id, captured.playlist_id, track_ids=captured.track_ids, **values)
    elif isinstance(captured, ArchiveAppendCommand):
        current = capture_append(
            storage,
            user_id,
            captured.playlist_id,
            track_uris=captured.track_uris,
            baseline_track_uris=captured.baseline_track_uris,
            **values,
        )
    elif isinstance(captured, FinalizeRestoreCommand):
        current = capture_restore(storage, user_id, captured.playlist_id, track_uris=captured.track_uris, **values)
    else:
        raise ValueError("Unsupported finalizer command")
    return assert_revalidated(captured, current)
