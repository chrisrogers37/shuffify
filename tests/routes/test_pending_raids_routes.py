"""
Tests for pending raid routes.

Tests cover authentication, validation, and basic success paths
for the pending raid track inbox endpoints.
"""

from unittest.mock import MagicMock, patch

import pytest

from shuffify.services.workshop_finalize_service import WorkshopFinalizeResult
from tests.paths import api_path

# =============================================================
# Authentication Tests
# =============================================================


class TestPendingRaidsAuth:
    """All pending raid endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_list_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(
                api_path("/playlist/p1/pending-raids")
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_dismiss_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pending-raids/dismiss"),
                json={"track_ids": [1]},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_dismiss_all_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pending-raids/dismiss-all")
            )
            assert resp.status_code == 401


# =============================================================
# Validation Tests
# =============================================================


class TestPendingRaidsValidation:
    """Request validation tests."""

    @patch("shuffify.routes.require_auth")
    def test_dismiss_missing_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/pending-raids/dismiss"),
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_dismiss_empty_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/pending-raids/dismiss"),
            json={"track_ids": []},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_finalize_missing_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/pending-raids/finalize"),
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_finalize_empty_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/pending-raids/finalize"),
            json={"track_ids": []},
        )
        assert resp.status_code == 400


# =============================================================
# Finalize Endpoint Tests
# =============================================================


class TestPendingRaidsFinalize:
    @pytest.mark.parametrize("endpoint", ["finalize"])
    @patch("shuffify.routes.require_auth")
    def test_unauth(self, mock_auth, db_app, endpoint):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            response = client.post(api_path(f"/playlist/p1/pending-raids/{endpoint}"))
        assert response.status_code == 401
        assert response.json["status"] == "failed"
        assert response.json["promoted_count"] == 0

    @pytest.mark.parametrize("endpoint", ["finalize"])
    @pytest.mark.parametrize("status,http_status,retryable", [
        ("complete", 200, False), ("failed", 409, False),
        ("failed", 502, True), ("unknown", 502, False),
    ])
    @patch("shuffify.routes.raid_panel.WorkshopFinalizeService")
    @patch("shuffify.routes.require_auth")
    def test_captured_request_and_exact_result_forwarded(
        self, auth, service, auth_client, endpoint, status, http_status, retryable,
    ):
        api = auth.return_value
        payload = {
            "track_ids": [12, 24], "source_playlist_id": "raid123",
            "target_track_uris": ["spotify:track:" + "a" * 22],
            "inspect_only": True,
        }
        result = WorkshopFinalizeResult(
            "raid123", "Collection", status, "Result", http_status=http_status,
            retryable=retryable, extra={"promoted_count": 2 if status == "complete" else 0},
        )
        service.return_value.finalize_raids.return_value = result
        with patch("shuffify.routes.raid_panel.log_activity") as activity:
            response = auth_client.post(api_path(f"/playlist/p1/pending-raids/{endpoint}"), json=payload)
        assert response.status_code == http_status
        expected = result.to_dict()
        if not expected["success"]:
            expected["category"] = "error"
        assert response.json == expected
        service.assert_called_once_with(api)
        call = service.return_value.finalize_raids.call_args
        assert call.args[0] == "p1"
        assert call.kwargs == payload
        activity.assert_not_called()
        api.playlist_add_items.assert_not_called()
        api.playlist_remove_items.assert_not_called()

    @pytest.mark.parametrize("endpoint", ["finalize"])
    @pytest.mark.parametrize("payload", [None, {}, [], {"track_ids": [1]}, {
        "source_playlist_id": None, "target_track_uris": [],
    }])
    @patch("shuffify.routes.raid_panel.WorkshopFinalizeService")
    @patch("shuffify.routes.require_auth")
    def test_incomplete_captured_task_rejected(self, auth, service, auth_client, endpoint, payload):
        response = auth_client.post(api_path(f"/playlist/p1/pending-raids/{endpoint}"), json=payload)
        assert response.status_code == 400
        assert response.json["status"] == "failed"
        assert response.json["promoted_count"] == 0
        assert response.json["retryable"] is False
        service.assert_not_called()
