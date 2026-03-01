"""Search pathway — resolves tracks via Spotify search API."""

import logging

from .base import ResolveResult

logger = logging.getLogger(__name__)

MAX_PAGES = 2
PAGE_SIZE = 10


class SearchPathway:
    """Pathway 2: Discover tracks via Spotify search.

    Uses the search_query field on UpstreamSource to find tracks.
    Results are inherently a subset (partial=True) since search
    returns relevance-ranked results, not a complete playlist.
    """

    @property
    def name(self) -> str:
        return "search"

    def can_handle(self, source) -> bool:
        return source.source_type == "search_query"

    def resolve(self, source, api=None) -> ResolveResult:
        if api is None:
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
                error_message="No API client provided",
            )

        query = getattr(source, "search_query", None)
        if not query:
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
                error_message="No search query configured",
            )

        all_uris = []
        seen = set()

        try:
            for page in range(MAX_PAGES):
                tracks = api.search_tracks(
                    query=query,
                    limit=PAGE_SIZE,
                    offset=page * PAGE_SIZE,
                )

                if not tracks:
                    break

                for t in tracks:
                    uri = t.get("uri")
                    if uri and uri not in seen:
                        seen.add(uri)
                        all_uris.append(uri)
        except Exception as e:
            logger.warning(
                "Search failed for query '%s': %s", query, e
            )
            if all_uris:
                return ResolveResult(
                    track_uris=all_uris,
                    pathway_name=self.name,
                    success=False,
                    partial=True,
                    error_message=str(e),
                )
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
                error_message=str(e),
            )

        if not all_uris:
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
            )

        return ResolveResult(
            track_uris=all_uris,
            pathway_name=self.name,
            success=True,
            partial=True,
        )
