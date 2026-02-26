"""
Tests for upstream source routes.

Tests cover list, add, and delete upstream source endpoints.
All routes use @require_auth_and_db decorator.
"""

from unittest.mock import patch, MagicMock

# Note: upstream_sources routes use @require_auth_and_db decorator
# from shuffify.routes.__init__. Mock targets must be at
# shuffify.routes.require_auth (not shuffify.routes.upstream_sources.*).


class TestListUpstreamSources:
    """Tests for GET /playlist/<id>/upstream-sources."""

    @patch("shuffify.routes.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(
                "/playlist/p1/upstream-sources"
            )
            assert resp.status_code == 401

    @patch(
        "shuffify.routes.upstream_sources"
        ".UpstreamSourceService"
    )
    @patch("shuffify.routes.require_auth")
    def test_list_empty_sources(
        self, mock_auth, mock_svc, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_svc.list_sources.return_value = []

        resp = auth_client.get(
            "/playlist/p1/upstream-sources"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["sources"] == []

    @patch(
        "shuffify.routes.upstream_sources"
        ".UpstreamSourceService"
    )
    @patch("shuffify.routes.require_auth")
    def test_list_returns_sources(
        self, mock_auth, mock_svc, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_source = MagicMock()
        mock_source.to_dict.return_value = {
            "id": 1,
            "source_playlist_id": "s1",
        }
        mock_svc.list_sources.return_value = [mock_source]

        resp = auth_client.get(
            "/playlist/p1/upstream-sources"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["sources"]) == 1

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.require_auth")
    def test_db_unavailable_returns_503(
        self, mock_auth, mock_db, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = False

        resp = auth_client.get(
            "/playlist/p1/upstream-sources"
        )
        assert resp.status_code == 503


class TestAddUpstreamSource:
    """Tests for POST /playlist/<id>/upstream-sources."""

    @patch("shuffify.routes.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/upstream-sources",
                json={"source_playlist_id": "s1"},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_empty_json_body_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        resp = auth_client.post(
            "/playlist/p1/upstream-sources",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_missing_source_playlist_id_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        resp = auth_client.post(
            "/playlist/p1/upstream-sources",
            json={"source_name": "something"},
        )
        assert resp.status_code == 400

    @patch(
        "shuffify.routes.upstream_sources"
        ".UpstreamSourceService"
    )
    @patch("shuffify.routes.require_auth")
    def test_add_success(
        self, mock_auth, mock_svc, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_source = MagicMock()
        mock_source.to_dict.return_value = {"id": 1}
        mock_svc.add_source.return_value = mock_source

        resp = auth_client.post(
            "/playlist/p1/upstream-sources",
            json={"source_playlist_id": "s1"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch(
        "shuffify.routes.upstream_sources"
        ".UpstreamSourceService"
    )
    @patch("shuffify.routes.require_auth")
    def test_duplicate_source_returns_400(
        self, mock_auth, mock_svc, auth_client
    ):
        from shuffify.services import UpstreamSourceError

        mock_auth.return_value = MagicMock()
        mock_svc.add_source.side_effect = (
            UpstreamSourceError("duplicate")
        )

        resp = auth_client.post(
            "/playlist/p1/upstream-sources",
            json={"source_playlist_id": "s1"},
        )
        assert resp.status_code == 400


class TestDeleteUpstreamSource:
    """Tests for DELETE /upstream-sources/<int:source_id>."""

    @patch("shuffify.routes.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete("/upstream-sources/1")
            assert resp.status_code == 401

    @patch(
        "shuffify.routes.upstream_sources"
        ".UpstreamSourceService"
    )
    @patch("shuffify.routes.require_auth")
    def test_delete_success(
        self, mock_auth, mock_svc, auth_client
    ):
        mock_auth.return_value = MagicMock()

        resp = auth_client.delete("/upstream-sources/1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch(
        "shuffify.routes.upstream_sources"
        ".UpstreamSourceService"
    )
    @patch("shuffify.routes.require_auth")
    def test_delete_not_found_returns_404(
        self, mock_auth, mock_svc, auth_client
    ):
        from shuffify.services import (
            UpstreamSourceNotFoundError,
        )

        mock_auth.return_value = MagicMock()
        mock_svc.delete_source.side_effect = (
            UpstreamSourceNotFoundError("gone")
        )

        resp = auth_client.delete("/upstream-sources/99")
        assert resp.status_code == 404

    @patch(
        "shuffify.routes.upstream_sources"
        ".UpstreamSourceService"
    )
    @patch("shuffify.routes.require_auth")
    def test_delete_error_returns_500(
        self, mock_auth, mock_svc, auth_client
    ):
        from shuffify.services import UpstreamSourceError

        mock_auth.return_value = MagicMock()
        mock_svc.delete_source.side_effect = (
            UpstreamSourceError("server error")
        )

        resp = auth_client.delete("/upstream-sources/1")
        assert resp.status_code == 500
