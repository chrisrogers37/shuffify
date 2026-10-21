"""
Service for managing raid playlist links (target + raid playlist).

Mirrors PlaylistPairService for the upstream raid direction.
"""

import logging

from shuffify.models.db import RaidPlaylistLink, db
from shuffify.services.base import commit_or_stage
from shuffify.services.playlist_lock import locked_commit_target

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
    """Manages raid playlist link CRUD operations.

    Mutators take a keyword-only ``commit``: ``commit=False`` stages the change
    (it flushes, lets errors propagate and leaves the transaction to the caller),
    for the one-transaction playlist setup Save. The default commits.
    """

    @staticmethod
    @locked_commit_target("target_playlist_id")
    def create_link(
        user_id,
        target_playlist_id,
        raid_playlist_id,
        target_playlist_name=None,
        raid_playlist_name=None,
        drip_count=3,
        drip_enabled=False,
        *,
        commit=True,
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
        commit_or_stage(
            commit,
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
    @locked_commit_target("target_playlist_id")
    def update_link(user_id, target_playlist_id, *, commit=True, **kwargs):
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

        commit_or_stage(
            commit,
            f"update raid link for "
            f"{target_playlist_id} (user {user_id})",
            RaidLinkError,
        )
        return link

    @staticmethod
    @locked_commit_target("target_playlist_id")
    def delete_link(user_id, target_playlist_id, *, commit=True):
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
        commit_or_stage(
            commit,
            f"delete raid link for "
            f"{target_playlist_id} (user {user_id})",
            RaidLinkError,
        )
