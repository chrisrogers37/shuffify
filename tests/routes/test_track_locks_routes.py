"""
Tests for track lock routes.

Covers authentication, validation, and happy paths for
GET /workshop/<id>/locks, POST .../toggle, POST .../unlock-all.
"""

from unittest.mock import patch, MagicMock


# =============================================================
# Authentication Tests
# =============================================================


class TestAuthRequired:
    """All lock endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_get_locks_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as c:
            resp = c.get("/workshop/pl1/locks")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_toggle_lock_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as c:
            resp = c.post(
                "/workshop/pl1/locks/toggle",
                json={
                    "track_uri": "spotify:track:t1",
                    "position": 0,
                },
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_unlock_all_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as c:
            resp = c.post(
                "/workshop/pl1/locks/unlock-all",
                json={},
            )
            assert resp.status_code == 401


# =============================================================
# Get Locks Tests
# =============================================================


class TestGetLocks:
    """Tests for GET /workshop/<id>/locks."""

    @patch("shuffify.routes.require_auth")
    def test_get_locks_empty(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get("/workshop/pl1/locks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["locks"] == []

    @patch("shuffify.routes.require_auth")
    def test_get_locks_with_data(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        # Create a lock
        auth_client.post(
            "/workshop/pl1/locks/toggle",
            json={
                "track_uri": "spotify:track:t1",
                "position": 0,
            },
        )

        resp = auth_client.get("/workshop/pl1/locks")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["locks"]) == 1
        assert data["locks"][0]["track_uri"] == (
            "spotify:track:t1"
        )
        assert data["locks"][0]["lock_tier"] == "standard"


# =============================================================
# Toggle Lock Tests
# =============================================================


class TestToggleLock:
    """Tests for POST /workshop/<id>/locks/toggle."""

    @patch("shuffify.routes.require_auth")
    def test_toggle_creates_standard(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/workshop/pl1/locks/toggle",
            json={
                "track_uri": "spotify:track:t1",
                "position": 0,
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["lock"]["lock_tier"] == "standard"

    @patch("shuffify.routes.require_auth")
    def test_toggle_upgrades_to_super(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        # First toggle -> standard
        auth_client.post(
            "/workshop/pl1/locks/toggle",
            json={
                "track_uri": "spotify:track:t1",
                "position": 0,
            },
        )

        # Second toggle -> super
        resp = auth_client.post(
            "/workshop/pl1/locks/toggle",
            json={
                "track_uri": "spotify:track:t1",
                "position": 0,
            },
        )
        data = resp.get_json()
        assert data["lock"]["lock_tier"] == "super"

    @patch("shuffify.routes.require_auth")
    def test_toggle_unlocks(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        # Toggle 3 times: standard -> super -> unlocked
        for _ in range(3):
            resp = auth_client.post(
                "/workshop/pl1/locks/toggle",
                json={
                    "track_uri": "spotify:track:t1",
                    "position": 0,
                },
            )
        data = resp.get_json()
        assert data["success"] is True
        assert data["lock"] is None

    @patch("shuffify.routes.require_auth")
    def test_toggle_invalid_uri(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/workshop/pl1/locks/toggle",
            json={
                "track_uri": "invalid_uri",
                "position": 0,
            },
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_toggle_missing_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/workshop/pl1/locks/toggle"
        )
        assert resp.status_code == 400


# =============================================================
# Unlock All Tests
# =============================================================


class TestUnlockAll:
    """Tests for POST /workshop/<id>/locks/unlock-all."""

    @patch("shuffify.routes.require_auth")
    def test_unlock_all_success(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        # Create locks and verify they succeed
        r1 = auth_client.post(
            "/workshop/pl1/locks/toggle",
            json={
                "track_uri": "spotify:track:t1",
                "position": 0,
            },
        )
        assert r1.get_json()["success"] is True

        r2 = auth_client.post(
            "/workshop/pl1/locks/toggle",
            json={
                "track_uri": "spotify:track:t2",
                "position": 1,
            },
        )
        assert r2.get_json()["success"] is True

        # Verify locks exist
        resp = auth_client.get("/workshop/pl1/locks")
        assert len(resp.get_json()["locks"]) == 2

        resp = auth_client.post(
            "/workshop/pl1/locks/unlock-all",
            json={"track_uris": None},
        )
        data = resp.get_json()
        assert data["success"] is True, (
            f"unlock-all failed: {data}"
        )
        assert data["count"] == 2

        # Verify all unlocked
        resp = auth_client.get("/workshop/pl1/locks")
        data = resp.get_json()
        assert data["locks"] == []

    @patch("shuffify.routes.require_auth")
    def test_unlock_specific_uris(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        auth_client.post(
            "/workshop/pl1/locks/toggle",
            json={
                "track_uri": "spotify:track:t1",
                "position": 0,
            },
        )
        auth_client.post(
            "/workshop/pl1/locks/toggle",
            json={
                "track_uri": "spotify:track:t2",
                "position": 1,
            },
        )

        resp = auth_client.post(
            "/workshop/pl1/locks/unlock-all",
            json={
                "track_uris": ["spotify:track:t1"],
            },
        )
        data = resp.get_json()
        assert data["count"] == 1

        # t2 should still be locked
        resp = auth_client.get("/workshop/pl1/locks")
        data = resp.get_json()
        assert len(data["locks"]) == 1
        assert data["locks"][0]["track_uri"] == (
            "spotify:track:t2"
        )
