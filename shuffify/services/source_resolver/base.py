"""Base types for the source resolver package."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from shuffify.models.db import UpstreamSource

logger = logging.getLogger(__name__)


@dataclass
class ResolveResult:
    """Result of resolving a single source."""

    track_uris: List[str]
    pathway_name: str
    success: bool
    partial: bool = False
    error_message: Optional[str] = None


@dataclass
class ResolveAllResult:
    """Aggregated result from resolving multiple sources."""

    new_uris: List[str]
    source_results: List[Tuple[Any, ResolveResult]] = field(
        default_factory=list
    )


class ResolvePathway(Protocol):
    """Protocol for source resolution pathways."""

    @property
    def name(self) -> str: ...

    def can_handle(self, source: UpstreamSource) -> bool: ...

    def resolve(
        self, source: UpstreamSource, api: Any = None
    ) -> ResolveResult: ...
