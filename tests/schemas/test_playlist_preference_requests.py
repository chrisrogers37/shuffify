"""
Tests for playlist preference Pydantic schemas.

Covers valid requests, empty lists, oversized lists,
and invalid ID formats.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.playlist_preference_requests import (
    SaveOrderRequest,
)


class TestSaveOrderRequest:
    """Tests for SaveOrderRequest schema."""

    def test_valid_request(self):
        """Should accept valid playlist IDs."""
        req = SaveOrderRequest(
            playlist_ids=["abc123", "def456", "ghi789"]
        )
        assert len(req.playlist_ids) == 3

    def test_single_id(self):
        """Should accept a single playlist ID."""
        req = SaveOrderRequest(
            playlist_ids=["abc123"]
        )
        assert len(req.playlist_ids) == 1

    def test_empty_list_rejected(self):
        """Should reject empty playlist_ids list."""
        with pytest.raises(ValidationError) as exc:
            SaveOrderRequest(playlist_ids=[])
        assert "must not be empty" in str(exc.value)

    def test_over_500_rejected(self):
        """Should reject lists with more than 500 items."""
        ids = [f"id{i}" for i in range(501)]
        with pytest.raises(ValidationError) as exc:
            SaveOrderRequest(playlist_ids=ids)
        assert "cannot exceed 500" in str(exc.value)

    def test_exactly_500_accepted(self):
        """Should accept exactly 500 items."""
        ids = [f"id{i}" for i in range(500)]
        req = SaveOrderRequest(playlist_ids=ids)
        assert len(req.playlist_ids) == 500

    def test_invalid_format_rejected(self):
        """Should reject IDs with invalid characters."""
        with pytest.raises(ValidationError) as exc:
            SaveOrderRequest(
                playlist_ids=["valid", "in valid!"]
            )
        assert "Invalid playlist ID" in str(exc.value)

    def test_empty_string_rejected(self):
        """Should reject empty string as ID."""
        with pytest.raises(ValidationError) as exc:
            SaveOrderRequest(playlist_ids=[""])
        assert "Invalid playlist ID" in str(exc.value)
