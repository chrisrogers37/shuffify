"""
Service for managing playlist pairs (production + archive).
"""

import logging

from shuffify.models.db import PlaylistPair, db
from shuffify.services.base import commit_or_stage
from shuffify.services.playlist_lock import locked_commit_target

logger = logging.getLogger(__name__)


class PlaylistPairError(Exception):
    """Base error for playlist pair operations."""
    pass


class PlaylistPairNotFoundError(PlaylistPairError):
    """Raised when a playlist pair is not found."""
    pass


class PlaylistPairExistsError(PlaylistPairError):
    """Raised when a pair already exists for the playlist."""
    pass


class PlaylistPairService:
    """Manages playlist pair CRUD and archive/unarchive operations.

    Mutators take a keyword-only ``commit``: ``commit=False`` stages the change
    (it flushes, lets errors propagate and leaves the transaction to the caller),
    for the one-transaction playlist setup Save. The default commits immediately.
    """

    @staticmethod
    @locked_commit_target("production_playlist_id")
    def create_pair(
        user_id,
        production_playlist_id,
        archive_playlist_id,
        production_playlist_name=None,
        archive_playlist_name=None,
        auto_archive_on_remove=True,
        *,
        commit=True,
    ):
        """Create a new playlist pair.

        Raises PlaylistPairExistsError if pair already exists.
        """
        existing = PlaylistPair.query.filter_by(
            user_id=user_id,
            production_playlist_id=production_playlist_id,
        ).first()
        if existing:
            raise PlaylistPairExistsError(
                "A pair already exists for this playlist"
            )

        pair = PlaylistPair(
            user_id=user_id,
            production_playlist_id=production_playlist_id,
            archive_playlist_id=archive_playlist_id,
            production_playlist_name=production_playlist_name,
            archive_playlist_name=archive_playlist_name,
            auto_archive_on_remove=auto_archive_on_remove,
        )
        db.session.add(pair)
        commit_or_stage(
            commit,
            f"create playlist pair "
            f"{production_playlist_id} -> "
            f"{archive_playlist_id} for user {user_id}",
            PlaylistPairError,
        )
        return pair

    @staticmethod
    def get_pair_for_playlist(user_id, production_playlist_id):
        """Get the pair for a production playlist, or None."""
        return PlaylistPair.query.filter_by(
            user_id=user_id,
            production_playlist_id=production_playlist_id,
        ).first()

    @staticmethod
    def get_pairs_for_user(user_id):
        """Get all pairs for a user."""
        return PlaylistPair.query.filter_by(
            user_id=user_id,
        ).order_by(PlaylistPair.created_at.desc()).all()

    @staticmethod
    @locked_commit_target("production_playlist_id")
    def update_pair(user_id, production_playlist_id, *, commit=True, **kwargs):
        """Update a playlist pair's settings.

        Raises PlaylistPairNotFoundError if not found.
        """
        pair = PlaylistPair.query.filter_by(
            user_id=user_id,
            production_playlist_id=production_playlist_id,
        ).first()
        if not pair:
            raise PlaylistPairNotFoundError(
                "No pair found for this playlist"
            )

        for key, value in kwargs.items():
            if hasattr(pair, key) and value is not None:
                setattr(pair, key, value)

        commit_or_stage(
            commit,
            f"update playlist pair for "
            f"{production_playlist_id} (user {user_id})",
            PlaylistPairError,
        )
        return pair

    @staticmethod
    @locked_commit_target("production_playlist_id")
    def delete_pair(user_id, production_playlist_id, *, commit=True):
        """Delete a playlist pair.

        Raises PlaylistPairNotFoundError if not found.
        """
        pair = PlaylistPair.query.filter_by(
            user_id=user_id,
            production_playlist_id=production_playlist_id,
        ).first()
        if not pair:
            raise PlaylistPairNotFoundError(
                "No pair found for this playlist"
            )
        db.session.delete(pair)
        commit_or_stage(
            commit,
            f"delete playlist pair for "
            f"{production_playlist_id} (user {user_id})",
            PlaylistPairError,
        )

    @staticmethod
    def archive_tracks(api, archive_playlist_id, track_uris):
        """Add tracks to the archive playlist on Spotify.

        Returns the number of tracks added.
        """
        if not track_uris:
            return 0

        api.playlist_add_items(archive_playlist_id, track_uris)

        logger.info(
            "Archived %d tracks to %s",
            len(track_uris),
            archive_playlist_id,
        )
        return len(track_uris)

    @staticmethod
    def unarchive_tracks(
        api,
        production_playlist_id,
        archive_playlist_id,
        track_uris,
    ):
        """Move tracks from archive back to production.

        Adds to production, removes from archive.
        Returns the number of tracks moved.
        """
        if not track_uris:
            return 0

        api.playlist_add_items(
            production_playlist_id, track_uris
        )
        api.playlist_remove_items(
            archive_playlist_id, track_uris
        )

        logger.info(
            "Unarchived %d tracks from %s to %s",
            len(track_uris),
            archive_playlist_id,
            production_playlist_id,
        )
        return len(track_uris)
