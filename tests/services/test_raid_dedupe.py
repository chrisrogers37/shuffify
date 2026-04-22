"""
Tests for chain-wide raid deduplication.
"""

import pytest
from unittest.mock import MagicMock, patch

from shuffify.models.db import (
    db,
    PlaylistPair,
    RaidPlaylistLink,
    PendingRaidTrack,
)
from shuffify.services.user_service import UserService
from shuffify.services.raid_dedupe import (
    build_full_exclusion_set,
)
from shuffify.enums import PendingRaidStatus


@pytest.fixture
def user(db_app):
    """Provide a test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify(
            {
                "id": "dedupeuser1",
                "display_name": "Dedupe User",
                "images": [],
            }
        )
        yield result.user


@pytest.fixture
def mock_api():
    """Mock SpotifyAPI."""
    api = MagicMock()
    return api


class TestBuildFullExclusionSet:
    """Tests for build_full_exclusion_set."""

    def test_includes_target_tracks(self, user, mock_api):
        mock_api.get_playlist_tracks.return_value = [
            {"uri": "spotify:track:t1"},
            {"uri": "spotify:track:t2"},
        ]

        result, count = build_full_exclusion_set(mock_api, "target1", user.id)
        assert "spotify:track:t1" in result
        assert "spotify:track:t2" in result
        assert count == 2

    def test_includes_raid_playlist_tracks(self, user, mock_api):
        link = RaidPlaylistLink(
            user_id=user.id,
            target_playlist_id="target2",
            raid_playlist_id="raid2",
        )
        db.session.add(link)
        db.session.commit()

        def get_tracks(pid):
            if pid == "target2":
                return [{"uri": "spotify:track:t1"}]
            if pid == "raid2":
                return [{"uri": "spotify:track:r1"}]
            return []

        mock_api.get_playlist_tracks.side_effect = get_tracks

        result, _ = build_full_exclusion_set(mock_api, "target2", user.id)
        assert "spotify:track:t1" in result
        assert "spotify:track:r1" in result

    def test_includes_archive_tracks(self, user, mock_api):
        pair = PlaylistPair(
            user_id=user.id,
            production_playlist_id="target3",
            archive_playlist_id="archive3",
        )
        db.session.add(pair)
        db.session.commit()

        def get_tracks(pid):
            if pid == "target3":
                return [{"uri": "spotify:track:t1"}]
            if pid == "archive3":
                return [{"uri": "spotify:track:a1"}]
            return []

        mock_api.get_playlist_tracks.side_effect = get_tracks

        result, _ = build_full_exclusion_set(mock_api, "target3", user.id)
        assert "spotify:track:a1" in result

    def test_includes_dismissed_tracks(self, user, mock_api):
        pending = PendingRaidTrack(
            user_id=user.id,
            target_playlist_id="target4",
            track_uri="spotify:track:dismissed1",
            track_name="Dismissed Track",
            status=PendingRaidStatus.DISMISSED,
        )
        db.session.add(pending)
        db.session.commit()

        mock_api.get_playlist_tracks.return_value = []

        result, _ = build_full_exclusion_set(mock_api, "target4", user.id)
        assert "spotify:track:dismissed1" in result

    def test_full_chain_dedupe(self, user, mock_api):
        """Test all four sources combined."""
        # Setup raid link
        link = RaidPlaylistLink(
            user_id=user.id,
            target_playlist_id="target5",
            raid_playlist_id="raid5",
        )
        db.session.add(link)

        # Setup archive pair
        pair = PlaylistPair(
            user_id=user.id,
            production_playlist_id="target5",
            archive_playlist_id="archive5",
        )
        db.session.add(pair)

        # Setup dismissed track
        dismissed = PendingRaidTrack(
            user_id=user.id,
            target_playlist_id="target5",
            track_uri="spotify:track:d1",
            track_name="Dismissed",
            status=PendingRaidStatus.DISMISSED,
        )
        db.session.add(dismissed)
        db.session.commit()

        def get_tracks(pid):
            if pid == "target5":
                return [{"uri": "spotify:track:t1"}]
            if pid == "raid5":
                return [{"uri": "spotify:track:r1"}]
            if pid == "archive5":
                return [{"uri": "spotify:track:a1"}]
            return []

        mock_api.get_playlist_tracks.side_effect = get_tracks

        result, _ = build_full_exclusion_set(mock_api, "target5", user.id)
        assert "spotify:track:t1" in result
        assert "spotify:track:r1" in result
        assert "spotify:track:a1" in result
        assert "spotify:track:d1" in result
        assert len(result) == 4

    def test_handles_api_errors_gracefully(self, user, mock_api):
        """API errors should not crash, just return
        partial results."""
        mock_api.get_playlist_tracks.side_effect = Exception("API error")

        result, _ = build_full_exclusion_set(mock_api, "target_err", user.id)
        # Should return empty set, not raise
        assert isinstance(result, set)


class TestRollbackOnDbFailure:
    """Verify db.session.rollback() is called when DB queries fail."""

    def test_raid_link_query_failure_rolls_back(self, user, mock_api):
        mock_api.get_playlist_tracks.return_value = [{"uri": "spotify:track:t1"}]

        with (
            patch("shuffify.services.raid_dedupe.RaidPlaylistLink") as MockLink,
            patch("shuffify.services.raid_dedupe.db") as mock_db,
        ):
            MockLink.query.filter_by.side_effect = Exception("DB error")

            result, _ = build_full_exclusion_set(mock_api, "target_pl", user.id)

        mock_db.session.rollback.assert_called()
        assert "spotify:track:t1" in result

    def test_playlist_pair_query_failure_rolls_back(self, user, mock_api):
        mock_api.get_playlist_tracks.return_value = [{"uri": "spotify:track:t1"}]

        with (
            patch("shuffify.services.raid_dedupe.PlaylistPair") as MockPair,
            patch("shuffify.services.raid_dedupe.db") as mock_db,
        ):
            MockPair.query.filter_by.side_effect = Exception("DB error")

            result, _ = build_full_exclusion_set(mock_api, "target_pl", user.id)

        mock_db.session.rollback.assert_called()
        assert "spotify:track:t1" in result

    def test_pending_raid_query_failure_rolls_back(self, user, mock_api):
        mock_api.get_playlist_tracks.return_value = [{"uri": "spotify:track:t1"}]

        with (
            patch("shuffify.services.raid_dedupe.PendingRaidTrack") as MockPending,
            patch("shuffify.services.raid_dedupe.db") as mock_db,
        ):
            MockPending.query.filter_by.side_effect = Exception("DB error")

            result, _ = build_full_exclusion_set(mock_api, "target_pl", user.id)

        mock_db.session.rollback.assert_called()
        assert "spotify:track:t1" in result

    def test_all_db_queries_fail_rolls_back_each(self, user, mock_api):
        """All three DB failures should each trigger a rollback."""
        mock_api.get_playlist_tracks.return_value = [{"uri": "spotify:track:t1"}]

        with (
            patch("shuffify.services.raid_dedupe.RaidPlaylistLink") as MockLink,
            patch("shuffify.services.raid_dedupe.PlaylistPair") as MockPair,
            patch("shuffify.services.raid_dedupe.PendingRaidTrack") as MockPending,
            patch("shuffify.services.raid_dedupe.db") as mock_db,
        ):
            MockLink.query.filter_by.side_effect = Exception("fail")
            MockPair.query.filter_by.side_effect = Exception("fail")
            MockPending.query.filter_by.side_effect = Exception("fail")

            result, count = build_full_exclusion_set(mock_api, "target_pl", user.id)

        assert mock_db.session.rollback.call_count == 3
        assert result == {"spotify:track:t1"}
        assert count == 1
