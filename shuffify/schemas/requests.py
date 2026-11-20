"""
Request validation schemas using Pydantic.

Provides type-safe validation for all API request parameters.
"""

import re
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.spotify.api import MAX_PLAYLIST_TRACKS

TRACK_URI_RE = re.compile(r"^spotify:track:[a-zA-Z0-9]{22}$")


class ShuffleRequest(BaseModel):
    """
    Complete shuffle request schema.

    Validates both algorithm selection and algorithm-specific parameters.
    """

    algorithm: str = Field(
        default="BasicShuffle", description="Name of the shuffle algorithm to use"
    )

    # Common parameters (used by multiple algorithms)
    keep_first: Annotated[int, Field(ge=0)] = 0
    # Range and default come from the selected algorithm's `parameters`;
    # None means the request left it out.
    section_count: Optional[int] = None

    # PercentageShuffle specific
    shuffle_percentage: Annotated[float, Field(ge=0.0, le=100.0)] = 50.0
    shuffle_location: Literal["front", "back"] = "front"

    # ArtistSpacingShuffle specific
    min_spacing: Annotated[int, Field(ge=1, le=10)] = 1

    # AlbumSequenceShuffle specific
    shuffle_within_albums: Literal["no", "yes"] = "no"

    # NewestFirstShuffle specific
    jitter: Annotated[int, Field(ge=1, le=50)] = 5

    # Track locks: {position: track_uri} for locked tracks
    locked_positions: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Map of position (as string key) to track URI "
            "for tracks that should stay in place"
        ),
    )

    class Config:
        extra = "ignore"

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm_name(cls, v: str) -> str:
        """Ensure algorithm name is valid."""
        if not v or not v.strip():
            raise ValueError("Algorithm name cannot be empty")
        v = v.strip()
        valid_algorithms = set(ShuffleRegistry.get_available_algorithms().keys())
        if v not in valid_algorithms:
            raise ValueError(
                f"Invalid algorithm '{v}'. "
                f"Valid options: {', '.join(sorted(valid_algorithms))}"
            )
        return v

    @field_validator("section_count")
    @classmethod
    def validate_section_count(cls, v: Optional[int], info) -> Optional[int]:
        """Hold section_count to the range the selected algorithm declares."""
        algorithm = info.data.get("algorithm")
        if algorithm is None:  # the algorithm itself was refused
            return v
        declared = ShuffleRegistry.get_algorithm(algorithm)().parameters
        rule = declared.get("section_count")
        if rule is None:  # an algorithm without sections ignores the option
            return v
        # Only a sent value reaches this check, so None here is an explicit null.
        if v is None or not rule["min"] <= v <= rule["max"]:
            raise ValueError(
                f"{algorithm} takes {rule['min']} to {rule['max']} sections"
            )
        return v

    def get_algorithm_params(self) -> Dict[str, Any]:
        """
        Extract only the parameters the selected algorithm declares.

        A field left as None takes the algorithm's declared default.

        Returns:
            Dictionary of algorithm-specific parameters.
        """
        params = {}
        declared = ShuffleRegistry.get_algorithm(self.algorithm)().parameters
        for name, rule in declared.items():
            value = getattr(self, name)
            params[name] = rule["default"] if value is None else value
        return params


def parse_shuffle_request(form_data: Dict[str, Any]) -> ShuffleRequest:
    """
    Parse and validate shuffle request from form data.

    Handles type conversion from string form values.

    Args:
        form_data: Dictionary of form values (typically strings).

    Returns:
        Validated ShuffleRequest instance.

    Raises:
        ValidationError: If validation fails.
    """
    # Convert string values to appropriate types
    parsed = {}

    # Algorithm name
    if "algorithm" in form_data:
        parsed["algorithm"] = str(form_data["algorithm"])

    # Integer parameters
    for key in ["keep_first", "section_count", "min_spacing", "jitter"]:
        if key in form_data:
            try:
                parsed[key] = int(form_data[key])
            except (ValueError, TypeError):
                parsed[key] = form_data[key]  # Let Pydantic handle the error

    # Float parameters
    if "shuffle_percentage" in form_data:
        try:
            parsed["shuffle_percentage"] = float(form_data["shuffle_percentage"])
        except (ValueError, TypeError):
            parsed["shuffle_percentage"] = form_data["shuffle_percentage"]

    # String parameters
    for key in ["shuffle_location", "shuffle_within_albums"]:
        if key in form_data:
            parsed[key] = str(form_data[key])

    # Track locks: {position: track_uri}. Forwarded as-is so locked tracks
    # survive a preview shuffle; Pydantic validates the dict shape.
    if "locked_positions" in form_data:
        parsed["locked_positions"] = form_data["locked_positions"]

    return ShuffleRequest(**parsed)


class WorkshopCommitRequest(BaseModel):
    """Schema for committing workshop changes to Spotify."""

    baseline_track_uris: List[str] = Field(
        ...,
        min_length=0,
        max_length=MAX_PLAYLIST_TRACKS,
        description="Ordered track URIs when the draft was loaded",
    )
    track_uris: List[str] = Field(
        ...,
        min_length=0,
        max_length=MAX_PLAYLIST_TRACKS,
        description="Ordered list of track URIs to save",
    )

    @field_validator("baseline_track_uris", "track_uris")
    @classmethod
    def validate_track_uris(cls, v: List[str]) -> List[str]:
        """Ensure all URIs are valid Spotify track URIs."""
        for uri in v:
            if not TRACK_URI_RE.fullmatch(uri):
                raise ValueError(f"Invalid track URI format: {uri}")
        return v


class WorkshopSearchRequest(BaseModel):
    """Schema for searching Spotify's catalog from the workshop."""

    query: str = Field(
        ..., min_length=1, max_length=200, description="Search query string"
    )
    limit: Annotated[int, Field(ge=1, le=10)] = 10
    offset: Annotated[int, Field(ge=0)] = 0

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        """Ensure query is not just whitespace."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Search query cannot be empty or whitespace")
        return stripped

    class Config:
        extra = "ignore"


class ExternalPlaylistRequest(BaseModel):
    """Schema for loading an external playlist by URL or search query."""

    url: Optional[str] = Field(
        default=None,
        description="Spotify playlist URL, URI, or ID",
    )
    query: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Search query for finding playlists by name",
    )

    @field_validator("url")
    @classmethod
    def validate_url_not_empty(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace from URL if provided."""
        if v is not None:
            v = v.strip()
            if not v:
                return None
        return v

    @field_validator("query")
    @classmethod
    def validate_query_not_empty(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace from query if provided."""
        if v is not None:
            v = v.strip()
            if not v:
                return None
        return v

    def model_post_init(self, __context) -> None:
        """Ensure exactly one of url or query is provided."""
        if not self.url and not self.query:
            raise ValueError("Either 'url' or 'query' must be provided")
        if self.url and self.query:
            raise ValueError("Provide 'url' or 'query', not both")
