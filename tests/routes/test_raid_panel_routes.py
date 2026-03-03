"""
Tests for raid panel routes.

Tests cover authentication, validation, DB unavailability,
and basic success paths.
"""

import time
from unittest.mock import patch, MagicMock



# =============================================================================
# Authentication Tests
# =============================================================================


class TestRaidAuthRequired:
    """All raid panel endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_status_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(
                "/playlist/p1/raid-status"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_watch_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/raid-watch",
                json={"source_playlist_id": "s1"},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_raid_now_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/raid-now",
                json={},
            )
            assert resp.status_code == 401


# =============================================================================
# Validation Tests
# =============================================================================


class TestRaidValidation:
    """Request validation tests."""

    @patch("shuffify.routes.require_auth")
    def test_watch_invalid_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-watch",
            json={"source_playlist_id": ""},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_unwatch_missing_source_id(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-unwatch",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_raid_now_empty_source_list(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-now",
            json={"source_playlist_ids": []},
        )
        assert resp.status_code == 400


# =============================================================================
# DB Unavailable Tests
# =============================================================================


class TestRaidDbUnavailable:
    """DB unavailable returns 503."""

    @patch("shuffify.routes.require_auth")
    @patch("shuffify.is_db_available")
    def test_status_db_unavailable(
        self, mock_db, mock_auth, db_app
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = False
        with db_app.test_client() as client:
            with client.session_transaction() as sess:
                sess["spotify_token"] = {
                    "access_token": "t",
                    "expires_at": time.time() + 3600,
                }
                sess["user_data"] = {"id": "user123"}
            resp = client.get(
                "/playlist/p1/raid-status"
            )
            assert resp.status_code == 503

    @patch("shuffify.routes.require_auth")
    @patch("shuffify.is_db_available")
    def test_watch_db_unavailable(
        self, mock_db, mock_auth, db_app
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = False
        with db_app.test_client() as client:
            with client.session_transaction() as sess:
                sess["spotify_token"] = {
                    "access_token": "t",
                    "expires_at": time.time() + 3600,
                }
                sess["user_data"] = {"id": "user123"}
            resp = client.post(
                "/playlist/p1/raid-watch",
                json={"source_playlist_id": "s1"},
            )
            assert resp.status_code == 503


# =============================================================================
# Success Path Tests
# =============================================================================


class TestRaidSuccessPaths:
    """Basic success path tests."""

    @patch("shuffify.routes.require_auth")
    def test_status_returns_structure(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get(
            "/playlist/p1/raid-status"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "raid_status" in data
        status = data["raid_status"]
        assert "sources" in status
        assert "has_schedule" in status
        assert "source_count" in status
        assert "max_sources" in status


# =============================================================================
# POST /playlist/<id>/raid-add-url
# =============================================================================


class TestRaidAddUrlAuth:
    """Authentication tests for raid-add-url."""

    @patch("shuffify.routes.require_auth")
    def test_add_url_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/raid-add-url",
                json={
                    "url": "https://open.spotify.com"
                    "/playlist/abc",
                },
            )
            assert resp.status_code == 401


class TestRaidAddUrlValidation:
    """Validation tests for raid-add-url."""

    @patch("shuffify.routes.require_auth")
    def test_missing_url_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-add-url",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_empty_url_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-add-url",
            json={"url": "  "},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.raid_panel"
        ".parse_spotify_playlist_url"
    )
    def test_invalid_url_returns_400(
        self, mock_parse, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_parse.return_value = None
        resp = auth_client.post(
            "/playlist/p1/raid-add-url",
            json={"url": "not-a-spotify-url"},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.raid_panel"
        ".parse_spotify_playlist_url"
    )
    def test_self_reference_returns_400(
        self, mock_parse, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_parse.return_value = "p1"
        resp = auth_client.post(
            "/playlist/p1/raid-add-url",
            json={
                "url": "https://open.spotify.com"
                "/playlist/p1",
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "same playlist" in data["message"].lower()

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.raid_panel"
        ".parse_spotify_playlist_url"
    )
    @patch(
        "shuffify.routes.raid_panel.PlaylistService"
    )
    def test_own_playlist_returns_400(
        self,
        mock_svc_cls,
        mock_parse,
        mock_auth,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()
        mock_parse.return_value = "ext1"

        mock_playlist = MagicMock()
        mock_playlist.owner_id = "user123"
        mock_playlist.name = "My List"
        mock_playlist.total_tracks = 10
        mock_svc_cls.return_value.get_playlist.return_value = (
            mock_playlist
        )

        resp = auth_client.post(
            "/playlist/p1/raid-add-url",
            json={
                "url": "https://open.spotify.com"
                "/playlist/ext1",
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "own playlist" in data["message"].lower()

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.raid_panel"
        ".parse_spotify_playlist_url"
    )
    @patch(
        "shuffify.routes.raid_panel.PlaylistService"
    )
    def test_playlist_not_found_returns_404(
        self,
        mock_svc_cls,
        mock_parse,
        mock_auth,
        auth_client,
    ):
        from shuffify.services.playlist_service import (
            PlaylistNotFoundError,
        )

        mock_auth.return_value = MagicMock()
        mock_parse.return_value = "ext1"
        mock_svc_cls.return_value.get_playlist.side_effect = (
            PlaylistNotFoundError("Not found")
        )

        resp = auth_client.post(
            "/playlist/p1/raid-add-url",
            json={
                "url": "https://open.spotify.com"
                "/playlist/ext1",
            },
        )
        assert resp.status_code == 404
