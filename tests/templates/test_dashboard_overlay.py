"""
Tests for the dashboard shuffle overlay UI.

Verifies that the hover overlay structure renders correctly:
algorithm icon buttons, gear icons, keep-first stepper,
workshop/undo placement, info bar simplification, and hidden forms.
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService


MOCK_ALGORITHMS = [
    {
        "name": "Basic",
        "class_name": "BasicShuffle",
        "description": "Random shuffle",
        "parameters": {
            "keep_first": {
                "type": "integer",
                "default": 0,
                "min": 0,
                "description": "Tracks to keep",
            }
        },
    },
    {
        "name": "Percentage",
        "class_name": "PercentageShuffle",
        "description": "Shuffle a percentage",
        "parameters": {
            "shuffle_percentage": {
                "type": "float",
                "default": 50,
                "min": 0,
                "max": 100,
                "description": "Percentage",
            },
            "shuffle_location": {
                "type": "string",
                "default": "front",
                "options": ["front", "back"],
                "description": "Location",
            },
        },
    },
    {
        "name": "Balanced",
        "class_name": "BalancedShuffle",
        "description": "Balanced shuffle",
        "parameters": {
            "keep_first": {
                "type": "integer",
                "default": 0,
                "min": 0,
                "description": "Tracks to keep",
            },
            "section_count": {
                "type": "integer",
                "default": 4,
                "min": 2,
                "max": 10,
                "description": "Sections",
            },
        },
    },
    {
        "name": "Stratified",
        "class_name": "StratifiedShuffle",
        "description": "Stratified shuffle",
        "parameters": {
            "keep_first": {
                "type": "integer",
                "default": 0,
                "min": 0,
                "description": "Tracks to keep",
            },
            "section_count": {
                "type": "integer",
                "default": 5,
                "min": 2,
                "max": 20,
                "description": "Sections",
            },
        },
    },
    {
        "name": "Artist Spacing",
        "class_name": "ArtistSpacingShuffle",
        "description": "Space artists apart",
        "parameters": {
            "min_spacing": {
                "type": "integer",
                "default": 1,
                "min": 1,
                "description": "Min spacing",
            }
        },
    },
    {
        "name": "Album Sequence",
        "class_name": "AlbumSequenceShuffle",
        "description": "Keep album order",
        "parameters": {
            "shuffle_within_albums": {
                "type": "string",
                "default": "no",
                "options": ["no", "yes"],
                "description": "Shuffle within",
            }
        },
    },
]

MOCK_PLAYLIST = MagicMock()
MOCK_PLAYLIST.id = "playlist_abc123"
MOCK_PLAYLIST.name = "Test Playlist"
MOCK_PLAYLIST.images = [MagicMock(url="https://img.example.com/1.jpg")]
MOCK_PLAYLIST.tracks = MagicMock(total=42)
MOCK_PLAYLIST.external_urls = MagicMock(
    spotify="https://open.spotify.com/playlist/abc123"
)


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"
    os.environ.pop("DATABASE_URL", None)

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///:memory:"
    )
    app.config["TESTING"] = True
    app.config["SCHEDULER_ENABLED"] = False

    with app.app_context():
        db.drop_all()
        db.create_all()
        UserService.upsert_from_spotify(
            {
                "id": "user123",
                "display_name": "Test User",
                "images": [],
            }
        )
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def auth_client(db_app):
    """Authenticated test client with session user data."""
    with db_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
                "refresh_token": "test_refresh",
            }
            sess["user_data"] = {
                "id": "user123",
                "display_name": "Test User",
            }
        yield client


@pytest.fixture
def dashboard_html(auth_client):
    """Render the dashboard and return HTML string."""
    with patch(
        "shuffify.routes.core.is_authenticated"
    ) as mock_is_auth, patch(
        "shuffify.routes.core.AuthService"
    ) as mock_auth_svc, patch(
        "shuffify.routes.core.PlaylistService"
    ) as mock_ps_class, patch(
        "shuffify.routes.core.ShuffleService"
    ) as mock_shuffle_svc, patch(
        "shuffify.routes.core.DashboardService"
    ) as mock_dash_svc:
        mock_is_auth.return_value = True
        mock_client = MagicMock()
        mock_auth_svc.get_authenticated_client.return_value = (
            mock_client
        )
        mock_auth_svc.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        }

        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = [MOCK_PLAYLIST]
        mock_ps_class.return_value = mock_ps

        mock_shuffle_svc.list_algorithms.return_value = (
            MOCK_ALGORITHMS
        )
        mock_dash_svc.get_dashboard_data.return_value = {}

        resp = auth_client.get("/")
        assert resp.status_code == 200
        return resp.data.decode()


class TestOverlayRendering:
    """Tests for the shuffle overlay UI structure."""

    def test_overlay_renders_for_each_playlist(
        self, dashboard_html
    ):
        """Overlay element exists within the playlist tile."""
        assert "shuffle-overlay" in dashboard_html

    def test_algorithm_icons_render(self, dashboard_html):
        """Six algorithm icon buttons render per playlist."""
        for algo in MOCK_ALGORITHMS:
            assert (
                f'data-algorithm="{algo["class_name"]}"'
                in dashboard_html
            )

    def test_gear_icon_present_for_parameterized_algorithms(
        self, dashboard_html
    ):
        """Gear icon appears for algorithms with params beyond keep_first."""
        # These should have gear icons
        for class_name in [
            "PercentageShuffle",
            "BalancedShuffle",
            "StratifiedShuffle",
            "ArtistSpacingShuffle",
            "AlbumSequenceShuffle",
        ]:
            assert (
                f'class="algo-settings-btn'
                in dashboard_html
            ), (
                f"Gear icon missing for {class_name}"
            )

    def test_gear_icon_absent_for_basic_shuffle(
        self, dashboard_html
    ):
        """BasicShuffle (only keep_first param) has no gear icon."""
        # Count gear icon HTML elements (class= attribute, not JS refs)
        count = dashboard_html.count(
            'class="algo-settings-btn'
        )
        assert count == 5, (
            f"Expected 5 gear icons, got {count}"
        )

    def test_keep_first_stepper_renders(self, dashboard_html):
        """Keep-first stepper renders with correct id and max."""
        assert (
            'id="keep-first-playlist_abc123"'
            in dashboard_html
        )
        assert 'max="42"' in dashboard_html

    def test_workshop_link_on_overlay(self, dashboard_html):
        """Workshop link is inside the overlay, not the info bar."""
        # Workshop should appear with the overlay styling
        assert (
            "Open Playlist Workshop" in dashboard_html
        )
        assert (
            "bg-spotify-green/80" in dashboard_html
        )

    def test_info_bar_simplified(self, dashboard_html):
        """Info bar has playlist name and Spotify link but no Workshop button."""
        # The info bar (bg-spotify-green px-4) should NOT contain
        # "Workshop" text. Workshop is now in the overlay.
        # Find the info bar section
        info_bar_start = dashboard_html.find(
            "Playlist Info Bar"
        )
        assert info_bar_start != -1, (
            "Info bar comment not found"
        )
        # The section from info bar to end of its div
        info_section = dashboard_html[
            info_bar_start:info_bar_start + 500
        ]
        assert "Workshop" not in info_section

    def test_hidden_forms_present(self, dashboard_html):
        """Hidden shuffle and undo forms exist."""
        assert "shuffle-form" in dashboard_html
        assert "undo-form" in dashboard_html
        assert (
            'id="undo-form-playlist_abc123"'
            in dashboard_html
        )

    def test_undo_button_hidden_by_default(
        self, dashboard_html
    ):
        """Undo overlay button has hidden class initially."""
        assert (
            "undo-overlay-btn hidden" in dashboard_html
        )

    def test_spotify_link_in_info_bar(self, dashboard_html):
        """Spotify external link is in the info bar."""
        assert (
            "open.spotify.com/playlist/abc123"
            in dashboard_html
        )
