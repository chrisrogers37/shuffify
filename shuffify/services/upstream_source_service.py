"""
Upstream source service for managing persistent source configurations.

Handles CRUD operations for UpstreamSource records, which link a source
playlist to a target playlist for a specific user.
"""

import logging
from typing import List, Optional

from shuffify.models.db import db, UpstreamSource, User

logger = logging.getLogger(__name__)


class UpstreamSourceError(Exception):
    """Base exception for upstream source operations."""

    pass


class UpstreamSourceNotFoundError(UpstreamSourceError):
    """Raised when an upstream source cannot be found."""

    pass


class UpstreamSourceService:
    """Service for managing UpstreamSource records."""

    @staticmethod
    def add_source(
        spotify_id: str,
        target_playlist_id: str,
        source_playlist_id: str,
        source_type: str = "external",
        source_url: Optional[str] = None,
        source_name: Optional[str] = None,
    ) -> UpstreamSource:
        """
        Add an upstream source configuration.

        Args:
            spotify_id: The Spotify user ID.
            target_playlist_id: The playlist being built/modified.
            source_playlist_id: The playlist to pull tracks from.
            source_type: Either 'own' or 'external'.
            source_url: The original URL used to find this source.
            source_name: Display name of the source playlist.

        Returns:
            The created UpstreamSource instance.

        Raises:
            UpstreamSourceError: If user not found or creation fails.
        """
        if source_type not in ("own", "external"):
            raise UpstreamSourceError(
                f"Invalid source_type: {source_type}. "
                f"Must be 'own' or 'external'."
            )

        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise UpstreamSourceError(
                f"User not found for spotify_id: {spotify_id}"
            )

        # Check for duplicate: same user, target, and source
        existing = UpstreamSource.query.filter_by(
            user_id=user.id,
            target_playlist_id=target_playlist_id,
            source_playlist_id=source_playlist_id,
        ).first()

        if existing:
            logger.info(
                f"Upstream source already exists: "
                f"{source_playlist_id} -> "
                f"{target_playlist_id} for user {spotify_id}"
            )
            return existing

        try:
            source = UpstreamSource(
                user_id=user.id,
                target_playlist_id=target_playlist_id,
                source_playlist_id=source_playlist_id,
                source_url=source_url,
                source_type=source_type,
                source_name=source_name,
            )
            db.session.add(source)
            db.session.commit()

            logger.info(
                f"Added upstream source: "
                f"{source_playlist_id} -> "
                f"{target_playlist_id} for user {spotify_id} "
                f"(type={source_type})"
            )
            return source

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to add upstream source: {e}",
                exc_info=True,
            )
            raise UpstreamSourceError(
                f"Failed to add source: {e}"
            )

    @staticmethod
    def list_sources(
        spotify_id: str, target_playlist_id: str
    ) -> List[UpstreamSource]:
        """
        List all upstream sources for a user's target playlist.

        Args:
            spotify_id: The Spotify user ID.
            target_playlist_id: The target playlist ID.

        Returns:
            List of UpstreamSource instances.
        """
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            return []

        return (
            UpstreamSource.query.filter_by(
                user_id=user.id,
                target_playlist_id=target_playlist_id,
            )
            .order_by(UpstreamSource.created_at.desc())
            .all()
        )

    @staticmethod
    def get_source(
        source_id: int, spotify_id: str
    ) -> UpstreamSource:
        """
        Get a specific upstream source by ID.

        Args:
            source_id: The upstream source database ID.
            spotify_id: The Spotify user ID (for ownership check).

        Returns:
            UpstreamSource instance.

        Raises:
            UpstreamSourceNotFoundError: If not found or not owned.
        """
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise UpstreamSourceNotFoundError("User not found")

        source = db.session.get(UpstreamSource, source_id)
        if not source or source.user_id != user.id:
            raise UpstreamSourceNotFoundError(
                f"Upstream source {source_id} not found"
            )
        return source

    @staticmethod
    def delete_source(
        source_id: int, spotify_id: str
    ) -> bool:
        """
        Delete an upstream source configuration.

        Args:
            source_id: The upstream source database ID.
            spotify_id: The Spotify user ID (for ownership check).

        Returns:
            True if deleted successfully.

        Raises:
            UpstreamSourceNotFoundError: If not found or not owned.
            UpstreamSourceError: If deletion fails.
        """
        source = UpstreamSourceService.get_source(
            source_id, spotify_id
        )

        try:
            db.session.delete(source)
            db.session.commit()
            logger.info(
                f"Deleted upstream source {source_id}"
            )
            return True

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to delete upstream source "
                f"{source_id}: {e}",
                exc_info=True,
            )
            raise UpstreamSourceError(
                f"Failed to delete source: {e}"
            )

    @staticmethod
    def list_all_sources_for_user(
        spotify_id: str,
    ) -> List[UpstreamSource]:
        """
        List ALL upstream sources for a user, across all targets.

        Args:
            spotify_id: The Spotify user ID.

        Returns:
            List of UpstreamSource instances.
        """
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            return []

        return (
            UpstreamSource.query.filter_by(user_id=user.id)
            .order_by(UpstreamSource.created_at.desc())
            .all()
        )
