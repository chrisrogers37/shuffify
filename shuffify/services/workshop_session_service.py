"""
Workshop session service for saving and loading workshop state.

Handles CRUD operations for WorkshopSession records, enabling users
to save their track arrangements and resume later.
"""

import logging
from typing import List, Optional

from shuffify.models.db import db, WorkshopSession, User

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

        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise WorkshopSessionError(
                f"User not found for spotify_id: {spotify_id}. "
                f"User must be logged in first."
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

        try:
            ws = WorkshopSession(
                user_id=user.id,
                playlist_id=playlist_id,
                session_name=session_name,
            )
            ws.track_uris = track_uris

            db.session.add(ws)
            db.session.commit()

            logger.info(
                f"Saved workshop session '{session_name}' for user "
                f"{spotify_id}, playlist {playlist_id} "
                f"({len(track_uris)} tracks)"
            )
            return ws

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to save workshop session: {e}",
                exc_info=True,
            )
            raise WorkshopSessionError(
                f"Failed to save session: {e}"
            )

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
        user = User.query.filter_by(spotify_id=spotify_id).first()
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
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise WorkshopSessionNotFoundError("User not found")

        ws = db.session.get(WorkshopSession, session_id)
        if not ws or ws.user_id != user.id:
            raise WorkshopSessionNotFoundError(
                f"Workshop session {session_id} not found"
            )

        return ws

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

        try:
            ws.track_uris = track_uris
            if session_name is not None:
                ws.session_name = session_name.strip()
            db.session.commit()

            logger.info(
                f"Updated workshop session {session_id}: "
                f"'{ws.session_name}' ({len(track_uris)} tracks)"
            )
            return ws

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to update workshop session "
                f"{session_id}: {e}",
                exc_info=True,
            )
            raise WorkshopSessionError(
                f"Failed to update session: {e}"
            )

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

        try:
            db.session.delete(ws)
            db.session.commit()
            logger.info(
                f"Deleted workshop session {session_id}"
            )
            return True

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to delete workshop session "
                f"{session_id}: {e}",
                exc_info=True,
            )
            raise WorkshopSessionError(
                f"Failed to delete session: {e}"
            )
