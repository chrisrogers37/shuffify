"""
Tests for raid link and drip routes.

Tests cover authentication, validation, and basic success paths
for raid playlist link CRUD and drip endpoints.
"""

from unittest.mock import patch, MagicMock


# =============================================================
# Authentication Tests
# =============================================================


class TestRaidLinkAuthRequired:
    """Raid link endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_create_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/raid-link",
                json={"create_new": True},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_update_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.put(
                "/playlist/p1/raid-link",
                json={"drip_count": 5},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_delete_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete(
                "/playlist/p1/raid-link"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_drip_now_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/drip-now",
                json={},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_drip_toggle_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/drip-schedule-toggle"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_source_count_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.put(
                "/playlist/p1/raid-source-count",
                json={
                    "source_id": 1,
                    "raid_count": 5,
                },
            )
            assert resp.status_code == 401


# =============================================================
# Validation Tests
# =============================================================


class TestRaidLinkValidation:
    """Request validation tests."""

    @patch("shuffify.routes.require_auth")
    def test_create_empty_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-link",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_update_no_fields(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.put(
            "/playlist/p1/raid-link",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_update_drip_count_too_low(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.put(
            "/playlist/p1/raid-link",
            json={"drip_count": 0},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_source_count_too_low(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.put(
            "/playlist/p1/raid-source-count",
            json={"source_id": 1, "raid_count": 0},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_create_existing_without_id(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-link",
            json={
                "create_new": False,
                # Missing raid_playlist_id
            },
        )
        assert resp.status_code == 400


# =============================================================
# Success Path Tests
# =============================================================


class TestRaidLinkSuccess:
    """Basic success path tests."""

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.raid_panel"
        ".RaidLinkService"
    )
    def test_create_new_raid_link(
        self, mock_svc, mock_auth, auth_client,
    ):
        mock_auth.return_value = MagicMock()
        # No existing link — allows creation to proceed
        mock_svc.get_link_for_playlist.return_value = None
        mock_link = MagicMock()
        mock_link.to_dict.return_value = {
            "id": 1,
            "raid_playlist_id": "new_raid",
        }
        mock_svc.create_raid_playlist.return_value = (
            "new_raid", "My Playlist [Raids]"
        )
        mock_svc.create_link.return_value = mock_link

        resp = auth_client.post(
            "/playlist/p1/raid-link",
            json={
                "create_new": True,
                "target_playlist_name": "My Playlist",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.raid_panel"
        ".RaidSyncService"
    )
    def test_drip_now_success(
        self, mock_svc, mock_auth, auth_client,
    ):
        mock_auth.return_value = MagicMock()
        mock_svc.drip_now.return_value = {
            "tracks_added": 3,
            "tracks_total": 50,
            "status": "success",
        }

        resp = auth_client.post(
            "/playlist/p1/drip-now",
            json={},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["tracks_added"] == 3
