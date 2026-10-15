"""Explicit API paths for requests and fixtures."""

import pytest

from tests.paths import api_path


@pytest.mark.parametrize("path,expected", [
    ("/playlist/example", "/api/playlist/example"),
    ("/api/user-playlists", "/api/user-playlists"),
    ("/api", "/api"),
    ("/apiary", "/api/apiary"),
    ("/playlist/example?refresh=1&next=%2Fsettings", "/api/playlist/example?refresh=1&next=%2Fsettings"),
])
def test_api_path_preserves_queries_and_is_idempotent(path, expected):
    assert api_path(path) == expected
    assert api_path(api_path(path)) == expected


@pytest.mark.parametrize("path", [
    "https://example.invalid/playlist/example", "//example.invalid/path", "relative/path",
])
def test_api_path_rejects_external_or_relative_urls(path):
    with pytest.raises(ValueError):
        api_path(path)
