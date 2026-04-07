"""
Tests for track lock utility functions in shuffle_algorithms/utils.py.

Covers split_locked_tracks and reassemble_with_locks.
"""

import pytest

from shuffify.shuffle_algorithms.utils import (
    split_locked_tracks,
    reassemble_with_locks,
)


class TestSplitLockedTracks:
    """Tests for split_locked_tracks."""

    def test_no_locks_returns_all_tracks(self):
        tracks = [
            {"uri": "spotify:track:a"},
            {"uri": "spotify:track:b"},
            {"uri": "spotify:track:c"},
        ]
        locked, unlocked = split_locked_tracks(tracks, {})
        assert locked == {}
        assert len(unlocked) == 3

    def test_none_locks_returns_all_tracks(self):
        tracks = [{"uri": "spotify:track:a"}]
        locked, unlocked = split_locked_tracks(tracks, None)
        assert locked == {}
        assert len(unlocked) == 1

    def test_valid_locks_split_correctly(self):
        tracks = [
            {"uri": "spotify:track:a"},
            {"uri": "spotify:track:b"},
            {"uri": "spotify:track:c"},
        ]
        locked_positions = {
            0: "spotify:track:a",
            2: "spotify:track:c",
        }
        locked, unlocked = split_locked_tracks(
            tracks, locked_positions
        )
        assert locked == {
            0: "spotify:track:a",
            2: "spotify:track:c",
        }
        assert len(unlocked) == 1
        assert unlocked[0]["uri"] == "spotify:track:b"

    def test_invalid_position_dropped(self):
        tracks = [{"uri": "spotify:track:a"}]
        locked_positions = {
            5: "spotify:track:a",  # Out of range
        }
        locked, unlocked = split_locked_tracks(
            tracks, locked_positions
        )
        assert locked == {}
        assert len(unlocked) == 1

    def test_mismatched_uri_dropped(self):
        tracks = [
            {"uri": "spotify:track:a"},
            {"uri": "spotify:track:b"},
        ]
        locked_positions = {
            0: "spotify:track:WRONG",  # Wrong URI
        }
        locked, unlocked = split_locked_tracks(
            tracks, locked_positions
        )
        assert locked == {}
        assert len(unlocked) == 2

    def test_string_position_keys_converted(self):
        tracks = [
            {"uri": "spotify:track:a"},
            {"uri": "spotify:track:b"},
        ]
        locked_positions = {
            "0": "spotify:track:a",
        }
        locked, unlocked = split_locked_tracks(
            tracks, locked_positions
        )
        assert 0 in locked
        assert len(unlocked) == 1

    def test_all_locked_returns_empty_unlocked(self):
        tracks = [
            {"uri": "spotify:track:a"},
            {"uri": "spotify:track:b"},
        ]
        locked_positions = {
            0: "spotify:track:a",
            1: "spotify:track:b",
        }
        locked, unlocked = split_locked_tracks(
            tracks, locked_positions
        )
        assert len(locked) == 2
        assert len(unlocked) == 0


class TestReassembleWithLocks:
    """Tests for reassemble_with_locks."""

    def test_no_locks_returns_shuffled(self):
        shuffled = ["a", "b", "c"]
        result = reassemble_with_locks(shuffled, {}, 3)
        assert result == ["a", "b", "c"]

    def test_locks_placed_correctly(self):
        shuffled_unlocked = ["b", "d"]
        locked = {0: "a", 2: "c"}
        result = reassemble_with_locks(
            shuffled_unlocked, locked, 4
        )
        assert result == ["a", "b", "c", "d"]

    def test_single_lock_at_start(self):
        shuffled_unlocked = ["b", "c"]
        locked = {0: "a"}
        result = reassemble_with_locks(
            shuffled_unlocked, locked, 3
        )
        assert result == ["a", "b", "c"]

    def test_single_lock_at_end(self):
        shuffled_unlocked = ["a", "b"]
        locked = {2: "c"}
        result = reassemble_with_locks(
            shuffled_unlocked, locked, 3
        )
        assert result == ["a", "b", "c"]

    def test_all_locked(self):
        locked = {0: "a", 1: "b", 2: "c"}
        result = reassemble_with_locks([], locked, 3)
        assert result == ["a", "b", "c"]

    def test_string_position_keys(self):
        shuffled_unlocked = ["b"]
        locked = {"0": "a", "2": "c"}
        result = reassemble_with_locks(
            shuffled_unlocked, locked, 3
        )
        assert result == ["a", "b", "c"]
