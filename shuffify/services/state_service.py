"""
State service for managing playlist state history.

Handles undo/redo functionality by maintaining a history of playlist states
in the session. Each playlist has its own independent state history.
"""

import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Session key for storing playlist states
PLAYLIST_STATES_KEY = "playlist_states"


@dataclass
class PlaylistState:
    """Represents the state history for a single playlist."""

    states: List[List[str]]  # List of URI lists (each list is a state)
    current_index: int  # Index pointing to the current state

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for session storage."""
        return {"states": self.states, "current_index": self.current_index}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlaylistState":
        """Create from dictionary (from session)."""
        return cls(
            states=data.get("states", []), current_index=data.get("current_index", 0)
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


class StaleHistoryError(StateError):
    """The selected session revision changed before it could be confirmed."""


@dataclass(frozen=True)
class UndoSelection:
    states: tuple[tuple[str, ...], ...]
    current_index: int

    @property
    def desired_uris(self):
        return self.states[self.current_index - 1]

    def state_dict(self):
        return {"states": [list(uris) for uris in self.states], "current_index": self.current_index}


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
        session: Dict[str, Any], playlist_id: str
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
        session: Dict[str, Any], playlist_id: str
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
        session: Dict[str, Any], playlist_id: str, initial_uris: List[str]
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

        state = PlaylistState(states=[initial_uris], current_index=0)

        session[PLAYLIST_STATES_KEY][playlist_id] = state.to_dict()
        session.modified = True

        logger.info(
            f"Initialized state for playlist {playlist_id} with {len(initial_uris)} tracks"
        )
        return state

    @staticmethod
    def record_new_state(
        session: Dict[str, Any], playlist_id: str, new_uris: List[str]
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
        state.states = state.states[: state.current_index + 1]

        # Add the new state
        state.states.append(new_uris)
        state.current_index += 1

        # Save back to session
        session[PLAYLIST_STATES_KEY][playlist_id] = state.to_dict()
        session.modified = True

        logger.info(
            f"Recorded new state for playlist {playlist_id}, index now at {state.current_index}"
        )
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
            raise AlreadyAtOriginalError(
                f"Already at original state for playlist {playlist_id}"
            )

        # Move to previous state
        state.current_index -= 1
        previous_uris = state.states[state.current_index]

        # Save back to session
        session[PLAYLIST_STATES_KEY][playlist_id] = state.to_dict()
        session.modified = True

        logger.info(
            f"Undo for playlist {playlist_id}, index now at {state.current_index}"
        )
        return previous_uris

    @staticmethod
    def ensure_playlist_initialized(
        session: Dict[str, Any], playlist_id: str, current_uris: List[str]
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
            logger.info(
                f"First interaction with playlist {playlist_id}, storing original state"
            )
            state = StateService.initialize_playlist_state(
                session, playlist_id, current_uris
            )

        return state

    @staticmethod
    def peek_undo(session, playlist_id) -> UndoSelection:
        """Copy the next Undo revision without initializing or touching session."""
        raw = session.get(PLAYLIST_STATES_KEY, {}).get(playlist_id)
        if not raw:
            raise NoHistoryError("No saved playlist history is available in this session.")
        states, index = raw.get("states"), raw.get("current_index")
        if (not isinstance(states, list) or not states or type(index) is not int
                or not 0 <= index < len(states)
                or any(not isinstance(uris, list) or any(not isinstance(u, str) for u in uris) for uris in states)):
            raise StateError("The saved playlist history is unavailable.")
        if index == 0:
            raise AlreadyAtOriginalError("Already at original playlist state.")
        return UndoSelection(tuple(tuple(uris) for uris in states), index)

    @staticmethod
    def assert_undo_selection(session, playlist_id, selection) -> None:
        if session.get(PLAYLIST_STATES_KEY, {}).get(playlist_id) != selection.state_dict():
            raise StaleHistoryError("The selected saved version changed. Review the current history.")

    @staticmethod
    def confirm_undo(session, playlist_id, selection) -> PlaylistState:
        StateService.assert_undo_selection(session, playlist_id, selection)
        state = PlaylistState([list(uris) for uris in selection.states], selection.current_index - 1)
        StateService._assign_verified_state(session, playlist_id, state)
        return state

    @staticmethod
    def record_verified_change(session, playlist_id, *, before_uris, after_uris) -> PlaylistState:
        """Build actual-before and desired history before one session assignment."""
        raw = deepcopy(session.get(PLAYLIST_STATES_KEY, {}).get(playlist_id))
        if not raw:
            state = PlaylistState([list(before_uris)], 0)
        else:
            state = PlaylistState.from_dict(raw)
            if (type(state.current_index) is not int or not 0 <= state.current_index < len(state.states)):
                raise StateError("The saved playlist history is unavailable.")
        for uris in (list(before_uris), list(after_uris)):
            if state.states[state.current_index] != uris:
                state.states = state.states[:state.current_index + 1] + [uris]
                state.current_index += 1
        StateService._assign_verified_state(session, playlist_id, state)
        return state

    @staticmethod
    def _assign_verified_state(session, playlist_id, state):
        histories = dict(session.get(PLAYLIST_STATES_KEY, {}))
        histories[playlist_id] = deepcopy(state.to_dict())
        session[PLAYLIST_STATES_KEY] = histories
        session.modified = True
