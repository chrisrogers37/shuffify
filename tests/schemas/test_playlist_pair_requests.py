"""
Tests for playlist pair request validation schemas.

Tests CreatePairRequest, ArchiveTracksRequest, and
UnarchiveTracksRequest Pydantic models.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.playlist_pair_requests import (
    CreatePairRequest,
    ArchiveTracksRequest,
    UnarchiveTracksRequest,
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
# ArchiveTracksRequest
# =============================================================================


class TestArchiveTracksRequestValid:
    """Tests for valid ArchiveTracksRequest payloads."""

    def test_valid_uris(self):
        req = ArchiveTracksRequest(track_uris=[
            "spotify:track:a1b2c3d4e5f6g7h8i9j0k1",
            "spotify:track:z9y8x7w6v5u4t3s2r1q0p9",
        ])
        assert len(req.track_uris) == 2

    def test_single_uri(self):
        req = ArchiveTracksRequest(track_uris=[
            "spotify:track:a1b2c3d4e5f6g7h8i9j0k1",
        ])
        assert len(req.track_uris) == 1


class TestArchiveTracksRequestInvalid:
    """Tests for invalid ArchiveTracksRequest payloads."""

    def test_empty_list_raises(self):
        with pytest.raises(ValidationError):
            ArchiveTracksRequest(track_uris=[])

    def test_invalid_uri_format_raises(self):
        with pytest.raises(ValidationError):
            ArchiveTracksRequest(track_uris=["bad_uri"])

    def test_short_track_id_raises(self):
        with pytest.raises(ValidationError):
            ArchiveTracksRequest(
                track_uris=["spotify:track:short"]
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
