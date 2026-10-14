"""Every Spotify URL shuffify builds: where it is bound, and its path.

Each is a module-level name bound once, at import, from
shuffify/spotify/endpoints.py. test_spotify_endpoints reads them all to prove
SPOTIFY_BASE_URL moves every one; the fake-Spotify contract tests repoint
them all at the fake. A Spotify URL added to shuffify belongs here, or the
contract tests would send it to the real host.
"""

import importlib

# name: (module, attribute -- dotted through a class --, path after the origin)
SPOTIFY_URLS = {
    "api": ("shuffify.spotify.http_client", "BASE_URL", "/v1"),
    "authorize": ("shuffify.spotify.auth", "SpotifyAuthManager._AUTHORIZE_URL", "/authorize"),
    "token": ("shuffify.spotify.auth", "SpotifyAuthManager._TOKEN_URL", "/api/token"),
    "revoke": ("shuffify.spotify.auth", "SpotifyAuthManager._REVOKE_URL", "/api/revoke"),
    "scraper_embed": ("shuffify.services.source_resolver.public_scraper_pathway", "EMBED_URL",
                      "/embed/playlist/{playlist_id}"),
    "scraper_public": ("shuffify.services.source_resolver.public_scraper_pathway", "PUBLIC_URL",
                       "/playlist/{playlist_id}"),
    "metadata_embed": ("shuffify.services.playlist_service", "_EMBED_URL", "/embed/playlist/{playlist_id}"),
}


def _owner(module: str, attribute: str):
    owner = importlib.import_module(module)
    *path, name = attribute.split(".")
    for part in path:
        owner = getattr(owner, part)
    return owner, name


def current() -> dict:
    """Each URL as shuffify has it bound right now."""
    return {key: getattr(*_owner(module, attr)) for key, (module, attr, _) in SPOTIFY_URLS.items()}


def repoint(monkeypatch, origin: str) -> None:
    """Point every URL at ``origin``, as SPOTIFY_BASE_URL would at import."""
    for module, attr, path in SPOTIFY_URLS.values():
        monkeypatch.setattr(*_owner(module, attr), origin + path)
