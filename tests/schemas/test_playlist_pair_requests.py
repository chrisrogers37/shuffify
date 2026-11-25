"""
Tests for playlist pair request validation schemas.

Tests CreatePairRequest, UpdatePairRequest,
and UnarchiveTracksRequest Pydantic models.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.playlist_pair_requests import (
    CreatePairRequest,
    UnarchiveTracksRequest,
    UpdatePairRequest,
)

# =============================================================================
# CreatePairRequest
# =============================================================================


class TestCreatePairRequestValid:
    """Tests for valid CreatePairRequest payloads."""

    def test_create_new_mode(self):
        req = CreatePairRequest(
            create_new=True,
            production_playlist_name="My Playlist",
        )
        assert req.create_new is True
        assert req.archive_playlist_id is None

    def test_existing_playlist_mode(self):
        req = CreatePairRequest(
            archive_playlist_id="abc123",
            archive_playlist_name="My Archive",
        )
        assert req.create_new is False
        assert req.archive_playlist_id == "abc123"

    def test_production_name_trimmed(self):
        req = CreatePairRequest(
            create_new=True,
            production_playlist_name="  Padded  ",
        )
        assert req.production_playlist_name == "Padded"


class TestCreatePairRequestInvalid:
    """Tests for invalid CreatePairRequest payloads."""

    def test_both_modes_raises(self):
        with pytest.raises(ValidationError):
            CreatePairRequest(
                create_new=True,
                archive_playlist_id="abc123",
                archive_playlist_name="Archive",
            )

    def test_neither_mode_raises(self):
        with pytest.raises(ValidationError):
            CreatePairRequest(
                create_new=False,
            )

    def test_existing_without_name_raises(self):
        with pytest.raises(ValidationError):
            CreatePairRequest(
                archive_playlist_id="abc123",
            )

    def test_empty_production_name_raises(self):
        with pytest.raises(ValidationError):
            CreatePairRequest(
                create_new=True,
                production_playlist_name="   ",
            )


# =============================================================================
# UnarchiveTracksRequest
# =============================================================================


class TestUnarchiveTracksRequestValid:
    """Tests for valid UnarchiveTracksRequest payloads."""

    def test_valid_uris(self):
        req = UnarchiveTracksRequest(track_uris=[
            "spotify:track:a1b2c3d4e5f6g7h8i9j0k1",
        ])
        assert len(req.track_uris) == 1

    def test_multiple_uris(self):
        req = UnarchiveTracksRequest(track_uris=[
            "spotify:track:a1b2c3d4e5f6g7h8i9j0k1",
            "spotify:track:z9y8x7w6v5u4t3s2r1q0p9",
        ])
        assert len(req.track_uris) == 2


class TestUnarchiveTracksRequestInvalid:
    """Tests for invalid UnarchiveTracksRequest payloads."""

    def test_empty_list_raises(self):
        with pytest.raises(ValidationError):
            UnarchiveTracksRequest(track_uris=[])

    def test_invalid_format_raises(self):
        with pytest.raises(ValidationError):
            UnarchiveTracksRequest(
                track_uris=["not:a:valid:uri"]
            )

    def test_short_track_id_raises(self):
        with pytest.raises(ValidationError):
            UnarchiveTracksRequest(
                track_uris=["spotify:track:short"]
            )


# =============================================================================
# UpdatePairRequest
# =============================================================================


class TestUpdatePairRequestValid:
    """Tests for valid UpdatePairRequest payloads."""

    def test_auto_archive_true(self):
        req = UpdatePairRequest(
            auto_archive_on_remove=True,
        )
        assert req.auto_archive_on_remove is True

    def test_auto_archive_false(self):
        req = UpdatePairRequest(
            auto_archive_on_remove=False,
        )
        assert req.auto_archive_on_remove is False


class TestUpdatePairRequestInvalid:
    """Tests for invalid UpdatePairRequest payloads."""

    def test_no_fields_raises(self):
        with pytest.raises(ValidationError):
            UpdatePairRequest()

    def test_none_field_raises(self):
        with pytest.raises(ValidationError):
            UpdatePairRequest(
                auto_archive_on_remove=None,
            )


class TestUnarchiveTracksRequestBound:
    """An unarchive selection holds at most one playlist's tracks."""

    def test_holds_a_full_playlist_but_not_one_track_more(self):
        # Spotify's limit for one playlist is 10,000 tracks.
        uris = ["spotify:track:" + "a" * 22] * 10_001
        assert len(UnarchiveTracksRequest(track_uris=uris[:10_000]).track_uris) == 10_000
        with pytest.raises(ValidationError) as refused:
            UnarchiveTracksRequest(track_uris=uris)
        assert [(error["loc"], error["type"]) for error in refused.value.errors()] == [(("track_uris",), "too_long")]
