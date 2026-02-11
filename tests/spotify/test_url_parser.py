"""
Tests for the Spotify URL parser utility.

Covers all supported URL formats, edge cases, and invalid inputs.
"""

import pytest

from shuffify.spotify.url_parser import parse_spotify_playlist_url


class TestParseSpotifyPlaylistUrl:
    """Tests for parse_spotify_playlist_url()."""

    # =========================================================================
    # Valid URL formats
    # =========================================================================

    def test_full_https_url(self):
        """Standard HTTPS URL should extract playlist ID."""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_https_url_with_query_params(self):
        """URL with query parameters should extract playlist ID."""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123"
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_url_without_protocol(self):
        """URL without https:// should still work."""
        url = "open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_http_url(self):
        """HTTP (non-HTTPS) URL should also work."""
        url = "http://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_spotify_uri(self):
        """Spotify URI format should extract playlist ID."""
        uri = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(uri) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_bare_playlist_id(self):
        """A bare 22-character ID should be returned as-is."""
        bare_id = "37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(bare_id) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_url_with_leading_trailing_whitespace(self):
        """Whitespace around a URL should be stripped."""
        url = "  https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M  "
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_uri_with_whitespace(self):
        """Whitespace around a URI should be stripped."""
        uri = "  spotify:playlist:37i9dQZF1DXcBWIGoYBM5M  "
        assert parse_spotify_playlist_url(uri) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_url_with_multiple_query_params(self):
        """URL with multiple query params should extract ID."""
        url = (
            "https://open.spotify.com/playlist/"
            "37i9dQZF1DXcBWIGoYBM5M?si=abc&dl=true"
        )
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    # =========================================================================
    # Invalid inputs
    # =========================================================================

    def test_none_returns_none(self):
        """None input should return None."""
        assert parse_spotify_playlist_url(None) is None

    def test_empty_string_returns_none(self):
        """Empty string should return None."""
        assert parse_spotify_playlist_url("") is None

    def test_whitespace_only_returns_none(self):
        """Whitespace-only string should return None."""
        assert parse_spotify_playlist_url("   ") is None

    def test_random_string_returns_none(self):
        """Random text should return None."""
        assert parse_spotify_playlist_url("not a spotify url") is None

    def test_track_url_returns_none(self):
        """A Spotify track URL should return None (not a playlist)."""
        url = "https://open.spotify.com/track/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) is None

    def test_album_url_returns_none(self):
        """A Spotify album URL should return None (not a playlist)."""
        url = "https://open.spotify.com/album/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) is None

    def test_artist_url_returns_none(self):
        """A Spotify artist URL should return None (not a playlist)."""
        url = "https://open.spotify.com/artist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) is None

    def test_track_uri_returns_none(self):
        """A Spotify track URI should return None."""
        uri = "spotify:track:37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(uri) is None

    def test_short_id_returns_none(self):
        """An ID shorter than 22 characters should return None."""
        assert parse_spotify_playlist_url("abc123") is None

    def test_long_id_returns_none(self):
        """An ID longer than 22 characters should return None."""
        assert parse_spotify_playlist_url("a" * 23) is None

    def test_id_with_special_chars_returns_none(self):
        """An ID with special characters should return None."""
        assert parse_spotify_playlist_url("37i9dQZF1DXcBWIGoYBM_!") is None

    def test_integer_returns_none(self):
        """Non-string input should return None."""
        assert parse_spotify_playlist_url(12345) is None

    def test_boolean_returns_none(self):
        """Boolean input should return None."""
        assert parse_spotify_playlist_url(True) is None

    # =========================================================================
    # Different valid playlist IDs
    # =========================================================================

    def test_another_valid_id_from_url(self):
        """Verify with a different playlist ID format."""
        url = "https://open.spotify.com/playlist/5ABHKGoOzxkaa28ttQV9sE"
        assert parse_spotify_playlist_url(url) == "5ABHKGoOzxkaa28ttQV9sE"

    def test_numeric_heavy_id(self):
        """Verify IDs with mostly digits work."""
        bare_id = "1234567890abcDEFGH1234"
        assert parse_spotify_playlist_url(bare_id) == "1234567890abcDEFGH1234"
