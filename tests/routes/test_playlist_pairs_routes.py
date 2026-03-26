"""
Tests for playlist pair routes.

Tests cover authentication, error handling, and basic CRUD
for the /playlist/<id>/pair endpoints.
"""

from unittest.mock import patch, MagicMock

# =============================================================================
# Authentication Tests
# =============================================================================


class TestPairAuthRequired:
    """All pair endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_get_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/playlist/p1/pair")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_create_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pair",
                json={"create_new": True},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_delete_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete("/playlist/p1/pair")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_archive_tracks_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pair/archive",
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
                "/playlist/p1/pair/unarchive",
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
        resp = auth_client.get("/playlist/p1/pair")
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
            "/playlist/p1/pair",
            json={"create_new": False},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_no_json_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pair",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400


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
                "/playlist/p1/pair",
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
            "/playlist/p1/pair",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_no_json_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.patch(
            "/playlist/p1/pair",
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
            "/playlist/p1/pair",
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
                "/playlist/p1/pair/finalize-restore",
                json={
                    "track_uris": [
                        "spotify:track:"
                        "a1b2c3d4e5f6g7h8i9j0k1"
                    ]
                },
            )
            assert resp.status_code == 401


class TestFinalizeRestoreValidation:
    """Validation tests for finalize-restore endpoint."""

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.playlist_pairs"
        ".PlaylistPairService"
    )
    def test_no_pair_returns_404(
        self, mock_svc, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_svc.get_pair_for_playlist.return_value = None
        resp = auth_client.post(
            "/playlist/p1/pair/finalize-restore",
            json={
                "track_uris": [
                    "spotify:track:"
                    "a1b2c3d4e5f6g7h8i9j0k1"
                ]
            },
        )
        assert resp.status_code == 404

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.playlist_pairs"
        ".PlaylistPairService"
    )
    def test_empty_uris_returns_400(
        self, mock_svc, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_pair = MagicMock()
        mock_svc.get_pair_for_playlist.return_value = (
            mock_pair
        )
        resp = auth_client.post(
            "/playlist/p1/pair/finalize-restore",
            json={"track_uris": []},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.playlist_pairs"
        ".PlaylistPairService"
    )
    def test_invalid_uri_returns_400(
        self, mock_svc, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_pair = MagicMock()
        mock_svc.get_pair_for_playlist.return_value = (
            mock_pair
        )
        resp = auth_client.post(
            "/playlist/p1/pair/finalize-restore",
            json={"track_uris": ["invalid-uri"]},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.playlist_pairs"
        ".PlaylistPairService"
    )
    def test_finalize_success(
        self, mock_svc, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_pair = MagicMock()
        mock_svc.get_pair_for_playlist.return_value = (
            mock_pair
        )
        mock_svc.remove_from_archive.return_value = 1
        resp = auth_client.post(
            "/playlist/p1/pair/finalize-restore",
            json={
                "track_uris": [
                    "spotify:track:"
                    "a1b2c3d4e5f6g7h8i9j0k1"
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["restored_count"] == 1
        mock_svc.remove_from_archive.assert_called_once()
