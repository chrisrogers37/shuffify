"""
Service for managing playlist pairs (production + archive).
"""

import logging

from shuffify.models.db import db, PlaylistPair
from shuffify.services.base import safe_commit

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
    """Manages playlist pair CRUD and archive/unarchive operations."""

    @staticmethod
    def create_pair(
        user_id,
        production_playlist_id,
        archive_playlist_id,
        production_playlist_name=None,
        archive_playlist_name=None,
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
        )
        db.session.add(pair)
        safe_commit(
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
    def delete_pair(user_id, production_playlist_id):
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
        safe_commit(
            f"delete playlist pair for "
            f"{production_playlist_id} (user {user_id})",
            PlaylistPairError,
        )

    @staticmethod
    def archive_tracks(sp, archive_playlist_id, track_uris):
        """Add tracks to the archive playlist on Spotify.

        Batches in groups of 100.
        Returns the number of tracks added.
        """
        if not track_uris:
            return 0

        added = 0
        for i in range(0, len(track_uris), 100):
            batch = track_uris[i:i + 100]
            sp.playlist_add_items(archive_playlist_id, batch)
            added += len(batch)

        logger.info(
            "Archived %d tracks to %s",
            added,
            archive_playlist_id,
        )
        return added

    @staticmethod
    def unarchive_tracks(
        sp,
        production_playlist_id,
        archive_playlist_id,
        track_uris,
    ):
        """Move tracks from archive back to production.

        Adds to production, removes from archive.
        Batches in groups of 100.
        Returns the number of tracks moved.
        """
        if not track_uris:
            return 0

        # Add to production
        for i in range(0, len(track_uris), 100):
            batch = track_uris[i:i + 100]
            sp.playlist_add_items(
                production_playlist_id, batch
            )

        # Remove from archive
        for i in range(0, len(track_uris), 100):
            batch = track_uris[i:i + 100]
            sp.playlist_remove_all_occurrences_of_items(
                archive_playlist_id, batch
            )

        logger.info(
            "Unarchived %d tracks from %s to %s",
            len(track_uris),
            archive_playlist_id,
            production_playlist_id,
        )
        return len(track_uris)

    @staticmethod
    def create_archive_playlist(sp, user_id, name):
        """Create a new private Spotify playlist for archiving.

        Returns (playlist_id, playlist_name).
        """
        archive_name = f"{name} [Archive]"
        result = sp.user_playlist_create(
            user_id,
            archive_name,
            public=False,
            description="Archive playlist for removed tracks",
        )
        logger.info(
            "Created archive playlist '%s' (%s)",
            archive_name,
            result["id"],
        )
        return result["id"], archive_name
