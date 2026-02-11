"""
Spotify URL and URI parser utility.

Extracts resource IDs from various Spotify URL and URI formats.
Supports web URLs, app URIs, and bare IDs.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Spotify playlist ID format: 22 alphanumeric characters
SPOTIFY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9]{22}$")

# Patterns for extracting playlist ID from various URL formats
_URL_PATTERNS = [
    # https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123
    # open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
    re.compile(
        r"(?:https?://)?open\.spotify\.com/playlist/([a-zA-Z0-9]{22})(?:\?.*)?$"
    ),
    # spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
    re.compile(r"^spotify:playlist:([a-zA-Z0-9]{22})$"),
]


def parse_spotify_playlist_url(input_string: str) -> Optional[str]:
    """
    Extract a Spotify playlist ID from a URL, URI, or bare ID.

    Supports these formats:
        - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
        - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123
        - open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
        - spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
        - 37i9dQZF1DXcBWIGoYBM5M  (bare ID)

    Args:
        input_string: The URL, URI, or ID to parse.

    Returns:
        The 22-character playlist ID, or None if the input
        does not match any known format.
    """
    if not input_string or not isinstance(input_string, str):
        return None

    cleaned = input_string.strip()
    if not cleaned:
        return None

    # Check if it is already a bare playlist ID
    if SPOTIFY_ID_PATTERN.match(cleaned):
        logger.debug(f"Parsed bare playlist ID: {cleaned}")
        return cleaned

    # Try each URL/URI pattern
    for pattern in _URL_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            playlist_id = match.group(1)
            logger.debug(f"Parsed playlist ID from URL/URI: {playlist_id}")
            return playlist_id

    logger.debug(f"Could not parse playlist ID from: {cleaned!r}")
    return None
