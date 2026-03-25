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

    @patch("shuffify.routes.require_auth")
    def test_unpromote_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pending-raids/unpromote",
                json={
                    "track_uris": [
                        "spotify:track:1",
                    ]
                },
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

    @patch("shuffify.routes.require_auth")
    def test_finalize_missing_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pending-raids/finalize",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_finalize_empty_track_ids(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pending-raids/finalize",
            json={"track_ids": []},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_unpromote_missing_uris(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pending-raids/unpromote",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_unpromote_empty_uris(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pending-raids/unpromote",
            json={"track_uris": []},
        )
        assert resp.status_code == 400


# =============================================================
# Finalize Endpoint Tests
# =============================================================


class TestPendingRaidsFinalize:
    """Tests for the finalize and finalize-all endpoints.

    These endpoints mark DB records as PROMOTED and clean up
    the raid playlist WITHOUT adding to the target playlist
    (commit already handled that).
    """

    @patch("shuffify.routes.require_auth")
    def test_finalize_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pending-raids/finalize",
                json={"track_ids": [1]},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_finalize_all_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pending-raids/finalize-all"
            )
            assert resp.status_code == 401

    @patch(
        "shuffify.routes.raid_panel"
        ".RaidLinkService"
        ".remove_tracks_from_raid_playlist"
    )
    @patch(
        "shuffify.routes.raid_panel"
        ".PendingRaidService"
    )
    @patch("shuffify.routes.require_auth")
    def test_finalize_marks_promoted_no_spotify_add(
        self, mock_auth, mock_service,
        mock_remove, auth_client,
    ):
        """Finalize should promote in DB but NOT call
        playlist_add_items."""
        mock_track = MagicMock()
        mock_track.track_uri = "spotify:track:1"
        mock_service.promote_tracks.return_value = [
            mock_track
        ]

        resp = auth_client.post(
            "/playlist/p1/pending-raids/finalize",
            json={"track_ids": [1]},
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["promoted_count"] == 1
        mock_service.promote_tracks.assert_called_once()

    @patch(
        "shuffify.routes.raid_panel"
        ".RaidLinkService"
        ".remove_tracks_from_raid_playlist"
    )
    @patch(
        "shuffify.routes.raid_panel"
        ".PendingRaidService"
    )
    @patch("shuffify.routes.require_auth")
    def test_finalize_removes_from_raid_playlist(
        self, mock_auth, mock_service,
        mock_remove, auth_client,
    ):
        """Finalize should clean up the raid playlist."""
        mock_track = MagicMock()
        mock_track.track_uri = "spotify:track:1"
        mock_service.promote_tracks.return_value = [
            mock_track
        ]

        resp = auth_client.post(
            "/playlist/p1/pending-raids/finalize",
            json={"track_ids": [1]},
        )

        assert resp.status_code == 200
        mock_remove.assert_called_once()

    @patch(
        "shuffify.routes.raid_panel"
        ".RaidLinkService"
        ".remove_tracks_from_raid_playlist"
    )
    @patch(
        "shuffify.routes.raid_panel"
        ".PendingRaidService"
    )
    @patch("shuffify.routes.require_auth")
    def test_finalize_all_promotes_all(
        self, mock_auth, mock_service,
        mock_remove, auth_client,
    ):
        """Finalize-all should promote all pending tracks."""
        mock_track = MagicMock()
        mock_track.track_uri = "spotify:track:1"
        mock_service.promote_all.return_value = [
            mock_track
        ]

        resp = auth_client.post(
            "/playlist/p1/pending-raids/finalize-all",
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["promoted_count"] == 1
        mock_service.promote_all.assert_called_once()
        mock_remove.assert_called_once()

    @patch(
        "shuffify.routes.raid_panel"
        ".PendingRaidService"
    )
    @patch("shuffify.routes.require_auth")
    def test_finalize_empty_returns_success(
        self, mock_auth, mock_service, auth_client,
    ):
        """Finalize with no matching tracks returns success
        with 0 count."""
        mock_service.promote_tracks.return_value = []

        resp = auth_client.post(
            "/playlist/p1/pending-raids/finalize",
            json={"track_ids": [999]},
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["promoted_count"] == 0

    @patch(
        "shuffify.routes.raid_panel"
        ".PendingRaidService"
    )
    @patch("shuffify.routes.require_auth")
    def test_finalize_all_empty_returns_success(
        self, mock_auth, mock_service, auth_client,
    ):
        """Finalize-all with no pending tracks returns
        success with 0 count."""
        mock_service.promote_all.return_value = []

        resp = auth_client.post(
            "/playlist/p1/pending-raids/finalize-all",
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["promoted_count"] == 0
