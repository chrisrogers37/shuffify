"""
Tests for pending raid routes.

Tests cover authentication, validation, and basic success paths
for the pending raid track inbox endpoints.
"""

from unittest.mock import patch, MagicMock


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
                "/playlist/p1/pending-raids"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_promote_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pending-raids/promote",
                json={"track_ids": [1]},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_dismiss_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pending-raids/dismiss",
                json={"track_ids": [1]},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_promote_all_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pending-raids/promote-all"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_dismiss_all_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pending-raids/dismiss-all"
            )
            assert resp.status_code == 401


# =============================================================
# Validation Tests
# =============================================================


class TestPendingRaidsValidation:
    """Request validation tests."""

    @patch("shuffify.routes.require_auth")
    def test_promote_missing_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pending-raids/promote",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_dismiss_missing_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pending-raids/dismiss",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_promote_empty_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pending-raids/promote",
            json={"track_ids": []},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_dismiss_empty_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pending-raids/dismiss",
            json={"track_ids": []},
        )
        assert resp.status_code == 400
