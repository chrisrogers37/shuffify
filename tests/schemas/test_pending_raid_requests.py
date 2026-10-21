"""
Tests for pending raid request schemas.

Tests cover Pydantic validation for dismiss requests.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.pending_raid_requests import DismissTracksRequest


class TestDismissTracksRequest:
    """Tests for DismissTracksRequest validation."""

    def test_valid_request(self):
        req = DismissTracksRequest(track_ids=[1, 2])
        assert req.track_ids == [1, 2]

    def test_empty_list_rejected(self):
        with pytest.raises(ValidationError):
            DismissTracksRequest(track_ids=[])

    def test_missing_field_rejected(self):
        with pytest.raises(ValidationError):
            DismissTracksRequest()

    def test_single_id(self):
        req = DismissTracksRequest(track_ids=[7])
        assert req.track_ids == [7]
