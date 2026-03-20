"""Metadata tracks pathway — extracts tracks from playlist metadata endpoint."""

import logging

from shuffify.spotify.exceptions import SpotifyNotFoundError
from .base import ResolveResult

logger = logging.getLogger(__name__)

HANDLED_SOURCE_TYPES = ("own", "external")


class MetadataTracksPathway:
    """Pathway 1b: Extract tracks via GET /playlists/{id}.

    Uses the playlist metadata endpoint which embeds the first page of
    tracks and allows pagination via ``next`` URLs.  Unlike the
    ``/items`` endpoint this is NOT restricted to owners/collaborators
    and works for any public playlist.

    Sits between DirectAPIPathway (which calls the restricted ``/items``
    endpoint) and PublicScraperPathway in the resolution chain.
    """

    @property
    def name(self) -> str:
        return "metadata_tracks"

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
            tracks = api.get_playlist_tracks_via_metadata(
                playlist_id
            )
        except SpotifyNotFoundError:
            raise
        except Exception as e:
            logger.warning(
                "MetadataTracks failed for %s: %s",
                playlist_id,
                e,
            )
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
                error_message=str(e),
            )

        uris = [t["uri"] for t in tracks if t.get("uri")]

        if not uris:
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
