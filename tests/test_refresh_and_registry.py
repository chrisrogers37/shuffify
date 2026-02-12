"""
Tests for refresh playlists route and updated registry behavior.

Covers the new POST /refresh-playlists endpoint and hidden algorithm filtering.
"""

import pytest
from unittest.mock import Mock, patch

from shuffify.shuffle_algorithms.registry import ShuffleRegistry


class TestRegistryHiddenAlgorithms:
    """Tests for algorithm visibility in the registry."""

    def test_tempo_gradient_is_registered(self):
        """TempoGradientShuffle should be registered (available by name)."""
        algo_class = ShuffleRegistry.get_algorithm("TempoGradientShuffle")
        assert algo_class is not None

    def test_tempo_gradient_is_hidden_from_listing(self):
        """TempoGradientShuffle should not appear in list_algorithms."""
        algorithms = ShuffleRegistry.list_algorithms()
        class_names = [a["class_name"] for a in algorithms]
        assert "TempoGradientShuffle" not in class_names

    def test_artist_spacing_is_visible(self):
        """ArtistSpacingShuffle should appear in list_algorithms."""
        algorithms = ShuffleRegistry.list_algorithms()
        class_names = [a["class_name"] for a in algorithms]
        assert "ArtistSpacingShuffle" in class_names

    def test_album_sequence_is_visible(self):
        """AlbumSequenceShuffle should appear in list_algorithms."""
        algorithms = ShuffleRegistry.list_algorithms()
        class_names = [a["class_name"] for a in algorithms]
        assert "AlbumSequenceShuffle" in class_names

    def test_visible_algorithm_count(self):
        """Should have 6 visible algorithms (7 total minus 1 hidden)."""
        algorithms = ShuffleRegistry.list_algorithms()
        assert len(algorithms) == 6

    def test_all_algorithms_count(self):
        """Should have 7 total registered algorithms."""
        all_algos = ShuffleRegistry.get_available_algorithms()
        assert len(all_algos) == 7

    def test_algorithm_display_order(self):
        """Algorithms should appear in the defined order."""
        algorithms = ShuffleRegistry.list_algorithms()
        names = [a["name"] for a in algorithms]
        expected = [
            "Basic",
            "Percentage",
            "Balanced",
            "Stratified",
            "Artist Spacing",
            "Album Sequence",
        ]
        assert names == expected


class TestRefreshPlaylistsRoute:
    """Tests for POST /refresh-playlists endpoint."""

    @pytest.fixture
    def app(self):
        """Create Flask app for testing."""
        import os

        os.environ["SPOTIFY_CLIENT_ID"] = "test_client_id"
        os.environ["SPOTIFY_CLIENT_SECRET"] = "test_client_secret"
        os.environ["SPOTIFY_REDIRECT_URI"] = "http://localhost:5000/callback"
        os.environ["SECRET_KEY"] = "test-secret-key-for-testing"

        from shuffify import create_app

        app = create_app("development")
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        """Flask test client."""
        return app.test_client()

    def test_refresh_requires_auth(self, client):
        """Should return 401 when not authenticated."""
        response = client.post("/refresh-playlists")
        assert response.status_code == 401
        data = response.get_json()
        assert data["success"] is False

    @patch("shuffify.routes.playlists.require_auth")
    @patch("shuffify.routes.playlists.PlaylistService")
    def test_refresh_returns_playlists(
        self, mock_playlist_service_cls, mock_auth, client
    ):
        """Should return refreshed playlists on success."""
        mock_client = Mock()
        mock_auth.return_value = mock_client

        mock_service = Mock()
        mock_service.get_user_playlists.return_value = [
            {"id": "p1", "name": "Playlist 1"},
            {"id": "p2", "name": "Playlist 2"},
        ]
        mock_playlist_service_cls.return_value = mock_service

        response = client.post("/refresh-playlists")
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "Playlists refreshed" in data["message"]
        assert len(data["playlists"]) == 2

    @patch("shuffify.routes.playlists.require_auth")
    @patch("shuffify.routes.playlists.PlaylistService")
    def test_refresh_calls_skip_cache(
        self, mock_playlist_service_cls, mock_auth, client
    ):
        """Should call get_user_playlists with skip_cache=True."""
        mock_client = Mock()
        mock_auth.return_value = mock_client

        mock_service = Mock()
        mock_service.get_user_playlists.return_value = []
        mock_playlist_service_cls.return_value = mock_service

        client.post("/refresh-playlists")

        mock_service.get_user_playlists.assert_called_once_with(
            skip_cache=True
        )

    @patch("shuffify.routes.playlists.require_auth")
    @patch("shuffify.routes.playlists.PlaylistService")
    def test_refresh_handles_error(
        self, mock_playlist_service_cls, mock_auth, client
    ):
        """Should return 500 on PlaylistError."""
        from shuffify.services import PlaylistError

        mock_client = Mock()
        mock_auth.return_value = mock_client

        mock_service = Mock()
        mock_service.get_user_playlists.side_effect = PlaylistError(
            "API error"
        )
        mock_playlist_service_cls.return_value = mock_service

        response = client.post("/refresh-playlists")
        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False
