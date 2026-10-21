"""
Tests for PendingRaidService.

Tests cover listing, promoting, dismissing, and
cleanup of pending raid tracks.
"""

import pytest

from shuffify.enums import PendingRaidStatus
from shuffify.models.db import PendingRaidTrack, db
from shuffify.services.operations.executors.collect_storage import stage_pending
from shuffify.services.pending_raid_service import (
    PendingRaidService,
)
from shuffify.services.user_service import UserService


@pytest.fixture
def user(db_app):
    """Provide a test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield result.user


@pytest.fixture
def sample_tracks():
    """Sample track dicts for staging."""
    return [
        {
            "uri": "spotify:track:t1",
            "name": "Track One",
            "artists": ["Artist A"],
            "album_name": "Album 1",
            "album_image_url": "https://img/1.jpg",
            "duration_ms": 200000,
        },
        {
            "uri": "spotify:track:t2",
            "name": "Track Two",
            "artists": ["Artist B", "Artist C"],
            "album_name": "Album 2",
            "album_image_url": "https://img/2.jpg",
            "duration_ms": 180000,
        },
        {
            "uri": "spotify:track:t3",
            "name": "Track Three",
            "artists": ["Artist A"],
            "album_name": "Album 3",
        },
    ]


def stage(user, target_playlist_id, tracks):
    """Stage ``tracks`` as the Collect executor does, and commit them."""
    stage_pending(db.session, user.id, target_playlist_id, tracks)
    db.session.commit()


# =============================================================
# List Pending
# =============================================================


class TestList:
    """Tests for PendingRaidService.list_pending."""

    def test_list_pending(self, user, sample_tracks):
        stage(user, "pl1", sample_tracks)
        pending = PendingRaidService.list_pending(
            user.id, "pl1"
        )
        assert len(pending) == 3

    def test_list_excludes_promoted(
        self, user, sample_tracks
    ):
        stage(user, "pl1", sample_tracks)
        # Promote one
        track = PendingRaidTrack.query.first()
        PendingRaidService.promote_tracks(
            user.id, "pl1", [track.id]
        )
        pending = PendingRaidService.list_pending(
            user.id, "pl1"
        )
        assert len(pending) == 2


# =============================================================
# Promote Tracks
# =============================================================


class TestPromote:
    """Tests for promote_tracks and promote_all."""

    def test_promote_tracks(self, user, sample_tracks):
        stage(user, "pl1", sample_tracks)
        all_tracks = PendingRaidTrack.query.all()
        promoted = PendingRaidService.promote_tracks(
            user.id, "pl1", [all_tracks[0].id]
        )
        assert len(promoted) == 1
        assert (
            promoted[0].status
            == PendingRaidStatus.PROMOTED
        )
        assert promoted[0].resolved_at is not None

    def test_promote_all(self, user, sample_tracks):
        stage(user, "pl1", sample_tracks)
        promoted = PendingRaidService.promote_all(
            user.id, "pl1"
        )
        assert len(promoted) == 3
        for t in promoted:
            assert (
                t.status == PendingRaidStatus.PROMOTED
            )


# =============================================================
# Unpromote Tracks
# =============================================================


class TestUnpromote:
    """Tests for PendingRaidService.unpromote_tracks."""

    def test_unpromote_reverts_to_pending(
        self, user, sample_tracks
    ):
        stage(user, "pl1", sample_tracks)
        PendingRaidService.promote_all(user.id, "pl1")
        uris = [t["uri"] for t in sample_tracks]

        count = PendingRaidService.unpromote_tracks(
            user.id, "pl1", uris
        )
        assert count == 3

        pending = PendingRaidService.list_pending(
            user.id, "pl1"
        )
        assert len(pending) == 3
        for t in pending:
            assert (
                t.status == PendingRaidStatus.PENDING
            )
            assert t.resolved_at is None

    def test_unpromote_ignores_non_promoted(
        self, user, sample_tracks
    ):
        stage(user, "pl1", sample_tracks)
        # Don't promote — they're still PENDING
        uris = [t["uri"] for t in sample_tracks]

        count = PendingRaidService.unpromote_tracks(
            user.id, "pl1", uris
        )
        assert count == 0

    def test_unpromote_no_match_returns_zero(
        self, user
    ):
        count = PendingRaidService.unpromote_tracks(
            user.id,
            "pl1",
            ["spotify:track:nonexistent"],
        )
        assert count == 0


# =============================================================
# Dismiss Tracks
# =============================================================


class TestDismiss:
    """Tests for dismiss_tracks and dismiss_all."""

    def test_dismiss_tracks(self, user, sample_tracks):
        stage(user, "pl1", sample_tracks)
        track = PendingRaidTrack.query.first()
        count = PendingRaidService.dismiss_tracks(
            user.id, "pl1", [track.id]
        )
        assert count == 1

    def test_dismiss_all(self, user, sample_tracks):
        stage(user, "pl1", sample_tracks)
        count = PendingRaidService.dismiss_all(
            user.id, "pl1"
        )
        assert count == 3


# =============================================================
# Counts & Cleanup
# =============================================================


class TestCountsAndCleanup:
    """Tests for get_pending_count and cleanup_resolved."""

    def test_pending_count(self, user, sample_tracks):
        stage(user, "pl1", sample_tracks)
        assert PendingRaidService.get_pending_count(
            user.id, "pl1"
        ) == 3

    def test_cleanup_resolved(
        self, user, sample_tracks
    ):
        stage(user, "pl1", sample_tracks)
        PendingRaidService.promote_all(user.id, "pl1")
        removed = PendingRaidService.cleanup_resolved(
            user.id, "pl1"
        )
        assert removed == 3
        assert PendingRaidTrack.query.count() == 0
