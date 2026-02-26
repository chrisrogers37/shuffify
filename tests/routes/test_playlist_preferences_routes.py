"""
Tests for playlist preference routes.

Covers authentication, validation, happy paths for all
4 endpoints.
"""

from unittest.mock import patch, MagicMock



# =============================================================
# Authentication Tests
# =============================================================


class TestAuthRequired:
    """All endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_save_order_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as c:
            resp = c.post(
                "/api/playlist-preferences/order",
                json={"playlist_ids": ["a"]},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_toggle_hidden_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as c:
            resp = c.post(
                "/api/playlist-preferences/"
                "pl1/toggle-hidden"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_toggle_pinned_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as c:
            resp = c.post(
                "/api/playlist-preferences/"
                "pl1/toggle-pinned"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_reset_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as c:
            resp = c.post(
                "/api/playlist-preferences/reset"
            )
            assert resp.status_code == 401


# =============================================================
# Save Order Tests
# =============================================================


class TestSaveOrder:
    """Tests for POST /api/playlist-preferences/order."""

    @patch("shuffify.routes.require_auth")
    def test_save_order_success(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/api/playlist-preferences/order",
            json={
                "playlist_ids": ["pl1", "pl2", "pl3"]
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["count"] == 3

    @patch("shuffify.routes.require_auth")
    def test_save_order_empty_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/api/playlist-preferences/order",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_save_order_empty_list(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/api/playlist-preferences/order",
            json={"playlist_ids": []},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_save_order_invalid_format(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/api/playlist-preferences/order",
            json={
                "playlist_ids": ["valid", "in valid!"]
            },
        )
        assert resp.status_code == 400


# =============================================================
# Toggle Hidden Tests
# =============================================================


class TestToggleHidden:
    """Tests for POST toggle-hidden."""

    @patch("shuffify.routes.require_auth")
    def test_toggle_hidden_success(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/api/playlist-preferences/"
            "pl1/toggle-hidden"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["is_hidden"] is True

    @patch("shuffify.routes.require_auth")
    def test_toggle_hidden_twice(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        auth_client.post(
            "/api/playlist-preferences/"
            "pl1/toggle-hidden"
        )
        resp = auth_client.post(
            "/api/playlist-preferences/"
            "pl1/toggle-hidden"
        )
        data = resp.get_json()
        assert data["is_hidden"] is False


# =============================================================
# Toggle Pinned Tests
# =============================================================


class TestTogglePinned:
    """Tests for POST toggle-pinned."""

    @patch("shuffify.routes.require_auth")
    def test_toggle_pinned_success(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/api/playlist-preferences/"
            "pl1/toggle-pinned"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["is_pinned"] is True


# =============================================================
# Reset Tests
# =============================================================


class TestReset:
    """Tests for POST /api/playlist-preferences/reset."""

    @patch("shuffify.routes.require_auth")
    def test_reset_success(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        # Save some prefs first
        auth_client.post(
            "/api/playlist-preferences/order",
            json={"playlist_ids": ["pl1", "pl2"]},
        )
        resp = auth_client.post(
            "/api/playlist-preferences/reset"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["count"] == 2

    @patch("shuffify.routes.require_auth")
    def test_reset_empty(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/api/playlist-preferences/reset"
        )
        data = resp.get_json()
        assert data["success"] is True
        assert data["count"] == 0
