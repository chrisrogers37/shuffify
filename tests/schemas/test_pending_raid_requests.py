"""
Tests for pending raid request schemas.

Tests cover Pydantic validation for promote and dismiss requests.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.pending_raid_requests import (
    PromoteTracksRequest,
    DismissTracksRequest,
)


class TestPromoteTracksRequest:
    """Tests for PromoteTracksRequest validation."""

    def test_valid_request(self):
        req = PromoteTracksRequest(track_ids=[1, 2, 3])
        assert req.track_ids == [1, 2, 3]

    def test_empty_list_rejected(self):
        with pytest.raises(ValidationError):
            PromoteTracksRequest(track_ids=[])

    def test_missing_field_rejected(self):
        with pytest.raises(ValidationError):
            PromoteTracksRequest()

    def test_non_list_rejected(self):
        with pytest.raises(ValidationError):
            PromoteTracksRequest(track_ids="not_a_list")

    def test_single_id(self):
        req = PromoteTracksRequest(track_ids=[42])
        assert req.track_ids == [42]


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
