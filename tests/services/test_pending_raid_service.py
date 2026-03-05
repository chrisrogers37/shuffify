"""
Tests for PendingRaidService.

Tests cover staging, listing, promoting, dismissing, and
cleanup of pending raid tracks.
"""

import pytest

from shuffify.models.db import db, PendingRaidTrack
from shuffify.enums import PendingRaidStatus
from shuffify.services.user_service import UserService
from shuffify.services.pending_raid_service import (
    PendingRaidService,
)


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


# =============================================================
# Stage Tracks
# =============================================================


class TestStage:
    """Tests for PendingRaidService.stage_tracks."""

    def test_stage_tracks_success(
        self, user, sample_tracks
    ):
        staged = PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
            source_name="My Source",
        )
        assert staged == 3

    def test_stage_deduplicates(
        self, user, sample_tracks
    ):
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
        # Stage again — all should be skipped
        staged = PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
        assert staged == 0

    def test_stage_skips_no_uri(self, user):
        staged = PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=[{"name": "No URI"}],
        )
        assert staged == 0

    def test_stage_joins_artist_list(
        self, user, sample_tracks
    ):
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
        t = PendingRaidTrack.query.filter_by(
            track_uri="spotify:track:t2"
        ).first()
        assert t.track_artists == "Artist B, Artist C"


# =============================================================
# List Pending
# =============================================================


class TestList:
    """Tests for PendingRaidService.list_pending."""

    def test_list_pending(self, user, sample_tracks):
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
        pending = PendingRaidService.list_pending(
            user.id, "pl1"
        )
        assert len(pending) == 3

    def test_list_excludes_promoted(
        self, user, sample_tracks
    ):
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
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
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
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
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
        promoted = PendingRaidService.promote_all(
            user.id, "pl1"
        )
        assert len(promoted) == 3
        for t in promoted:
            assert (
                t.status == PendingRaidStatus.PROMOTED
            )


# =============================================================
# Dismiss Tracks
# =============================================================


class TestDismiss:
    """Tests for dismiss_tracks and dismiss_all."""

    def test_dismiss_tracks(self, user, sample_tracks):
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
        track = PendingRaidTrack.query.first()
        count = PendingRaidService.dismiss_tracks(
            user.id, "pl1", [track.id]
        )
        assert count == 1

    def test_dismiss_all(self, user, sample_tracks):
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
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
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
        assert PendingRaidService.get_pending_count(
            user.id, "pl1"
        ) == 3

    def test_cleanup_resolved(
        self, user, sample_tracks
    ):
        PendingRaidService.stage_tracks(
            user_id=user.id,
            target_playlist_id="pl1",
            tracks=sample_tracks,
        )
        PendingRaidService.promote_all(user.id, "pl1")
        removed = PendingRaidService.cleanup_resolved(
            user.id, "pl1"
        )
        assert removed == 3
        assert PendingRaidTrack.query.count() == 0
