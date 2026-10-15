"""
Tests for playlist pair routes.

Tests cover authentication, error handling, and basic CRUD
for the /playlist/<id>/pair endpoints.
"""

from unittest.mock import MagicMock, patch

import pytest

from shuffify.services.workshop_finalize_service import WorkshopFinalizeResult
from tests.paths import api_path

# =============================================================================
# Authentication Tests
# =============================================================================


class TestPairAuthRequired:
    """All pair endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_get_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(api_path("/playlist/p1/pair"))
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_create_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pair"),
                json={"create_new": True},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_delete_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete(api_path("/playlist/p1/pair"))
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_archive_tracks_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pair/archive"),
                json={"track_uris": ["spotify:track:x"]},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_unarchive_tracks_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pair/unarchive"),
                json={"track_uris": ["spotify:track:x"]},
            )
            assert resp.status_code == 401


# =============================================================================
# GET /playlist/<id>/pair
# =============================================================================


class TestGetPair:
    """Tests for GET /playlist/<id>/pair."""

    @patch("shuffify.routes.require_auth")
    def test_no_pair_returns_paired_false(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get(api_path("/playlist/p1/pair"))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["paired"] is False


# =============================================================================
# POST /playlist/<id>/pair
# =============================================================================


class TestCreatePairRoute:
    """Tests for POST /playlist/<id>/pair."""

    @patch("shuffify.routes.require_auth")
    def test_invalid_body_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/pair"),
            json={"create_new": False},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_no_json_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/pair"),
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_unverified_creation_is_reported_unknown_and_pairs_nothing(
        self, mock_auth, auth_client
    ):
        """A lost answer from Spotify leaves the archive playlist's existence unknown."""
        from shuffify.models.db import PlaylistPair
        from shuffify.services.playlist_setup_service import CREATION_UNKNOWN
        from shuffify.spotify.api import SpotifyAPI
        from shuffify.spotify.exceptions import SpotifyWriteUnverifiedError

        api = MagicMock(spec=SpotifyAPI)
        api.create_user_playlist.side_effect = SpotifyWriteUnverifiedError("timed out")
        mock_auth.return_value = api

        resp = auth_client.post(
            api_path("/playlist/p1/pair"),
            json={"create_new": True, "production_playlist_name": "Mix"},
        )

        assert resp.status_code == 502
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "creation_unknown"
        assert data["outcome"] == "unknown"
        assert data["message"] == CREATION_UNKNOWN
        api.create_user_playlist.assert_called_once()
        assert PlaylistPair.query.count() == 0

    @patch("shuffify.routes.require_auth")
    def test_creation_answered_without_an_id_is_reported_unknown_and_pairs_nothing(
        self, mock_auth, auth_client
    ):
        """An empty 2xx from Spotify may still have created the archive playlist."""
        from shuffify.models.db import PlaylistPair
        from shuffify.services.playlist_setup_service import CREATION_UNKNOWN
        from shuffify.spotify.api import SpotifyAPI

        api = MagicMock(spec=SpotifyAPI)
        api.create_user_playlist.return_value = None
        mock_auth.return_value = api

        resp = auth_client.post(
            api_path("/playlist/p1/pair"),
            json={"create_new": True, "production_playlist_name": "Mix"},
        )

        assert resp.status_code == 502
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "creation_unknown"
        assert data["outcome"] == "unknown"
        assert data["message"] == CREATION_UNKNOWN
        api.create_user_playlist.assert_called_once()
        api.update_playlist_details.assert_not_called()
        assert PlaylistPair.query.count() == 0


# =============================================================================
# PATCH /playlist/<id>/pair
# =============================================================================


class TestUpdatePairAuth:
    """Authentication tests for PATCH pair endpoint."""

    @patch("shuffify.routes.require_auth")
    def test_update_pair_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.patch(
                api_path("/playlist/p1/pair"),
                json={"auto_archive_on_remove": True},
            )
            assert resp.status_code == 401


class TestUpdatePairValidation:
    """Validation tests for PATCH pair endpoint."""

    @patch("shuffify.routes.require_auth")
    def test_no_fields_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.patch(
            api_path("/playlist/p1/pair"),
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_no_json_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.patch(
            api_path("/playlist/p1/pair"),
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.playlist_pairs"
        ".PlaylistPairService"
    )
    def test_pair_not_found_returns_404(
        self, mock_svc, mock_auth, auth_client
    ):
        from shuffify.services.playlist_pair_service import (
            PlaylistPairNotFoundError,
        )

        mock_auth.return_value = MagicMock()
        mock_svc.update_pair.side_effect = (
            PlaylistPairNotFoundError("No pair found")
        )
        resp = auth_client.patch(
            api_path("/playlist/p1/pair"),
            json={"auto_archive_on_remove": True},
        )
        assert resp.status_code == 404


# =============================================================================
# POST /playlist/<id>/pair/finalize-restore
# =============================================================================


class TestFinalizeRestoreAuth:
    """Authentication tests for finalize-restore endpoint."""

    @patch("shuffify.routes.require_auth")
    def test_finalize_restore_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pair/finalize-restore"),
                json={
                    "track_uris": [
                        "spotify:track:"
                        "a1b2c3d4e5f6g7h8i9j0k1"
                    ]
                },
            )
            assert resp.status_code == 401


class TestArchiveFinalizers:
    @pytest.mark.parametrize("endpoint,method,count_key", [
        ("finalize-restore", "finalize_restore", "restored_count"),
        ("archive", "append_archive", "archived_count"),
    ])
    @pytest.mark.parametrize("status,http_status,retryable", [
        ("complete", 200, False), ("failed", 409, False),
        ("failed", 502, True), ("unknown", 502, False),
    ])
    @patch("shuffify.routes.playlist_pairs.WorkshopFinalizeService")
    @patch("shuffify.routes.require_auth")
    def test_captured_request_and_exact_result_forwarded(
        self, auth, service, auth_client, endpoint, method, count_key,
        status, http_status, retryable,
    ):
        payload = {
            "source_playlist_id": "archive123",
            "target_track_uris": ["spotify:track:" + "a" * 22],
            "track_uris": ["spotify:track:" + "a" * 22] * 2,
            "inspect_only": True,
        }
        if endpoint == "archive":
            payload["baseline_track_uris"] = ["spotify:track:" + "b" * 22] * 2
        result = WorkshopFinalizeResult(
            "archive123", "Archive", status, "Result", http_status=http_status,
            retryable=retryable, extra={count_key: 2 if status == "complete" else 0},
        )
        finalize = getattr(service.return_value, method)
        finalize.return_value = result
        with patch("shuffify.routes.playlist_pairs.log_activity") as activity:
            response = auth_client.post(api_path(f"/playlist/p1/pair/{endpoint}"), json=payload)
        assert response.status_code == http_status
        expected = result.to_dict()
        if not expected["success"]:
            expected["category"] = "error"
        assert response.json == expected
        service.assert_called_once_with(auth.return_value)
        assert finalize.call_args.args[0] == "p1"
        assert finalize.call_args.kwargs == payload
        activity.assert_not_called()

    @pytest.mark.parametrize("endpoint", ["archive", "finalize-restore"])
    @pytest.mark.parametrize("payload", [None, {}, [], {"track_uris": []}, {
        "track_uris": ["spotify:track:" + "a" * 22],
    }])
    @patch("shuffify.routes.playlist_pairs.WorkshopFinalizeService")
    @patch("shuffify.routes.require_auth")
    def test_incomplete_captured_task_rejected(self, auth, service, auth_client, endpoint, payload):
        response = auth_client.post(api_path(f"/playlist/p1/pair/{endpoint}"), json=payload)
        assert response.status_code == 400
        assert response.json["status"] == "failed"
        assert response.json["retryable"] is False
        service.assert_not_called()
