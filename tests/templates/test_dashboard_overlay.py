"""
Rendered Library rows in both Spotify playlist shapes: identity, track count and the
Spotify link survive, there is one explicit quick shuffle with disclosed method choices,
and live actions keep their CSRF forms and track limit. The row's structure (named list
items, Playlist actions, Organize) is proven on the running page in
tests/test_library_page_runtime.py.
"""

from unittest.mock import MagicMock, patch

import pytest

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

# A Spotify playlist object as /me/playlists returns it, in both response
# shapes: the track count is under "items" ("current") or "tracks" ("legacy").
_PLAYLIST = {
    "id": "playlist_abc123",
    "name": "Test Playlist",
    "images": [{"url": "https://img.example.com/1.jpg", "height": 300, "width": 300}],
    "external_urls": {"spotify": "https://open.spotify.com/playlist/abc123"},
}
PLAYLIST_SHAPES = {
    "current": {**_PLAYLIST, "items": {"href": "https://example.invalid/items", "total": 42}},
    "legacy": {**_PLAYLIST, "tracks": {"href": "https://example.invalid/tracks", "total": 42}},
}


@pytest.fixture(params=sorted(PLAYLIST_SHAPES))
def dashboard_html(auth_client, request):
    """Render the dashboard, once per playlist shape, and return the HTML."""
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
        mock_ps.get_user_playlists.return_value = [PLAYLIST_SHAPES[request.param]]
        mock_ps_class.return_value = mock_ps

        mock_shuffle_svc.list_algorithms.return_value = (
            MOCK_ALGORITHMS
        )
        mock_dash_svc.get_dashboard_data.return_value = {}

        resp = auth_client.get("/")
        assert resp.status_code == 200
        return resp.data.decode()


class TestLibraryCardRendering:
    """Keep playlist discovery and explicit writes separate in both API shapes."""

    def test_playlist_identity_and_spotify_link_survive(self, dashboard_html):
        assert "Test Playlist" in dashboard_html
        assert "42 tracks" in dashboard_html
        assert "open.spotify.com/playlist/abc123" in dashboard_html
        assert "spotify-logo-white.svg" in dashboard_html
        assert 'href="/workshop/playlist_abc123"' in dashboard_html

    def test_one_shuffle_action_and_disclosed_method_choices(self, dashboard_html):
        from html.parser import HTMLParser

        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = []

            def handle_starttag(self, tag, attrs):
                self.elements.append((tag, dict(attrs)))

        parsed = Controls()
        parsed.feed(dashboard_html)
        quick = [attrs for tag, attrs in parsed.elements if tag == "button"
                 and "library-quick-shuffle" in attrs.get("class", "").split()]
        assert len(quick) == 1
        options = [attrs for tag, attrs in parsed.elements if tag == "option"]
        assert {a["class_name"] for a in MOCK_ALGORITHMS} <= {
            attrs.get("value") for attrs in options
        }
        disclosures = [attrs for tag, attrs in parsed.elements if tag == "details"
                       and "library-shuffle-details" in attrs.get("class", "").split()]
        assert len(disclosures) == 1
        assert "open" not in disclosures[0]
        assert 'class="algo-icon-btn' not in dashboard_html
        assert 'class="algo-settings-btn' not in dashboard_html

    def test_live_actions_retain_csrf_forms_and_track_limit(self, dashboard_html):
        assert "shuffle-form" in dashboard_html
        assert "undo-form" in dashboard_html
        assert 'name="csrf_token"' in dashboard_html
        assert 'data-track-count="42"' in dashboard_html
        assert 'src="/static/js/library-shuffle.js"' in dashboard_html
