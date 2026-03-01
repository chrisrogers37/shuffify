"""Direct API pathway — resolves tracks via Spotify Web API."""

import logging

from shuffify.spotify.exceptions import SpotifyNotFoundError
from .base import ResolveResult

logger = logging.getLogger(__name__)

HANDLED_SOURCE_TYPES = ("own", "external")


class DirectAPIPathway:
    """Pathway 1: Fetch tracks directly from Spotify API.

    Works for playlists the user owns or collaborates on.
    After Feb 2026, external playlists return empty items.
    """

    @property
    def name(self) -> str:
        return "direct_api"

    def can_handle(self, source) -> bool:
        return source.source_type in HANDLED_SOURCE_TYPES

    def resolve(self, source, api=None) -> ResolveResult:
        if api is None:
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
                error_message="No API client provided",
            )

        playlist_id = source.source_playlist_id
        if not playlist_id:
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
                error_message="No playlist ID on source",
            )

        try:
            tracks = api.get_playlist_tracks(playlist_id)
        except SpotifyNotFoundError:
            raise
        except Exception as e:
            logger.warning(
                "DirectAPI failed for %s: %s", playlist_id, e
            )
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
                error_message=str(e),
            )

        uris = [
            t["uri"] for t in tracks if t.get("uri")
        ]

        if not uris:
            # Empty result — likely the Feb 2026 restriction
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
            )

        return ResolveResult(
            track_uris=uris,
            pathway_name=self.name,
            success=True,
        )
