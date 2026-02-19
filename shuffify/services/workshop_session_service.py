"""
Workshop session service for saving and loading workshop state.

Handles CRUD operations for WorkshopSession records, enabling users
to save their track arrangements and resume later.
"""

import logging
from typing import List, Optional

from shuffify.models.db import db, WorkshopSession
from shuffify.services.base import (
    safe_commit,
    get_user_or_raise,
    get_owned_entity,
)

logger = logging.getLogger(__name__)

# Maximum number of saved sessions per user per playlist
MAX_SESSIONS_PER_PLAYLIST = 10


class WorkshopSessionError(Exception):
    """Base exception for workshop session operations."""

    pass


class WorkshopSessionNotFoundError(WorkshopSessionError):
    """Raised when a workshop session cannot be found."""

    pass


class WorkshopSessionLimitError(WorkshopSessionError):
    """Raised when user has too many saved sessions for a playlist."""

    pass


class WorkshopSessionService:
    """Service for managing saved workshop sessions."""

    @staticmethod
    def save_session(
        spotify_id: str,
        playlist_id: str,
        session_name: str,
        track_uris: List[str],
    ) -> WorkshopSession:
        """
        Save a workshop session for a user.

        Args:
            spotify_id: The Spotify user ID.
            playlist_id: The Spotify playlist ID.
            session_name: A user-provided name for this saved session.
            track_uris: Ordered list of track URIs.

        Returns:
            The created WorkshopSession instance.

        Raises:
            WorkshopSessionError: If the user does not exist.
            WorkshopSessionLimitError: If the user already has the max
                number of sessions for this playlist.
        """
        if not session_name or not session_name.strip():
            raise WorkshopSessionError(
                "Session name cannot be empty"
            )

        session_name = session_name.strip()

        user = get_user_or_raise(
            spotify_id, WorkshopSessionError
        )

        # Check session limit per playlist
        existing_count = WorkshopSession.query.filter_by(
            user_id=user.id, playlist_id=playlist_id
        ).count()

        if existing_count >= MAX_SESSIONS_PER_PLAYLIST:
            raise WorkshopSessionLimitError(
                f"Maximum of {MAX_SESSIONS_PER_PLAYLIST} saved "
                f"sessions per playlist reached. "
                f"Delete an existing session first."
            )

        ws = WorkshopSession(
            user_id=user.id,
            playlist_id=playlist_id,
            session_name=session_name,
        )
        ws.track_uris = track_uris

        db.session.add(ws)
        safe_commit(
            f"save workshop session '{session_name}' for "
            f"user {spotify_id}, playlist {playlist_id} "
            f"({len(track_uris)} tracks)",
            WorkshopSessionError,
        )
        return ws

    @staticmethod
    def list_sessions(
        spotify_id: str, playlist_id: str
    ) -> List[WorkshopSession]:
        """
        List all saved workshop sessions for a user and playlist.

        Args:
            spotify_id: The Spotify user ID.
            playlist_id: The Spotify playlist ID.

        Returns:
            List of WorkshopSession instances, most recent first.
        """
        user = get_user_or_raise(spotify_id)
        if not user:
            return []

        return (
            WorkshopSession.query.filter_by(
                user_id=user.id, playlist_id=playlist_id
            )
            .order_by(WorkshopSession.updated_at.desc())
            .all()
        )

    @staticmethod
    def get_session(
        session_id: int, spotify_id: str
    ) -> WorkshopSession:
        """
        Get a specific workshop session by ID.

        Args:
            session_id: The workshop session database ID.
            spotify_id: The Spotify user ID (for ownership check).

        Returns:
            WorkshopSession instance.

        Raises:
            WorkshopSessionNotFoundError: If not found or not owned.
        """
        user = get_user_or_raise(
            spotify_id, WorkshopSessionNotFoundError
        )
        return get_owned_entity(
            WorkshopSession,
            session_id,
            user.id,
            WorkshopSessionNotFoundError,
        )

    @staticmethod
    def update_session(
        session_id: int,
        spotify_id: str,
        track_uris: List[str],
        session_name: Optional[str] = None,
    ) -> WorkshopSession:
        """
        Update an existing workshop session.

        Args:
            session_id: The workshop session database ID.
            spotify_id: The Spotify user ID (for ownership check).
            track_uris: The new ordered list of track URIs.
            session_name: Optional new name for the session.

        Returns:
            The updated WorkshopSession instance.

        Raises:
            WorkshopSessionNotFoundError: If not found or not owned.
            WorkshopSessionError: If the update fails.
        """
        ws = WorkshopSessionService.get_session(
            session_id, spotify_id
        )

        ws.track_uris = track_uris
        if session_name is not None:
            ws.session_name = session_name.strip()
        safe_commit(
            f"update workshop session {session_id}: "
            f"'{ws.session_name}' ({len(track_uris)} tracks)",
            WorkshopSessionError,
        )
        return ws

    @staticmethod
    def delete_session(
        session_id: int, spotify_id: str
    ) -> bool:
        """
        Delete a saved workshop session.

        Args:
            session_id: The workshop session database ID.
            spotify_id: The Spotify user ID (for ownership check).

        Returns:
            True if deleted successfully.

        Raises:
            WorkshopSessionNotFoundError: If not found or not owned.
            WorkshopSessionError: If the deletion fails.
        """
        ws = WorkshopSessionService.get_session(
            session_id, spotify_id
        )

        db.session.delete(ws)
        safe_commit(
            f"delete workshop session {session_id}",
            WorkshopSessionError,
        )
        return True
