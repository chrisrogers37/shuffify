"""
Service for managing per-user playlist display preferences.

Handles sort ordering, hide/show toggling, pin toggling, and
bulk order updates for the dashboard playlist grid.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from shuffify.models.db import db, PlaylistPreference
from shuffify.services.base import safe_commit

logger = logging.getLogger(__name__)


class PlaylistPreferenceError(Exception):
    """Base error for playlist preference operations."""

    pass


class PlaylistPreferenceNotFoundError(
    PlaylistPreferenceError
):
    """Raised when a preference record is not found."""

    pass


class PlaylistPreferenceService:
    """Manages PlaylistPreference CRUD operations."""

    @staticmethod
    def get_user_preferences(
        user_id: int,
    ) -> Dict[str, PlaylistPreference]:
        """
        Get all playlist preferences for a user.

        Returns:
            Dict mapping spotify_playlist_id to
            PlaylistPreference.
        """
        prefs = PlaylistPreference.query.filter_by(
            user_id=user_id
        ).all()
        return {
            p.spotify_playlist_id: p for p in prefs
        }

    @staticmethod
    def get_preference(
        user_id: int,
        spotify_playlist_id: str,
    ) -> Optional[PlaylistPreference]:
        """Get a single preference record, or None."""
        return PlaylistPreference.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=spotify_playlist_id,
        ).first()

    @staticmethod
    def save_order(
        user_id: int,
        ordered_playlist_ids: List[str],
    ) -> int:
        """
        Bulk upsert sort_order for an ordered list of
        playlist IDs.

        Creates preference records for playlists that don't
        have one. Updates sort_order for existing records.

        Returns:
            Number of preferences updated/created.
        """
        existing = {
            p.spotify_playlist_id: p
            for p in PlaylistPreference.query.filter_by(
                user_id=user_id
            ).all()
        }

        count = 0
        for index, playlist_id in enumerate(
            ordered_playlist_ids
        ):
            pref = existing.get(playlist_id)
            if pref:
                pref.sort_order = index
                pref.updated_at = datetime.now(
                    timezone.utc
                )
            else:
                pref = PlaylistPreference(
                    user_id=user_id,
                    spotify_playlist_id=playlist_id,
                    sort_order=index,
                )
                db.session.add(pref)
            count += 1

        safe_commit(
            f"save playlist order ({count} items) "
            f"for user {user_id}",
            PlaylistPreferenceError,
        )
        return count

    @staticmethod
    def toggle_hidden(
        user_id: int,
        spotify_playlist_id: str,
    ) -> bool:
        """
        Toggle the is_hidden flag for a playlist.
        Creates a preference record if one doesn't exist.

        Returns:
            The new is_hidden value.
        """
        pref = PlaylistPreference.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=spotify_playlist_id,
        ).first()

        if pref:
            pref.is_hidden = not pref.is_hidden
            pref.updated_at = datetime.now(timezone.utc)
        else:
            pref = PlaylistPreference(
                user_id=user_id,
                spotify_playlist_id=spotify_playlist_id,
                is_hidden=True,
            )
            db.session.add(pref)

        safe_commit(
            f"toggle hidden for playlist "
            f"{spotify_playlist_id} (user {user_id})",
            PlaylistPreferenceError,
        )
        return pref.is_hidden

    @staticmethod
    def toggle_pinned(
        user_id: int,
        spotify_playlist_id: str,
    ) -> bool:
        """
        Toggle the is_pinned flag for a playlist.
        Creates a preference record if one doesn't exist.

        Returns:
            The new is_pinned value.
        """
        pref = PlaylistPreference.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=spotify_playlist_id,
        ).first()

        if pref:
            pref.is_pinned = not pref.is_pinned
            pref.updated_at = datetime.now(timezone.utc)
        else:
            pref = PlaylistPreference(
                user_id=user_id,
                spotify_playlist_id=spotify_playlist_id,
                is_pinned=True,
            )
            db.session.add(pref)

        safe_commit(
            f"toggle pinned for playlist "
            f"{spotify_playlist_id} (user {user_id})",
            PlaylistPreferenceError,
        )
        return pref.is_pinned

    @staticmethod
    def reset_preferences(user_id: int) -> int:
        """
        Delete all playlist preferences for a user.

        Returns:
            Number of records deleted.
        """
        count = PlaylistPreference.query.filter_by(
            user_id=user_id
        ).delete()
        safe_commit(
            f"reset all playlist preferences "
            f"({count} deleted) for user {user_id}",
            PlaylistPreferenceError,
        )
        return count

    @staticmethod
    def apply_preferences(playlists, preferences):
        """
        Apply user preferences to sort and filter a
        playlist list.

        Ordering logic:
        1. Pinned playlists first, sorted by sort_order
        2. Unpinned playlists, sorted by sort_order
        3. Unknown playlists last, in original order
        4. Hidden playlists excluded from visible result

        Returns:
            Tuple of (visible_playlists, hidden_playlists).
        """
        if not preferences:
            return list(playlists), []

        known = []
        unknown = []
        for pl in playlists:
            pl_id = (
                pl.get("id")
                if hasattr(pl, "get")
                else getattr(pl, "id", "")
            )
            pref = preferences.get(pl_id)
            if pref:
                known.append((pl, pref))
            else:
                unknown.append(pl)

        visible_known = [
            (p, pref)
            for p, pref in known
            if not pref.is_hidden
        ]
        hidden = [
            p for p, pref in known if pref.is_hidden
        ]

        visible_known.sort(
            key=lambda pair: (
                not pair[1].is_pinned,
                pair[1].sort_order,
            )
        )

        visible = [p for p, _ in visible_known] + unknown
        return visible, hidden
