"""Where Spotify lives: the base of every Spotify URL the app builds.

``SPOTIFY_BASE_URL`` points the Web API, OAuth and the public pages the
playlist scraper reads at one local origin -- the test harness's fake Spotify
-- with their paths unchanged. Only a loopback origin is accepted, because the
OAuth token endpoint receives the client secret; anything else fails at
import. Unset, every URL is the production one. Resolved once, at import, into
the constants below. See documentation/guides/testing-harness.md.
"""

import ipaddress
import os
from typing import Optional
from urllib.parse import urlsplit


class SpotifyBaseURLError(RuntimeError):
    """SPOTIFY_BASE_URL is not a bare loopback origin."""


def is_loopback_host(host: str) -> bool:
    """True for ``localhost`` or a loopback IP literal.

    A name merely starting with ``127.`` is not loopback: ``127.example.com``
    resolves wherever its owner says.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def local_origin(base_url: Optional[str]) -> Optional[str]:
    """The validated origin a SPOTIFY_BASE_URL value names, or None when unset."""
    if not base_url:
        return None
    value = base_url.strip().rstrip("/")
    parts = urlsplit(value)
    host = parts.hostname or ""
    try:
        port = parts.port  # ValueError for a non-numeric or out-of-range port
    except ValueError:
        port = -1
    # The origin must rebuild exactly from its parsed host and port, so
    # nothing else -- credentials, a path, stray characters that would land
    # in the CSP header -- can ride along. An IPv6 zone ID ("%...") is part of
    # the host yet may hold any characters, so it is refused outright.
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port is not None else "")
    if (
        parts.scheme not in ("http", "https")
        or not is_loopback_host(host)
        or "%" in host
        or port == -1
        or value != f"{parts.scheme}://{netloc}"
    ):
        raise SpotifyBaseURLError(
            "SPOTIFY_BASE_URL must be a bare loopback origin such as http://127.0.0.1:8900 "
            f"(the OAuth token endpoint receives the client secret), got {base_url!r}"
        )
    return value


_origin = local_origin(os.environ.get("SPOTIFY_BASE_URL"))

API_BASE_URL = f"{_origin}/v1" if _origin else "https://api.spotify.com/v1"
ACCOUNTS_BASE_URL = _origin or "https://accounts.spotify.com"
OPEN_BASE_URL = _origin or "https://open.spotify.com"
