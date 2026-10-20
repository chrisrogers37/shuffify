"""
Tests for PendingRaidService.

Tests cover listing pending raid tracks and promoting them
when a finalize is verified.
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
        PendingRaidService.complete_finalize([track])
        pending = PendingRaidService.list_pending(
            user.id, "pl1"
        )
        assert len(pending) == 2


# =============================================================
# Complete Finalize
# =============================================================


class TestCompleteFinalize:
    """Tests for PendingRaidService.complete_finalize."""

    def test_complete_finalize_promotes_pending_tracks(
        self, user, sample_tracks
    ):
        stage(user, "pl1", sample_tracks)
        PendingRaidService.complete_finalize(
            PendingRaidTrack.query.all()
        )
        tracks = PendingRaidTrack.query.all()
        assert len(tracks) == 3
        for t in tracks:
            assert t.status == PendingRaidStatus.PROMOTED
            assert t.resolved_at is not None
