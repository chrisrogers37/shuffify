"""
Tests for StateService.

Tests cover playlist state history management, undo/redo functionality.
"""

import pytest
from unittest.mock import Mock

from shuffify.services import (
    StateService,
    StateError,
    NoHistoryError,
    AlreadyAtOriginalError,
    PlaylistState,
    PLAYLIST_STATES_KEY,
)


class TestPlaylistStateDataclass:
    """Tests for PlaylistState dataclass."""

    def test_to_dict(self):
        """Should convert to dictionary."""
        state = PlaylistState(
            states=[['uri1', 'uri2'], ['uri2', 'uri1']],
            current_index=1
        )

        result = state.to_dict()

        assert result == {
            'states': [['uri1', 'uri2'], ['uri2', 'uri1']],
            'current_index': 1
        }

    def test_from_dict(self):
        """Should create from dictionary."""
        data = {
            'states': [['uri1', 'uri2'], ['uri2', 'uri1']],
            'current_index': 1
        }

        state = PlaylistState.from_dict(data)

        assert state.states == [['uri1', 'uri2'], ['uri2', 'uri1']]
        assert state.current_index == 1

    def test_from_dict_with_missing_keys(self):
        """Should handle missing keys with defaults."""
        data = {}

        state = PlaylistState.from_dict(data)

        assert state.states == []
        assert state.current_index == 0


class TestStateServiceInitializeSession:
    """Tests for initialize_session method."""

    def test_initialize_session_creates_key(self, mock_session):
        """Should create playlist_states key if missing."""
        assert PLAYLIST_STATES_KEY not in mock_session

        StateService.initialize_session(mock_session)

        assert PLAYLIST_STATES_KEY in mock_session
        assert mock_session[PLAYLIST_STATES_KEY] == {}

    def test_initialize_session_preserves_existing(self, mock_session):
        """Should not overwrite existing playlist_states."""
        mock_session[PLAYLIST_STATES_KEY] = {'existing': 'data'}

        StateService.initialize_session(mock_session)

        assert mock_session[PLAYLIST_STATES_KEY] == {'existing': 'data'}


class TestStateServiceGetPlaylistState:
    """Tests for get_playlist_state method."""

    def test_get_playlist_state_exists(self, session_with_state):
        """Should return PlaylistState when it exists."""
        state = StateService.get_playlist_state(session_with_state, 'playlist123')

        assert isinstance(state, PlaylistState)
        assert state.current_index == 1
        assert len(state.states) == 2

    def test_get_playlist_state_not_exists(self, mock_session):
        """Should return None when playlist not in session."""
        StateService.initialize_session(mock_session)

        state = StateService.get_playlist_state(mock_session, 'nonexistent')

        assert state is None

    def test_get_playlist_state_initializes_session(self, mock_session):
        """Should initialize session if needed."""
        state = StateService.get_playlist_state(mock_session, 'playlist123')

        assert PLAYLIST_STATES_KEY in mock_session
        assert state is None


class TestStateServiceGetCurrentUris:
    """Tests for get_current_uris method."""

    def test_get_current_uris_returns_current_state(self, session_with_state, sample_track_uris):
        """Should return URIs at current index."""
        # Current index is 1, which is reversed
        result = StateService.get_current_uris(session_with_state, 'playlist123')

        assert result == sample_track_uris[::-1]

    def test_get_current_uris_no_state(self, mock_session):
        """Should return None when no state exists."""
        result = StateService.get_current_uris(mock_session, 'nonexistent')

        assert result is None


class TestStateServiceInitializePlaylistState:
    """Tests for initialize_playlist_state method."""

    def test_initialize_playlist_state_creates_state(self, mock_session, sample_track_uris):
        """Should create new state with initial URIs."""
        state = StateService.initialize_playlist_state(
            mock_session, 'new_playlist', sample_track_uris
        )

        assert isinstance(state, PlaylistState)
        assert state.states == [sample_track_uris]
        assert state.current_index == 0

    def test_initialize_playlist_state_saves_to_session(self, mock_session, sample_track_uris):
        """Should save state to session."""
        StateService.initialize_playlist_state(mock_session, 'new_playlist', sample_track_uris)

        assert 'new_playlist' in mock_session[PLAYLIST_STATES_KEY]
        assert mock_session.modified is True

    def test_initialize_playlist_state_empty_uris(self, mock_session):
        """Should handle empty URI list."""
        state = StateService.initialize_playlist_state(mock_session, 'empty_playlist', [])

        assert state.states == [[]]
        assert state.current_index == 0


class TestStateServiceRecordNewState:
    """Tests for record_new_state method."""

    def test_record_new_state_appends_state(self, session_with_state):
        """Should append new state and increment index."""
        new_uris = ['new1', 'new2', 'new3']

        state = StateService.record_new_state(session_with_state, 'playlist123', new_uris)

        assert len(state.states) == 3
        assert state.states[-1] == new_uris
        assert state.current_index == 2

    def test_record_new_state_truncates_future(self, mock_session, sample_track_uris):
        """Should truncate future states when recording after undo."""
        # Set up state with 3 states, current at index 1 (after undo)
        mock_session[PLAYLIST_STATES_KEY] = {
            'playlist123': {
                'states': [
                    sample_track_uris,
                    sample_track_uris[::-1],
                    ['future', 'state']  # This should be truncated
                ],
                'current_index': 1
            }
        }

        new_uris = ['brand', 'new']
        state = StateService.record_new_state(mock_session, 'playlist123', new_uris)

        assert len(state.states) == 3  # Original, reversed, new (future truncated)
        assert state.states[-1] == new_uris
        assert state.current_index == 2

    def test_record_new_state_sets_modified_flag(self, session_with_state):
        """Should set session.modified to True."""
        StateService.record_new_state(session_with_state, 'playlist123', ['new'])

        assert session_with_state.modified is True

    def test_record_new_state_no_existing_state(self, mock_session):
        """Should raise StateError when no existing state."""
        StateService.initialize_session(mock_session)

        with pytest.raises(StateError) as exc_info:
            StateService.record_new_state(mock_session, 'nonexistent', ['uri'])
        assert "No existing state" in str(exc_info.value)


class TestStateServiceCanUndo:
    """Tests for can_undo method."""

    def test_can_undo_true_when_not_at_original(self, session_with_state):
        """Should return True when current_index > 0."""
        result = StateService.can_undo(session_with_state, 'playlist123')

        assert result is True

    def test_can_undo_false_when_at_original(self, mock_session, sample_track_uris):
        """Should return False when at original state (index 0)."""
        StateService.initialize_playlist_state(mock_session, 'playlist123', sample_track_uris)

        result = StateService.can_undo(mock_session, 'playlist123')

        assert result is False

    def test_can_undo_false_when_no_state(self, mock_session):
        """Should return False when no state exists."""
        result = StateService.can_undo(mock_session, 'nonexistent')

        assert result is False


class TestStateServiceUndo:
    """Tests for undo method."""

    def test_undo_returns_previous_uris(self, session_with_state, sample_track_uris):
        """Should return URIs from previous state."""
        result = StateService.undo(session_with_state, 'playlist123')

        # Previous state is the original (index 0)
        assert result == sample_track_uris

    def test_undo_decrements_index(self, session_with_state):
        """Should decrement current_index."""
        StateService.undo(session_with_state, 'playlist123')

        state = StateService.get_playlist_state(session_with_state, 'playlist123')
        assert state.current_index == 0

    def test_undo_sets_modified_flag(self, session_with_state):
        """Should set session.modified to True."""
        StateService.undo(session_with_state, 'playlist123')

        assert session_with_state.modified is True

    def test_undo_raises_no_history_error(self, mock_session):
        """Should raise NoHistoryError when no state exists."""
        StateService.initialize_session(mock_session)

        with pytest.raises(NoHistoryError) as exc_info:
            StateService.undo(mock_session, 'nonexistent')
        assert "No state history" in str(exc_info.value)

    def test_undo_raises_already_at_original(self, mock_session, sample_track_uris):
        """Should raise AlreadyAtOriginalError when at index 0."""
        StateService.initialize_playlist_state(mock_session, 'playlist123', sample_track_uris)

        with pytest.raises(AlreadyAtOriginalError) as exc_info:
            StateService.undo(mock_session, 'playlist123')
        assert "Already at original state" in str(exc_info.value)


class TestStateServiceRevertUndo:
    """Tests for revert_undo method."""

    def test_revert_undo_increments_index(self, mock_session, sample_track_uris):
        """Should increment index back after failed update."""
        # Set up state at index 0 with 2 states
        mock_session[PLAYLIST_STATES_KEY] = {
            'playlist123': {
                'states': [sample_track_uris, sample_track_uris[::-1]],
                'current_index': 0
            }
        }

        StateService.revert_undo(mock_session, 'playlist123')

        state = StateService.get_playlist_state(mock_session, 'playlist123')
        assert state.current_index == 1

    def test_revert_undo_sets_modified_flag(self, mock_session, sample_track_uris):
        """Should set session.modified to True."""
        mock_session[PLAYLIST_STATES_KEY] = {
            'playlist123': {
                'states': [sample_track_uris, sample_track_uris[::-1]],
                'current_index': 0
            }
        }

        StateService.revert_undo(mock_session, 'playlist123')

        assert mock_session.modified is True

    def test_revert_undo_no_state_does_nothing(self, mock_session):
        """Should do nothing when no state exists."""
        StateService.initialize_session(mock_session)

        # Should not raise
        StateService.revert_undo(mock_session, 'nonexistent')

    def test_revert_undo_at_end_does_nothing(self, session_with_state):
        """Should not increment past end of states list."""
        # current_index is 1 (last state)
        StateService.revert_undo(session_with_state, 'playlist123')

        state = StateService.get_playlist_state(session_with_state, 'playlist123')
        assert state.current_index == 1  # Unchanged


class TestStateServiceGetStateInfo:
    """Tests for get_state_info method."""

    def test_get_state_info_returns_dict(self, session_with_state):
        """Should return state as dictionary."""
        result = StateService.get_state_info(session_with_state, 'playlist123')

        assert isinstance(result, dict)
        assert 'states' in result
        assert 'current_index' in result

    def test_get_state_info_no_state(self, mock_session):
        """Should return None when no state exists."""
        result = StateService.get_state_info(mock_session, 'nonexistent')

        assert result is None


class TestStateServiceEnsurePlaylistInitialized:
    """Tests for ensure_playlist_initialized method."""

    def test_ensure_initialized_creates_new(self, mock_session, sample_track_uris):
        """Should create new state if not exists."""
        state = StateService.ensure_playlist_initialized(
            mock_session, 'new_playlist', sample_track_uris
        )

        assert isinstance(state, PlaylistState)
        assert state.states == [sample_track_uris]
        assert state.current_index == 0

    def test_ensure_initialized_returns_existing(self, session_with_state, sample_track_uris):
        """Should return existing state if exists."""
        state = StateService.ensure_playlist_initialized(
            session_with_state, 'playlist123', ['should', 'be', 'ignored']
        )

        # Should have existing 2 states, not new ones
        assert len(state.states) == 2
        assert state.current_index == 1


class TestStateServiceIntegration:
    """Integration tests for complete undo/redo workflows."""

    def test_full_shuffle_undo_workflow(self, mock_session):
        """Test complete workflow: init -> shuffle -> shuffle -> undo -> undo."""
        playlist_id = 'test_playlist'
        original = ['a', 'b', 'c']
        shuffle1 = ['c', 'a', 'b']
        shuffle2 = ['b', 'c', 'a']

        # Initialize
        StateService.ensure_playlist_initialized(mock_session, playlist_id, original)
        assert StateService.can_undo(mock_session, playlist_id) is False

        # First shuffle
        StateService.record_new_state(mock_session, playlist_id, shuffle1)
        assert StateService.can_undo(mock_session, playlist_id) is True
        assert StateService.get_current_uris(mock_session, playlist_id) == shuffle1

        # Second shuffle
        StateService.record_new_state(mock_session, playlist_id, shuffle2)
        assert StateService.get_current_uris(mock_session, playlist_id) == shuffle2

        # Undo first time (back to shuffle1)
        result = StateService.undo(mock_session, playlist_id)
        assert result == shuffle1
        assert StateService.get_current_uris(mock_session, playlist_id) == shuffle1

        # Undo second time (back to original)
        result = StateService.undo(mock_session, playlist_id)
        assert result == original
        assert StateService.can_undo(mock_session, playlist_id) is False

    def test_shuffle_after_undo_truncates_future(self, mock_session):
        """Test that shuffling after undo removes future states."""
        playlist_id = 'test_playlist'
        original = ['a', 'b', 'c']
        shuffle1 = ['c', 'a', 'b']
        shuffle2 = ['b', 'c', 'a']
        new_shuffle = ['a', 'c', 'b']

        # Init -> shuffle -> shuffle
        StateService.ensure_playlist_initialized(mock_session, playlist_id, original)
        StateService.record_new_state(mock_session, playlist_id, shuffle1)
        StateService.record_new_state(mock_session, playlist_id, shuffle2)

        # Undo once
        StateService.undo(mock_session, playlist_id)

        # New shuffle (should truncate shuffle2)
        StateService.record_new_state(mock_session, playlist_id, new_shuffle)

        state = StateService.get_playlist_state(mock_session, playlist_id)
        assert len(state.states) == 3  # original, shuffle1, new_shuffle
        assert state.states[-1] == new_shuffle
        assert shuffle2 not in state.states
