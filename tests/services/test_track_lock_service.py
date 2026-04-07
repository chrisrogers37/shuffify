"""
Tests for TrackLockService.

Covers toggle_lock, set_lock, unlock, bulk_unlock,
get_locks_for_playlist, get_locked_positions,
get_locked_uris, is_locked, cleanup_expired,
and update_positions_after_reorder.
"""

import pytest
from datetime import datetime, timedelta, timezone

from shuffify.enums import LockTier
from shuffify.models.db import db, User, TrackLock
from shuffify.services.track_lock_service import (
    TrackLockService,
    TrackLockError,
    STANDARD_EXPIRY_DAYS,
)


@pytest.fixture
def test_user(app_ctx):
    """Create a test user."""
    user = User(
        spotify_id="lock_svc_user",
        display_name="Lock User",
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def other_user(app_ctx):
    """Create another test user."""
    user = User(
        spotify_id="other_lock_user",
        display_name="Other User",
    )
    db.session.add(user)
    db.session.commit()
    return user


PLAYLIST_ID = "playlist_abc"
TRACK_A = "spotify:track:trackA"
TRACK_B = "spotify:track:trackB"
TRACK_C = "spotify:track:trackC"


class TestToggleLock:
    """Tests for toggle_lock (cycle: unlocked -> standard -> super -> unlocked)."""

    def test_first_toggle_creates_standard_lock(
        self, app_ctx, test_user
    ):
        result = TrackLockService.toggle_lock(
            test_user.id, PLAYLIST_ID, TRACK_A, 0
        )
        assert result is not None
        assert result["lock_tier"] == LockTier.STANDARD
        assert result["position"] == 0
        assert result["expires_at"] is not None

    def test_second_toggle_upgrades_to_super(
        self, app_ctx, test_user
    ):
        TrackLockService.toggle_lock(
            test_user.id, PLAYLIST_ID, TRACK_A, 0
        )
        result = TrackLockService.toggle_lock(
            test_user.id, PLAYLIST_ID, TRACK_A, 0
        )
        assert result is not None
        assert result["lock_tier"] == LockTier.SUPER
        assert result["expires_at"] is None

    def test_third_toggle_unlocks(
        self, app_ctx, test_user
    ):
        TrackLockService.toggle_lock(
            test_user.id, PLAYLIST_ID, TRACK_A, 0
        )
        TrackLockService.toggle_lock(
            test_user.id, PLAYLIST_ID, TRACK_A, 0
        )
        result = TrackLockService.toggle_lock(
            test_user.id, PLAYLIST_ID, TRACK_A, 0
        )
        assert result is None

        assert not TrackLockService.is_locked(
            test_user.id, PLAYLIST_ID, TRACK_A
        )

    def test_toggle_expired_lock_creates_new_standard(
        self, app_ctx, test_user
    ):
        """Toggling an expired lock should start fresh."""
        lock = TrackLock(
            user_id=test_user.id,
            spotify_playlist_id=PLAYLIST_ID,
            track_uri=TRACK_A,
            position=0,
            lock_tier=LockTier.STANDARD,
            expires_at=datetime.now(timezone.utc)
            - timedelta(days=1),
        )
        db.session.add(lock)
        db.session.commit()

        result = TrackLockService.toggle_lock(
            test_user.id, PLAYLIST_ID, TRACK_A, 0
        )
        assert result is not None
        assert result["lock_tier"] == LockTier.STANDARD


class TestSetLock:
    """Tests for set_lock."""

    def test_set_standard_lock(self, app_ctx, test_user):
        result = TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        assert result["lock_tier"] == LockTier.STANDARD
        assert result["expires_at"] is not None

    def test_set_super_lock(self, app_ctx, test_user):
        result = TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.SUPER,
        )
        assert result["lock_tier"] == LockTier.SUPER
        assert result["expires_at"] is None

    def test_update_existing_lock(self, app_ctx, test_user):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        result = TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.SUPER,
        )
        assert result["lock_tier"] == LockTier.SUPER

        locks = TrackLockService.get_locks_for_playlist(
            test_user.id, PLAYLIST_ID
        )
        assert len(locks) == 1


class TestUnlock:
    """Tests for unlock and bulk_unlock."""

    def test_unlock_existing(self, app_ctx, test_user):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        removed = TrackLockService.unlock(
            test_user.id, PLAYLIST_ID, TRACK_A
        )
        assert removed is True
        assert not TrackLockService.is_locked(
            test_user.id, PLAYLIST_ID, TRACK_A
        )

    def test_unlock_nonexistent_returns_false(
        self, app_ctx, test_user
    ):
        removed = TrackLockService.unlock(
            test_user.id, PLAYLIST_ID, TRACK_A
        )
        assert removed is False

    def test_bulk_unlock_all(self, app_ctx, test_user):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_B,
            1, LockTier.SUPER,
        )
        count = TrackLockService.bulk_unlock(
            test_user.id, PLAYLIST_ID
        )
        assert count == 2

    def test_bulk_unlock_specific(self, app_ctx, test_user):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_B,
            1, LockTier.SUPER,
        )
        count = TrackLockService.bulk_unlock(
            test_user.id, PLAYLIST_ID, [TRACK_A]
        )
        assert count == 1
        assert TrackLockService.is_locked(
            test_user.id, PLAYLIST_ID, TRACK_B
        )


class TestGetLocks:
    """Tests for query methods."""

    def test_get_locks_excludes_expired(
        self, app_ctx, test_user
    ):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.SUPER,
        )
        # Create an expired lock directly
        expired_lock = TrackLock(
            user_id=test_user.id,
            spotify_playlist_id=PLAYLIST_ID,
            track_uri=TRACK_B,
            position=1,
            lock_tier=LockTier.STANDARD,
            expires_at=datetime.now(timezone.utc)
            - timedelta(days=1),
        )
        db.session.add(expired_lock)
        db.session.commit()

        locks = TrackLockService.get_locks_for_playlist(
            test_user.id, PLAYLIST_ID
        )
        assert len(locks) == 1
        assert locks[0].track_uri == TRACK_A

    def test_get_locked_positions(
        self, app_ctx, test_user
    ):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_B,
            3, LockTier.SUPER,
        )
        positions = TrackLockService.get_locked_positions(
            test_user.id, PLAYLIST_ID
        )
        assert positions == {0: TRACK_A, 3: TRACK_B}

    def test_get_locked_uris(self, app_ctx, test_user):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_B,
            1, LockTier.SUPER,
        )
        uris = TrackLockService.get_locked_uris(
            test_user.id, PLAYLIST_ID
        )
        assert uris == {TRACK_A, TRACK_B}

    def test_is_locked(self, app_ctx, test_user):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        assert TrackLockService.is_locked(
            test_user.id, PLAYLIST_ID, TRACK_A
        )
        assert not TrackLockService.is_locked(
            test_user.id, PLAYLIST_ID, TRACK_B
        )

    def test_locks_isolated_between_users(
        self, app_ctx, test_user, other_user
    ):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        assert not TrackLockService.is_locked(
            other_user.id, PLAYLIST_ID, TRACK_A
        )


class TestCleanupExpired:
    """Tests for cleanup_expired."""

    def test_deletes_expired_standard_locks(
        self, app_ctx, test_user
    ):
        # Active standard lock
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        # Expired standard lock
        expired = TrackLock(
            user_id=test_user.id,
            spotify_playlist_id=PLAYLIST_ID,
            track_uri=TRACK_B,
            position=1,
            lock_tier=LockTier.STANDARD,
            expires_at=datetime.now(timezone.utc)
            - timedelta(days=1),
        )
        db.session.add(expired)
        db.session.commit()

        count = TrackLockService.cleanup_expired()
        assert count == 1
        assert TrackLockService.is_locked(
            test_user.id, PLAYLIST_ID, TRACK_A
        )

    def test_super_locks_not_deleted(
        self, app_ctx, test_user
    ):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.SUPER,
        )
        count = TrackLockService.cleanup_expired()
        assert count == 0


class TestUpdatePositions:
    """Tests for update_positions_after_reorder."""

    def test_updates_positions(self, app_ctx, test_user):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_B,
            1, LockTier.SUPER,
        )

        # Reverse order
        new_order = [TRACK_B, TRACK_A]
        updated = (
            TrackLockService.update_positions_after_reorder(
                test_user.id, PLAYLIST_ID, new_order
            )
        )
        assert updated == 2

        positions = TrackLockService.get_locked_positions(
            test_user.id, PLAYLIST_ID
        )
        assert positions == {0: TRACK_B, 1: TRACK_A}

    def test_deletes_orphaned_locks(
        self, app_ctx, test_user
    ):
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_A,
            0, LockTier.STANDARD,
        )
        TrackLockService.set_lock(
            test_user.id, PLAYLIST_ID, TRACK_B,
            1, LockTier.SUPER,
        )

        # Track B removed from playlist
        new_order = [TRACK_A]
        TrackLockService.update_positions_after_reorder(
            test_user.id, PLAYLIST_ID, new_order
        )

        assert TrackLockService.is_locked(
            test_user.id, PLAYLIST_ID, TRACK_A
        )
        assert not TrackLockService.is_locked(
            test_user.id, PLAYLIST_ID, TRACK_B
        )

    def test_no_locks_returns_zero(
        self, app_ctx, test_user
    ):
        updated = (
            TrackLockService.update_positions_after_reorder(
                test_user.id, PLAYLIST_ID,
                [TRACK_A, TRACK_B],
            )
        )
        assert updated == 0
