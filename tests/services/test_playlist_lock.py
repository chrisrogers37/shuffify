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
from sqlalchemy.exc import OperationalError

from shuffify.services.playlist_lock import (
    _PG_LOCK_NOT_AVAILABLE,
    _playlist_lock_key,
    playlist_lock,
)


def _operational_error(pgcode: str) -> OperationalError:
    """Build a SQLAlchemy OperationalError whose .orig.pgcode is set,
    matching how psycopg2 surfaces Postgres SQLSTATE codes."""
    orig = MagicMock()
    orig.pgcode = pgcode
    return OperationalError("stmt", {}, orig)


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

    def _patched_db(self, lock_outcome=None):
        """Build a patched db whose engine yields a connection on
        which `SET lock_timeout` succeeds and `pg_advisory_lock`
        either returns normally (acquired) or raises the given
        OperationalError (lock contention or unexpected error).

        ``lock_outcome`` is either ``None`` (lock acquired cleanly)
        or an exception to raise from the pg_advisory_lock call.
        """
        fake_db = MagicMock()
        fake_db.engine.dialect.name = "postgresql"
        conn = MagicMock()

        def _execute(stmt, params=None):
            sql = stmt.text if hasattr(stmt, "text") else str(stmt)
            if "pg_advisory_lock" in sql and lock_outcome is not None:
                raise lock_outcome
            return MagicMock()

        conn.execute.side_effect = _execute
        fake_db.engine.connect.return_value = conn
        return fake_db, conn

    def test_acquires_and_releases_when_lock_available(self):
        """Happy path: pg_advisory_lock returns → acquired=True →
        unlock is issued on the same connection."""
        fake_db, conn = self._patched_db(lock_outcome=None)
        with patch("shuffify.services.playlist_lock.db", fake_db):
            with playlist_lock("pid_1") as acquired:
                assert acquired is True

        calls = [
            (c.args[0].text if hasattr(c.args[0], "text") else str(c.args[0]))
            for c in conn.execute.call_args_list
        ]
        assert any("SET LOCAL lock_timeout" in q for q in calls)
        assert any(
            "pg_advisory_lock" in q and "pg_advisory_unlock" not in q for q in calls
        )
        assert any("pg_advisory_unlock" in q for q in calls)
        conn.close.assert_called_once()

    def test_times_out_when_lock_held(self):
        """When pg_advisory_lock raises lock_not_available (55P03)
        the context manager yields False and does NOT issue unlock."""
        fake_db, conn = self._patched_db(
            lock_outcome=_operational_error(_PG_LOCK_NOT_AVAILABLE)
        )
        with patch("shuffify.services.playlist_lock.db", fake_db):
            with playlist_lock("pid_2", timeout_s=0.01) as acquired:
                assert acquired is False

        calls = [
            (c.args[0].text if hasattr(c.args[0], "text") else str(c.args[0]))
            for c in conn.execute.call_args_list
        ]
        assert any("pg_advisory_lock" in q for q in calls)
        assert not any("pg_advisory_unlock" in q for q in calls)
        conn.close.assert_called_once()

    def test_propagates_unexpected_db_errors(self):
        """A non-55P03 OperationalError must propagate, not be
        swallowed as a timeout."""
        fake_db, conn = self._patched_db(
            lock_outcome=_operational_error("08006")  # connection failure
        )
        with patch("shuffify.services.playlist_lock.db", fake_db):
            with pytest.raises(OperationalError):
                with playlist_lock("pid_x"):
                    pytest.fail("body must not execute on unexpected error")
        conn.close.assert_called_once()

    def test_releases_lock_on_exception_inside_block(self):
        """If the wrapped block raises, the lock must still be
        released and the connection closed."""
        fake_db, conn = self._patched_db(lock_outcome=None)
        with patch("shuffify.services.playlist_lock.db", fake_db):
            with pytest.raises(RuntimeError, match="boom"):
                with playlist_lock("pid_3"):
                    raise RuntimeError("boom")

        calls = [
            (c.args[0].text if hasattr(c.args[0], "text") else str(c.args[0]))
            for c in conn.execute.call_args_list
        ]
        assert any("pg_advisory_unlock" in q for q in calls)
        conn.close.assert_called_once()
