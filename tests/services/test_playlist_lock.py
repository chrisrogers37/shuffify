"""
Tests for shuffify.services.playlist_lock.

The lock module ships behavior on two paths:

1. PostgreSQL — real ``pg_try_advisory_lock`` / ``pg_advisory_unlock``
   calls on a dedicated pool connection. Validated here by mocking
   the dialect + the connection so we exercise the bigint key + the
   acquire/release pair without needing a live Postgres.
2. SQLite (dev/test) — no-op fast path; yields True without touching
   the database.

The key-derivation function is exercised directly: it must be
deterministic across processes and stay within bigint range.
"""

from unittest.mock import MagicMock, patch

import pytest

from shuffify.services.playlist_lock import (
    _playlist_lock_key,
    playlist_lock,
)


class TestPlaylistLockKey:
    """Key derivation must be deterministic and bigint-safe."""

    def test_deterministic(self):
        """Same input always produces the same key."""
        a = _playlist_lock_key("2RStAYJhTu2XEwSdL4edBl")
        b = _playlist_lock_key("2RStAYJhTu2XEwSdL4edBl")
        assert a == b

    def test_different_inputs_different_keys(self):
        """Different playlists derive different keys (sanity check)."""
        keys = {
            _playlist_lock_key(pid)
            for pid in [
                "2RStAYJhTu2XEwSdL4edBl",
                "6IaGaOajCMoxpCMZTCpcru",
                "37i9dQZF1DX186v583rmzp",
            ]
        }
        assert len(keys) == 3

    def test_within_signed_bigint_range(self):
        """Key must fit in Postgres bigint (signed 64-bit)."""
        for pid in [
            "2RStAYJhTu2XEwSdL4edBl",
            "x",
            "",
            "a" * 200,
        ]:
            k = _playlist_lock_key(pid)
            assert -(2**63) <= k < 2**63


class TestPlaylistLockSQLite:
    """On non-Postgres dialects the lock must be a fast no-op."""

    def test_yields_true_without_touching_db(self):
        """SQLite path: yield True, no SQL executed."""
        # The conftest fixtures wire up SQLite, so _is_postgres()
        # returns False and we go down the no-op branch.
        with patch("shuffify.services.playlist_lock.db") as fake_db:
            fake_db.engine.dialect.name = "sqlite"
            with playlist_lock("any_playlist") as acquired:
                assert acquired is True
            fake_db.engine.connect.assert_not_called()


class TestPlaylistLockPostgres:
    """Postgres path: dedicated connection + advisory lock SQL."""

    def _patched_db(self, advisory_lock_return):
        """Build a patched db whose engine yields a connection that
        returns ``advisory_lock_return`` (True/False) from
        ``pg_try_advisory_lock`` and accepts ``pg_advisory_unlock``.

        Returns (db_mock, conn_mock) so the test can inspect calls.
        """
        fake_db = MagicMock()
        fake_db.engine.dialect.name = "postgresql"
        conn = MagicMock()
        result = MagicMock()
        result.scalar.return_value = advisory_lock_return
        conn.execute.return_value = result
        fake_db.engine.connect.return_value = conn
        return fake_db, conn

    def test_acquires_and_releases_when_lock_available(self):
        """Happy path: try_lock returns True → caller sees acquired=True
        → unlock is called on the same connection."""
        fake_db, conn = self._patched_db(advisory_lock_return=True)
        with patch("shuffify.services.playlist_lock.db", fake_db):
            with playlist_lock("pid_1") as acquired:
                assert acquired is True

        # First call: pg_try_advisory_lock; second: pg_advisory_unlock
        calls = [c.args[0].text for c in conn.execute.call_args_list]
        assert any("pg_try_advisory_lock" in q for q in calls)
        assert any("pg_advisory_unlock" in q for q in calls)
        conn.close.assert_called_once()

    def test_times_out_when_lock_held(self):
        """If pg_try_advisory_lock keeps returning False, yield False
        after the timeout and do NOT call unlock."""
        fake_db, conn = self._patched_db(advisory_lock_return=False)
        with patch("shuffify.services.playlist_lock.db", fake_db):
            with patch("shuffify.services.playlist_lock.time.sleep"):
                with playlist_lock("pid_2", timeout_s=0.01) as acquired:
                    assert acquired is False

        calls = [c.args[0].text for c in conn.execute.call_args_list]
        assert any("pg_try_advisory_lock" in q for q in calls)
        assert not any("pg_advisory_unlock" in q for q in calls)
        conn.close.assert_called_once()

    def test_releases_lock_on_exception_inside_block(self):
        """If the wrapped block raises, the lock must still be
        released and the connection closed."""
        fake_db, conn = self._patched_db(advisory_lock_return=True)
        with patch("shuffify.services.playlist_lock.db", fake_db):
            with pytest.raises(RuntimeError, match="boom"):
                with playlist_lock("pid_3"):
                    raise RuntimeError("boom")

        calls = [c.args[0].text for c in conn.execute.call_args_list]
        assert any("pg_advisory_unlock" in q for q in calls)
        conn.close.assert_called_once()
