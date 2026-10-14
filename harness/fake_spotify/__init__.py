"""A local fake of the Spotify Web API, OAuth and public pages, for the harness.

    python -m harness.fake_spotify --port 8900 --redirect-uri http://127.0.0.1:8000/callback

scripts/harness.sh starts it, and the app reaches it through SPOTIFY_BASE_URL.
See documentation/guides/testing-harness.md.
"""

from .app import create_app
from .store import Store, spotify_id

__all__ = ["Store", "create_app", "spotify_id"]
