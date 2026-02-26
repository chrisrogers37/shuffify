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
