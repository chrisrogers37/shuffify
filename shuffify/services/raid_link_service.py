"""
Service for managing raid playlist links (target + raid playlist).

Mirrors PlaylistPairService for the upstream raid direction.
"""

import logging

from shuffify.models.db import db, RaidPlaylistLink
from shuffify.services.base import (
    safe_commit,
    create_private_playlist,
)

logger = logging.getLogger(__name__)


class RaidLinkError(Exception):
    """Base error for raid link operations."""
    pass


class RaidLinkNotFoundError(RaidLinkError):
    """Raised when a raid link is not found."""
    pass


class RaidLinkExistsError(RaidLinkError):
    """Raised when a link already exists for the playlist."""
    pass


class RaidLinkService:
    """Manages raid playlist link CRUD operations."""

    @staticmethod
    def create_link(
        user_id,
        target_playlist_id,
        raid_playlist_id,
        target_playlist_name=None,
        raid_playlist_name=None,
        drip_count=3,
        drip_enabled=False,
    ):
        """Create a new raid playlist link.

        Raises RaidLinkExistsError if link already exists.
        """
        existing = RaidPlaylistLink.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_playlist_id,
        ).first()
        if existing:
            raise RaidLinkExistsError(
                "A raid link already exists for this playlist"
            )

        link = RaidPlaylistLink(
            user_id=user_id,
            target_playlist_id=target_playlist_id,
            raid_playlist_id=raid_playlist_id,
            target_playlist_name=target_playlist_name,
            raid_playlist_name=raid_playlist_name,
            drip_count=drip_count,
            drip_enabled=drip_enabled,
        )
        db.session.add(link)
        safe_commit(
            f"create raid link "
            f"{target_playlist_id} <- "
            f"{raid_playlist_id} for user {user_id}",
            RaidLinkError,
        )
        return link

    @staticmethod
    def get_link_for_playlist(user_id, target_playlist_id):
        """Get the raid link for a target playlist, or None."""
        return RaidPlaylistLink.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_playlist_id,
        ).first()

    @staticmethod
    def get_links_for_user(user_id):
        """Get all raid links for a user."""
        return RaidPlaylistLink.query.filter_by(
            user_id=user_id,
        ).order_by(
            RaidPlaylistLink.created_at.desc()
        ).all()

    @staticmethod
    def update_link(user_id, target_playlist_id, **kwargs):
        """Update a raid link's settings.

        Raises RaidLinkNotFoundError if not found.
        """
        link = RaidPlaylistLink.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_playlist_id,
        ).first()
        if not link:
            raise RaidLinkNotFoundError(
                "No raid link found for this playlist"
            )

        for key, value in kwargs.items():
            if hasattr(link, key) and value is not None:
                setattr(link, key, value)

        safe_commit(
            f"update raid link for "
            f"{target_playlist_id} (user {user_id})",
            RaidLinkError,
        )
        return link

    @staticmethod
    def delete_link(user_id, target_playlist_id):
        """Delete a raid playlist link.

        Raises RaidLinkNotFoundError if not found.
        """
        link = RaidPlaylistLink.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_playlist_id,
        ).first()
        if not link:
            raise RaidLinkNotFoundError(
                "No raid link found for this playlist"
            )
        db.session.delete(link)
        safe_commit(
            f"delete raid link for "
            f"{target_playlist_id} (user {user_id})",
            RaidLinkError,
        )

    @staticmethod
    def create_raid_playlist(api, user_id, name):
        """Create a new private Spotify playlist for raids.

        Returns (playlist_id, playlist_name).
        """
        return create_private_playlist(
            api,
            user_id,
            name,
            suffix="Raids",
            description=(
                "Raid staging playlist for incoming tracks"
            ),
        )
