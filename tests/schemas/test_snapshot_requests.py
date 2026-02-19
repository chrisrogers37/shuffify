"""
Tests for playlist snapshot request validation schemas.

Tests ManualSnapshotRequest Pydantic model for snapshot
creation payload validation.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.snapshot_requests import (
    ManualSnapshotRequest,
)


# =============================================================================
# Helpers
# =============================================================================


def _base_snapshot_kwargs(**overrides):
    """Return minimal valid kwargs for ManualSnapshotRequest."""
    defaults = {
        "playlist_name": "My Playlist",
        "track_uris": [
            "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
            "spotify:track:1301WleyT98MSxVHPZCA6M",
        ],
    }
    defaults.update(overrides)
    return defaults


# =============================================================================
# ManualSnapshotRequest — Valid inputs
# =============================================================================


class TestManualSnapshotRequestValid:
    """Tests for valid ManualSnapshotRequest payloads."""

    def test_valid_minimal_request(self):
        """Should accept request with required fields only."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs()
        )
        assert req.playlist_name == "My Playlist"
        assert len(req.track_uris) == 2
        assert req.trigger_description is None

    def test_valid_with_trigger_description(self):
        """Should accept request with optional description."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(
                trigger_description="Before shuffle"
            )
        )
        assert (
            req.trigger_description == "Before shuffle"
        )

    def test_single_track_uri(self):
        """Should accept a single track URI."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(
                track_uris=[
                    "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
                ],
            )
        )
        assert len(req.track_uris) == 1

    def test_empty_track_list(self):
        """Should accept an empty track list."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(track_uris=[])
        )
        assert len(req.track_uris) == 0

    def test_many_track_uris(self):
        """Should accept a large number of track URIs."""
        uris = [
            f"spotify:track:track{i:022d}"
            for i in range(500)
        ]
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(track_uris=uris)
        )
        assert len(req.track_uris) == 500

    def test_trigger_description_max_length(self):
        """Should accept description at max length."""
        desc = "x" * 500
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(
                trigger_description=desc
            )
        )
        assert len(req.trigger_description) == 500

    def test_extra_fields_ignored(self):
        """Should ignore unknown fields."""
        req = ManualSnapshotRequest(
            **_base_snapshot_kwargs(
                unknown_field="ignored"
            )
        )
        assert req.playlist_name == "My Playlist"
        assert not hasattr(req, "unknown_field")


# =============================================================================
# ManualSnapshotRequest — Invalid inputs
# =============================================================================


class TestManualSnapshotRequestInvalid:
    """Tests for invalid ManualSnapshotRequest payloads."""

    def test_missing_playlist_name(self):
        """Should reject request without playlist_name."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                track_uris=[
                    "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
                ],
            )

    def test_empty_playlist_name(self):
        """Should reject empty string playlist_name."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(playlist_name="")
            )

    def test_playlist_name_too_long(self):
        """Should reject playlist_name exceeding 255 chars."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    playlist_name="x" * 256
                )
            )

    def test_missing_track_uris(self):
        """Should reject request without track_uris."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                playlist_name="My Playlist",
            )

    def test_invalid_track_uri_format(self):
        """Should reject non-Spotify track URIs."""
        with pytest.raises(ValidationError) as exc_info:
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    track_uris=["not_a_valid_uri"]
                )
            )
        assert "Invalid track URI format" in str(
            exc_info.value
        )

    def test_invalid_uri_spotify_album(self):
        """Should reject Spotify album URIs."""
        with pytest.raises(ValidationError) as exc_info:
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    track_uris=[
                        "spotify:album:4iV5W9uYEdYUVa79Axb7Rh"
                    ]
                )
            )
        assert "Invalid track URI format" in str(
            exc_info.value
        )

    def test_invalid_uri_spotify_playlist(self):
        """Should reject Spotify playlist URIs."""
        with pytest.raises(ValidationError) as exc_info:
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    track_uris=[
                        "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
                    ]
                )
            )
        assert "Invalid track URI format" in str(
            exc_info.value
        )

    def test_mixed_valid_and_invalid_uris(self):
        """Should reject if any URI is invalid."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    track_uris=[
                        "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
                        "bad_uri",
                    ]
                )
            )

    def test_trigger_description_too_long(self):
        """Should reject description exceeding 500 chars."""
        with pytest.raises(ValidationError):
            ManualSnapshotRequest(
                **_base_snapshot_kwargs(
                    trigger_description="x" * 501
                )
            )
