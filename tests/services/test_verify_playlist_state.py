"""
Unit tests for JobExecutorService.verify_playlist_state and
PlaylistVerificationError.

Covers:
  - Exact match returns the actual URI list
  - Count drift raises with structured missing/extra
  - URI substitution (same count, wrong members) raises
  - Duplicates honored — multiset semantics, not set
  - Paginated re-fetch via mocked api.get_playlist_tracks
  - skip_cache=True is forwarded to the API call
  - Empty expected + empty actual is a clean pass
  - exception attributes carry playlist_id / phase / schedule_id
"""

from unittest.mock import MagicMock

import pytest

from shuffify.services.executors import (
    JobExecutorService,
    PlaylistVerificationError,
)


def _tracks(uris):
    return [{"uri": u, "name": u} for u in uris]


class TestVerifyPlaylistStateMatch:
    def test_exact_match_returns_actual_uris(self):
        api = MagicMock()
        api.get_playlist_tracks.return_value = _tracks(
            ["u1", "u2", "u3"]
        )

        result = JobExecutorService.verify_playlist_state(
            api, "p1", ["u1", "u2", "u3"], 42, "test",
        )

        assert result == ["u1", "u2", "u3"]

    def test_empty_expected_and_actual_passes(self):
        api = MagicMock()
        api.get_playlist_tracks.return_value = []

        result = JobExecutorService.verify_playlist_state(
            api, "p1", [], 42, "empty",
        )

        assert result == []

    def test_order_does_not_matter_for_match(self):
        """Multiset compare ignores order."""
        api = MagicMock()
        api.get_playlist_tracks.return_value = _tracks(
            ["u3", "u1", "u2"]
        )

        # No raise — same multiset.
        JobExecutorService.verify_playlist_state(
            api, "p1", ["u1", "u2", "u3"], 42, "test",
        )

    def test_skip_cache_is_forwarded(self):
        api = MagicMock()
        api.get_playlist_tracks.return_value = _tracks(
            ["u1"]
        )

        JobExecutorService.verify_playlist_state(
            api, "p1", ["u1"], 42, "test",
        )

        api.get_playlist_tracks.assert_called_once_with(
            "p1", skip_cache=True,
        )


class TestVerifyPlaylistStateDivergence:
    def test_count_drift_raises_with_missing(self):
        api = MagicMock()
        api.get_playlist_tracks.return_value = _tracks(
            ["u1", "u2"]
        )

        with pytest.raises(PlaylistVerificationError) as ex:
            JobExecutorService.verify_playlist_state(
                api, "p1",
                ["u1", "u2", "u3"], 42, "swap",
            )

        assert ex.value.missing == ["u3"]
        assert ex.value.extra == []
        assert ex.value.playlist_id == "p1"
        assert ex.value.phase == "swap"
        assert ex.value.schedule_id == 42

    def test_count_drift_raises_with_extra(self):
        api = MagicMock()
        api.get_playlist_tracks.return_value = _tracks(
            ["u1", "u2", "u3", "u4"]
        )

        with pytest.raises(PlaylistVerificationError) as ex:
            JobExecutorService.verify_playlist_state(
                api, "p1",
                ["u1", "u2"], 42, "swap",
            )

        assert sorted(ex.value.extra) == ["u3", "u4"]
        assert ex.value.missing == []

    def test_uri_substitution_raises(self):
        """Same count, different members — count-only
        verifier (the old one) would silently pass.
        Multiset compare catches it.
        """
        api = MagicMock()
        api.get_playlist_tracks.return_value = _tracks(
            ["u1", "u2", "u4"]  # u3 → u4
        )

        with pytest.raises(PlaylistVerificationError) as ex:
            JobExecutorService.verify_playlist_state(
                api, "p1",
                ["u1", "u2", "u3"], 42, "swap",
            )

        assert ex.value.missing == ["u3"]
        assert ex.value.extra == ["u4"]

    def test_duplicates_in_expected_must_match_actual(self):
        """Multiset semantics: 2 copies expected, 1 actual
        is a missing.
        """
        api = MagicMock()
        api.get_playlist_tracks.return_value = _tracks(
            ["u1", "u2"]
        )

        with pytest.raises(PlaylistVerificationError) as ex:
            JobExecutorService.verify_playlist_state(
                api, "p1",
                ["u1", "u1", "u2"], 42, "shuffle",
            )

        assert ex.value.missing == ["u1"]
        assert ex.value.extra == []

    def test_duplicates_in_actual_count_as_extra(self):
        """1 copy expected, 2 actual is an extra."""
        api = MagicMock()
        api.get_playlist_tracks.return_value = _tracks(
            ["u1", "u1", "u2"]
        )

        with pytest.raises(PlaylistVerificationError) as ex:
            JobExecutorService.verify_playlist_state(
                api, "p1",
                ["u1", "u2"], 42, "shuffle",
            )

        assert ex.value.missing == []
        assert ex.value.extra == ["u1"]

    def test_exception_message_contains_counts(self):
        api = MagicMock()
        api.get_playlist_tracks.return_value = _tracks(
            ["u1", "u2"]
        )

        with pytest.raises(PlaylistVerificationError) as ex:
            JobExecutorService.verify_playlist_state(
                api, "p1",
                ["u1", "u2", "u3"], 42, "swap",
            )

        msg = str(ex.value)
        assert "expected 3 tracks" in msg
        assert "got 2" in msg
        assert "missing 1" in msg
        assert "Schedule 42" in msg
        assert "swap" in msg


class TestVerifyPlaylistStateAPIResilience:
    def test_handles_none_returned_from_api(self):
        """Some Spotify cache miss paths can yield None;
        treat as empty.
        """
        api = MagicMock()
        api.get_playlist_tracks.return_value = None

        result = JobExecutorService.verify_playlist_state(
            api, "p1", [], 42, "test",
        )

        assert result == []

    def test_filters_tracks_without_uri(self):
        """Spotify can return placeholder items missing a
        URI (e.g. local tracks). Those don't count toward
        the actual set.
        """
        api = MagicMock()
        api.get_playlist_tracks.return_value = [
            {"uri": "u1", "name": "u1"},
            {"uri": None, "name": "local"},
            {"name": "broken"},
            {"uri": "u2", "name": "u2"},
        ]

        result = JobExecutorService.verify_playlist_state(
            api, "p1", ["u1", "u2"], 42, "test",
        )

        assert result == ["u1", "u2"]
