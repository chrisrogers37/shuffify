"""
Unit tests for PlaylistVerificationError.

Covers:
  - Count drift reports structured missing/extra
  - URI substitution (same count, wrong members) reports both sides
  - Duplicates honored -- multiset semantics, not set
  - Order alone is not a divergence
  - Exception attributes carry playlist_id / phase / schedule_id
  - The message carries the expected, actual, missing and extra counts
"""

from shuffify.services.executors import PlaylistVerificationError


def _error(expected, actual, *, playlist_id="p1", schedule_id=42, phase="swap"):
    return PlaylistVerificationError(playlist_id, expected, actual, schedule_id, phase)


class TestPlaylistVerificationErrorDivergence:
    def test_count_drift_reports_missing(self):
        error = _error(["u1", "u2", "u3"], ["u1", "u2"])

        assert error.missing == ["u3"]
        assert error.extra == []
        assert error.playlist_id == "p1"
        assert error.phase == "swap"
        assert error.schedule_id == 42

    def test_count_drift_reports_extra(self):
        error = _error(["u1", "u2"], ["u1", "u2", "u3", "u4"])

        assert sorted(error.extra) == ["u3", "u4"]
        assert error.missing == []

    def test_uri_substitution_reports_both_sides(self):
        """Same count, different members -- a count-only verifier would
        silently pass. Multiset compare catches it.
        """
        error = _error(["u1", "u2", "u3"], ["u1", "u2", "u4"])  # u3 -> u4

        assert error.missing == ["u3"]
        assert error.extra == ["u4"]

    def test_duplicates_in_expected_must_match_actual(self):
        """Multiset semantics: 2 copies expected, 1 actual is a missing."""
        error = _error(["u1", "u1", "u2"], ["u1", "u2"], phase="shuffle")

        assert error.missing == ["u1"]
        assert error.extra == []

    def test_duplicates_in_actual_count_as_extra(self):
        """1 copy expected, 2 actual is an extra."""
        error = _error(["u1", "u2"], ["u1", "u1", "u2"], phase="shuffle")

        assert error.missing == []
        assert error.extra == ["u1"]

    def test_order_alone_reports_nothing_missing_or_extra(self):
        """Multiset compare ignores order."""
        error = _error(["u1", "u2", "u3"], ["u3", "u1", "u2"])

        assert error.missing == []
        assert error.extra == []

    def test_exception_message_contains_counts(self):
        message = str(_error(["u1", "u2", "u3"], ["u1", "u2"]))

        assert "expected 3 tracks" in message
        assert "got 2" in message
        assert "missing 1" in message
        assert "extra 0" in message
        assert "Schedule 42" in message
        assert "swap" in message
