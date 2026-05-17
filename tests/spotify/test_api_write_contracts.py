"""
Tests for SpotifyAPI write-path contracts (F4).

Verifies that `update_playlist_tracks`, `playlist_add_items`, and
`playlist_remove_items` raise `SpotifyPartialBatchError` on per-batch
HTTP failure, with the diagnostic attributes the rollback path
relies on. Also verifies that the cache is invalidated when a write
fails *after* the playlist has already been mutated, so subsequent
reads don't return stale URIs.
"""

import time
from unittest.mock import patch, MagicMock

import pytest

from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.cache import SpotifyCache
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyPartialBatchError,
)


@pytest.fixture
def auth_manager():
    return SpotifyAuthManager(
        SpotifyCredentials(
            client_id="test_id",
            client_secret="test_secret",
            redirect_uri="http://localhost/callback",
        )
    )


@pytest.fixture
def token_info():
    return TokenInfo(
        access_token="test_token",
        token_type="Bearer",
        expires_at=time.time() + 3600,
        refresh_token="refresh",
    )


def _three_batches():
    """250 URIs split into 3 batches of 100/100/50."""
    return [f"spotify:track:{i:03d}" for i in range(250)]


# ---------------------------------------------------------------------------
# update_playlist_tracks
# ---------------------------------------------------------------------------

class TestUpdatePlaylistTracksSuccess:
    def test_returns_true_on_success(self, token_info, auth_manager):
        uris = ["spotify:track:1", "spotify:track:2"]
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None

            api = SpotifyAPI(token_info, auth_manager)
            assert api.update_playlist_tracks("p1", uris) is True

    def test_multi_batch_success_no_raise(
        self, token_info, auth_manager
    ):
        uris = _three_batches()
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None
            mock_http.post.return_value = None

            api = SpotifyAPI(token_info, auth_manager)
            assert api.update_playlist_tracks("p1", uris) is True
            assert mock_http.put.call_count == 1
            # Two POSTs for the remaining 150 tracks.
            assert mock_http.post.call_count == 2


class TestUpdatePlaylistTracksPutFailure:
    def test_put_failure_raises_partial_batch(
        self, token_info, auth_manager
    ):
        uris = _three_batches()
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.side_effect = SpotifyAPIError(
                "API error 502: bad gateway"
            )

            api = SpotifyAPI(token_info, auth_manager)
            with pytest.raises(SpotifyPartialBatchError) as exc_info:
                api.update_playlist_tracks("p1", uris)

            exc = exc_info.value
            assert exc.method == "update"
            assert exc.playlist_id == "p1"
            assert exc.completed_batches == 0
            assert exc.total_batches == 3
            assert exc.completed_uris == []
            assert exc.remaining_uris == uris
            assert isinstance(exc.cause, SpotifyAPIError)
            # PUT never succeeded — no POST attempts.
            assert mock_http.post.call_count == 0


class TestUpdatePlaylistTracksPostFailure:
    def test_first_post_failure_raises_partial_batch(
        self, token_info, auth_manager
    ):
        uris = _three_batches()
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None
            mock_http.post.side_effect = SpotifyAPIError(
                "API error 500"
            )

            api = SpotifyAPI(token_info, auth_manager)
            with pytest.raises(SpotifyPartialBatchError) as exc_info:
                api.update_playlist_tracks("p1", uris)

            exc = exc_info.value
            assert exc.completed_batches == 1
            assert exc.total_batches == 3
            assert exc.completed_uris == uris[:100]
            assert exc.remaining_uris == uris[100:]

    def test_last_post_failure_raises_partial_batch(
        self, token_info, auth_manager
    ):
        uris = _three_batches()
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None
            # First POST OK, second fails.
            mock_http.post.side_effect = [
                None,
                SpotifyAPIError("API error 503"),
            ]

            api = SpotifyAPI(token_info, auth_manager)
            with pytest.raises(SpotifyPartialBatchError) as exc_info:
                api.update_playlist_tracks("p1", uris)

            exc = exc_info.value
            assert exc.completed_batches == 2
            assert exc.total_batches == 3
            assert exc.completed_uris == uris[:200]
            assert exc.remaining_uris == uris[200:]


class TestUpdatePlaylistTracksCacheInvalidationOnFailure:
    def test_cache_invalidated_when_failure_follows_successful_put(
        self, token_info, auth_manager
    ):
        """PUT mutates the playlist; if a subsequent POST fails,
        the cache must be invalidated so the half-written state
        doesn't get served from cache.
        """
        uris = _three_batches()
        cache = MagicMock(spec=SpotifyCache)
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None
            mock_http.post.side_effect = SpotifyAPIError("boom")

            api = SpotifyAPI(token_info, auth_manager, cache=cache)
            with pytest.raises(SpotifyPartialBatchError):
                api.update_playlist_tracks("p1", uris)

            cache.invalidate_playlist.assert_called_with("p1")

    def test_cache_not_invalidated_when_put_itself_fails(
        self, token_info, auth_manager
    ):
        """If the PUT failed, the playlist wasn't mutated — no
        cache invalidation needed.
        """
        uris = _three_batches()
        cache = MagicMock(spec=SpotifyCache)
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.side_effect = SpotifyAPIError("boom")

            api = SpotifyAPI(token_info, auth_manager, cache=cache)
            with pytest.raises(SpotifyPartialBatchError):
                api.update_playlist_tracks("p1", uris)

            cache.invalidate_playlist.assert_not_called()


# ---------------------------------------------------------------------------
# playlist_add_items
# ---------------------------------------------------------------------------

class TestPlaylistAddItemsContract:
    def test_returns_true_on_success(self, token_info, auth_manager):
        uris = ["spotify:track:1", "spotify:track:2"]
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.post.return_value = None

            api = SpotifyAPI(token_info, auth_manager)
            assert api.playlist_add_items("p1", uris) is True

    def test_empty_uris_returns_true_without_call(
        self, token_info, auth_manager
    ):
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            api = SpotifyAPI(token_info, auth_manager)
            assert api.playlist_add_items("p1", []) is True
            mock_http.post.assert_not_called()

    def test_first_batch_failure_raises_partial_batch(
        self, token_info, auth_manager
    ):
        uris = _three_batches()
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.post.side_effect = SpotifyAPIError("502")

            api = SpotifyAPI(token_info, auth_manager)
            with pytest.raises(SpotifyPartialBatchError) as exc_info:
                api.playlist_add_items("p1", uris)

            exc = exc_info.value
            assert exc.method == "add"
            assert exc.completed_batches == 0
            assert exc.total_batches == 3
            assert exc.completed_uris == []
            assert exc.remaining_uris == uris

    def test_mid_batch_failure_raises_partial_batch(
        self, token_info, auth_manager
    ):
        uris = _three_batches()
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.post.side_effect = [
                None,
                SpotifyAPIError("502"),
            ]

            api = SpotifyAPI(token_info, auth_manager)
            with pytest.raises(SpotifyPartialBatchError) as exc_info:
                api.playlist_add_items("p1", uris)

            exc = exc_info.value
            assert exc.completed_batches == 1
            assert exc.completed_uris == uris[:100]
            assert exc.remaining_uris == uris[100:]


# ---------------------------------------------------------------------------
# playlist_remove_items
# ---------------------------------------------------------------------------

class TestPlaylistRemoveItemsContract:
    def test_returns_true_on_success(self, token_info, auth_manager):
        uris = ["spotify:track:1", "spotify:track:2"]
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.delete.return_value = None

            api = SpotifyAPI(token_info, auth_manager)
            assert api.playlist_remove_items("p1", uris) is True

    def test_http_failure_raises_partial_batch(
        self, token_info, auth_manager
    ):
        uris = _three_batches()
        with patch("shuffify.spotify.api.SpotifyHTTPClient") as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.delete.side_effect = [
                None,
                SpotifyAPIError("500"),
            ]

            api = SpotifyAPI(token_info, auth_manager)
            with pytest.raises(SpotifyPartialBatchError) as exc_info:
                api.playlist_remove_items("p1", uris)

            exc = exc_info.value
            assert exc.method == "remove"
            assert exc.completed_batches == 1
            assert exc.total_batches == 3
            assert exc.completed_uris == uris[:100]
            assert exc.remaining_uris == uris[100:]


# ---------------------------------------------------------------------------
# Exception attribute carrying
# ---------------------------------------------------------------------------

class TestSpotifyPartialBatchErrorAttributes:
    def test_carries_full_diagnostic(self):
        cause = SpotifyAPIError("API error 500: Server error")
        exc = SpotifyPartialBatchError(
            playlist_id="abc",
            method="update",
            completed_batches=1,
            total_batches=3,
            completed_uris=["spotify:track:1"],
            remaining_uris=["spotify:track:2", "spotify:track:3"],
            cause=cause,
        )

        assert exc.playlist_id == "abc"
        assert exc.method == "update"
        assert exc.completed_batches == 1
        assert exc.total_batches == 3
        assert exc.completed_uris == ["spotify:track:1"]
        assert exc.remaining_uris == [
            "spotify:track:2",
            "spotify:track:3",
        ]
        assert exc.cause is cause
        # The message summarizes the failure for log lines /
        # error_message storage.
        s = str(exc)
        assert "abc" in s
        assert "1/3" in s
        assert "update" in s
