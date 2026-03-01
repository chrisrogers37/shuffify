"""Source resolver — orchestrates pathways in priority order."""

import logging
from typing import Any, List, Optional, Set

from .base import ResolveAllResult, ResolvePathway, ResolveResult
from .direct_api_pathway import DirectAPIPathway
from .search_pathway import SearchPathway

logger = logging.getLogger(__name__)


class SourceResolver:
    """Resolves upstream sources to track URIs using multiple pathways.

    Stateless: returns results only. The caller is responsible
    for persisting any tracking data (last_resolved_at, etc.).
    """

    def __init__(
        self,
        pathways: Optional[List[ResolvePathway]] = None,
    ):
        if pathways is not None:
            self._pathways = pathways
        else:
            self._pathways = self._default_pathways()

    @staticmethod
    def _default_pathways() -> List[ResolvePathway]:
        """Default pathway chain in priority order."""
        return [
            DirectAPIPathway(),
            SearchPathway(),
        ]

    def resolve(self, source: Any, api: Any = None) -> ResolveResult:
        """Try each applicable pathway until one succeeds."""
        for pathway in self._pathways:
            if not pathway.can_handle(source):
                continue

            result = pathway.resolve(source, api=api)
            if result.success or result.partial:
                logger.info(
                    "Resolved source %s via %s (%d tracks)",
                    getattr(source, "source_playlist_id", "?"),
                    result.pathway_name,
                    len(result.track_uris),
                )
                return result

            logger.debug(
                "Pathway %s returned no results for %s",
                pathway.name,
                getattr(source, "source_playlist_id", "?"),
            )

        # All pathways exhausted
        return ResolveResult(
            track_uris=[],
            pathway_name="none",
            success=False,
            error_message="All pathways exhausted",
        )

    def resolve_all(
        self,
        sources: List[Any],
        api: Any = None,
        exclude_uris: Optional[Set[str]] = None,
    ) -> ResolveAllResult:
        """Resolve all sources, deduplicating against exclude_uris."""
        if exclude_uris is None:
            exclude_uris = set()

        seen: Set[str] = set(exclude_uris)
        new_uris: List[str] = []
        source_results = []

        for source in sources:
            result = self.resolve(source, api=api)
            source_results.append((source, result))

            for uri in result.track_uris:
                if uri not in seen:
                    seen.add(uri)
                    new_uris.append(uri)

        return ResolveAllResult(
            new_uris=new_uris,
            source_results=source_results,
        )
