"""
State service for managing playlist state history.

Handles undo/redo functionality by maintaining a history of playlist states
in the session. Each playlist has its own independent state history.
"""

import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Session key for storing playlist states
PLAYLIST_STATES_KEY = 'playlist_states'


@dataclass
class PlaylistState:
    """Represents the state history for a single playlist."""
    states: List[List[str]]  # List of URI lists (each list is a state)
    current_index: int  # Index pointing to the current state

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for session storage."""
        return {
            'states': self.states,
            'current_index': self.current_index
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PlaylistState':
        """Create from dictionary (from session)."""
        return cls(
            states=data.get('states', []),
            current_index=data.get('current_index', 0)
        )


class StateError(Exception):
    """Base exception for state operations."""
    pass


class NoHistoryError(StateError):
    """Raised when there's no history to restore."""
    pass


class AlreadyAtOriginalError(StateError):
    """Raised when already at the original state."""
    pass


class StateService:
    """
    Service for managing playlist state history.

    The state history is stored in the Flask session as:
    {
        'playlist_states': {
            'playlist_id': {
                'states': [
                    ['uri1', 'uri2', ...],  # Original state (index 0)
                    ['uri2', 'uri1', ...],  # After first shuffle
                    ...
                ],
                'current_index': 1  # Points to current state
            }
        }
    }
    """

    @staticmethod
    def initialize_session(session: Dict[str, Any]) -> None:
        """
        Ensure the session has the playlist_states structure.

        Args:
            session: The Flask session object.
        """
        if PLAYLIST_STATES_KEY not in session:
            session[PLAYLIST_STATES_KEY] = {}

    @staticmethod
    def get_playlist_state(
        session: Dict[str, Any],
        playlist_id: str
    ) -> Optional[PlaylistState]:
        """
        Get the state history for a playlist.

        Args:
            session: The Flask session object.
            playlist_id: The Spotify playlist ID.

        Returns:
            PlaylistState if exists, None otherwise.
        """
        StateService.initialize_session(session)
        state_data = session[PLAYLIST_STATES_KEY].get(playlist_id)

        if not state_data:
            return None

        return PlaylistState.from_dict(state_data)

    @staticmethod
    def get_current_uris(
        session: Dict[str, Any],
        playlist_id: str
    ) -> Optional[List[str]]:
        """
        Get the current track URIs for a playlist from state history.

        Args:
            session: The Flask session object.
            playlist_id: The Spotify playlist ID.

        Returns:
            List of URIs if state exists, None otherwise.
        """
        state = StateService.get_playlist_state(session, playlist_id)
        if not state or not state.states:
            return None
        return state.states[state.current_index]

    @staticmethod
    def initialize_playlist_state(
        session: Dict[str, Any],
        playlist_id: str,
        initial_uris: List[str]
    ) -> PlaylistState:
        """
        Initialize state history for a playlist with its original order.

        Args:
            session: The Flask session object.
            playlist_id: The Spotify playlist ID.
            initial_uris: The original track URIs.

        Returns:
            The initialized PlaylistState.
        """
        StateService.initialize_session(session)

        state = PlaylistState(
            states=[initial_uris],
            current_index=0
        )

        session[PLAYLIST_STATES_KEY][playlist_id] = state.to_dict()
        session.modified = True

        logger.info(f"Initialized state for playlist {playlist_id} with {len(initial_uris)} tracks")
        return state

    @staticmethod
    def record_new_state(
        session: Dict[str, Any],
        playlist_id: str,
        new_uris: List[str]
    ) -> PlaylistState:
        """
        Record a new state after a shuffle operation.

        If the user had undone steps, this truncates future states before
        adding the new one (can't redo after making a new change).

        Args:
            session: The Flask session object.
            playlist_id: The Spotify playlist ID.
            new_uris: The new track URIs after shuffle.

        Returns:
            The updated PlaylistState.

        Raises:
            StateError: If no existing state for this playlist.
        """
        state = StateService.get_playlist_state(session, playlist_id)
        if not state:
            raise StateError(f"No existing state for playlist {playlist_id}")

        # Truncate any future states (from previous undos)
        state.states = state.states[:state.current_index + 1]

        # Add the new state
        state.states.append(new_uris)
        state.current_index += 1

        # Save back to session
        session[PLAYLIST_STATES_KEY][playlist_id] = state.to_dict()
        session.modified = True

        logger.info(f"Recorded new state for playlist {playlist_id}, index now at {state.current_index}")
        return state

    @staticmethod
    def can_undo(session: Dict[str, Any], playlist_id: str) -> bool:
        """
        Check if undo is possible for a playlist.

        Args:
            session: The Flask session object.
            playlist_id: The Spotify playlist ID.

        Returns:
            True if we can undo (not at original state), False otherwise.
        """
        state = StateService.get_playlist_state(session, playlist_id)
        if not state:
            return False
        return state.current_index > 0

    @staticmethod
    def undo(session: Dict[str, Any], playlist_id: str) -> List[str]:
        """
        Step back to the previous state and return those URIs.

        Args:
            session: The Flask session object.
            playlist_id: The Spotify playlist ID.

        Returns:
            List of URIs for the previous state.

        Raises:
            NoHistoryError: If no state history exists.
            AlreadyAtOriginalError: If already at the original state.
        """
        state = StateService.get_playlist_state(session, playlist_id)

        if not state:
            raise NoHistoryError(f"No state history for playlist {playlist_id}")

        if state.current_index <= 0:
            raise AlreadyAtOriginalError(f"Already at original state for playlist {playlist_id}")

        # Move to previous state
        state.current_index -= 1
        previous_uris = state.states[state.current_index]

        # Save back to session
        session[PLAYLIST_STATES_KEY][playlist_id] = state.to_dict()
        session.modified = True

        logger.info(f"Undo for playlist {playlist_id}, index now at {state.current_index}")
        return previous_uris

    @staticmethod
    def revert_undo(session: Dict[str, Any], playlist_id: str) -> None:
        """
        Revert an undo operation (if the Spotify update failed).

        Args:
            session: The Flask session object.
            playlist_id: The Spotify playlist ID.
        """
        state = StateService.get_playlist_state(session, playlist_id)
        if not state:
            return

        # Move back forward
        if state.current_index < len(state.states) - 1:
            state.current_index += 1
            session[PLAYLIST_STATES_KEY][playlist_id] = state.to_dict()
            session.modified = True
            logger.warning(f"Reverted undo for playlist {playlist_id}")

    @staticmethod
    def get_state_info(
        session: Dict[str, Any],
        playlist_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get state information for a playlist (for API responses).

        Args:
            session: The Flask session object.
            playlist_id: The Spotify playlist ID.

        Returns:
            Dictionary with state info, or None if no state.
        """
        state = StateService.get_playlist_state(session, playlist_id)
        if not state:
            return None
        return state.to_dict()

    @staticmethod
    def ensure_playlist_initialized(
        session: Dict[str, Any],
        playlist_id: str,
        current_uris: List[str]
    ) -> PlaylistState:
        """
        Ensure a playlist has state initialized, creating it if needed.

        Args:
            session: The Flask session object.
            playlist_id: The Spotify playlist ID.
            current_uris: The current track URIs to use if initializing.

        Returns:
            The PlaylistState (existing or newly created).
        """
        state = StateService.get_playlist_state(session, playlist_id)

        if state is None:
            logger.info(f"First interaction with playlist {playlist_id}, storing original state")
            state = StateService.initialize_playlist_state(session, playlist_id, current_uris)

        return state
