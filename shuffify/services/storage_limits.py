"""Per-owner storage limits, checked inside the inventory writer's transaction."""

from sqlalchemy import select

from shuffify.models.db import PlaylistSnapshot, WorkshopSession
from shuffify.services.base import lock_user_inventory

MAX_USER_SNAPSHOTS = 200
MAX_USER_SESSIONS = 50
MAX_STORED_PLAYLISTS = 50


class StorageLimitReached(Exception):
    """A save would grow an inventory that is already full."""


def lock_storage_inventory(storage, user_id):
    """Start SQLite's writer transaction or hold the PostgreSQL owner lock through commit."""
    if storage.get_bind().dialect.name == "sqlite":
        connection = storage.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        lock_user_inventory(user_id, session=storage)


def _would_exceed(storage, rows, growth, limit):
    """Only a net increase can violate a grandfathered inventory."""
    return growth > 0 and (growth > limit or storage.scalar(rows.offset(limit - growth).limit(1)) is not None)


def check_storage_growth(storage, model, user_id, playlist_ids, *, row_growth):
    """Check the whole save, including any rows released by same-playlist rotations."""
    limit, label = (
        (MAX_USER_SNAPSHOTS, "snapshots") if model is PlaylistSnapshot else (MAX_USER_SESSIONS, "saved sessions")
    )
    rows = select(model.id).where(model.user_id == user_id)
    if _would_exceed(storage, rows, row_growth, limit):
        raise StorageLimitReached(f"You can store up to {limit} {label}. Delete an existing one to save another.")

    snapshots = select(PlaylistSnapshot.playlist_id).where(PlaylistSnapshot.user_id == user_id)
    sessions = select(WorkshopSession.playlist_id).where(WorkshopSession.user_id == user_id)
    requested = set(playlist_ids)
    represented = snapshots.where(PlaylistSnapshot.playlist_id.in_(requested)).union(
        sessions.where(WorkshopSession.playlist_id.in_(requested))
    )
    new_ids = requested.difference(storage.scalars(represented))
    if _would_exceed(storage, snapshots.union(sessions), len(new_ids), MAX_STORED_PLAYLISTS):
        raise StorageLimitReached(
            f"You can store snapshots and sessions for up to {MAX_STORED_PLAYLISTS} playlists. "
            "Delete all snapshots and sessions for a playlist to save a new one."
        )
