"""
Request validation schemas using Pydantic.

Provides type-safe validation for all API request parameters.
"""

from typing import Literal, Annotated, Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

from shuffify.shuffle_algorithms.registry import ShuffleRegistry


class ShuffleRequestBase(BaseModel):
    """Base schema for shuffle requests."""

    algorithm: str = Field(
        default="BasicShuffle", description="Name of the shuffle algorithm to use"
    )

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm_name(cls, v: str) -> str:
        """Ensure algorithm name is not empty."""
        if not v or not v.strip():
            raise ValueError("Algorithm name cannot be empty")
        return v.strip()


class BasicShuffleParams(BaseModel):
    """Parameters for BasicShuffle algorithm."""

    keep_first: Annotated[int, Field(ge=0, default=0)] = 0

    class Config:
        extra = "ignore"


class BalancedShuffleParams(BaseModel):
    """Parameters for BalancedShuffle algorithm."""

    keep_first: Annotated[int, Field(ge=0, default=0)] = 0
    section_count: Annotated[int, Field(ge=1, le=100, default=4)] = 4

    class Config:
        extra = "ignore"


class StratifiedShuffleParams(BaseModel):
    """Parameters for StratifiedShuffle algorithm."""

    keep_first: Annotated[int, Field(ge=0, default=0)] = 0
    section_count: Annotated[int, Field(ge=1, le=100, default=5)] = 5

    class Config:
        extra = "ignore"


class PercentageShuffleParams(BaseModel):
    """Parameters for PercentageShuffle algorithm."""

    shuffle_percentage: Annotated[float, Field(ge=0.0, le=100.0, default=50.0)] = 50.0
    shuffle_location: Literal["front", "back"] = "front"

    class Config:
        extra = "ignore"


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
    section_count: Annotated[int, Field(ge=1, le=100)] = 4

    # PercentageShuffle specific
    shuffle_percentage: Annotated[float, Field(ge=0.0, le=100.0)] = 50.0
    shuffle_location: Literal["front", "back"] = "front"

    # ArtistSpacingShuffle specific
    min_spacing: Annotated[int, Field(ge=1, le=10)] = 1

    # AlbumSequenceShuffle specific
    shuffle_within_albums: Literal["no", "yes"] = "no"

    # TempoGradientShuffle specific
    direction: Literal["ascending", "descending"] = "ascending"

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

    # Maps algorithm names to the list of parameter field names they use.
    # When adding a new algorithm, add an entry here with its parameter names.
    _ALGORITHM_PARAMS = {
        "BasicShuffle": ["keep_first"],
        "BalancedShuffle": ["keep_first", "section_count"],
        "StratifiedShuffle": ["keep_first", "section_count"],
        "PercentageShuffle": ["shuffle_percentage", "shuffle_location"],
        "ArtistSpacingShuffle": ["min_spacing"],
        "AlbumSequenceShuffle": ["shuffle_within_albums"],
        "TempoGradientShuffle": ["direction"],
    }

    def get_algorithm_params(self) -> Dict[str, Any]:
        """
        Extract only the parameters relevant to the selected algorithm.

        Returns:
            Dictionary of algorithm-specific parameters.
        """
        param_names = self._ALGORITHM_PARAMS.get(self.algorithm, [])
        return {name: getattr(self, name) for name in param_names}


class PlaylistQueryParams(BaseModel):
    """Query parameters for playlist endpoints."""

    features: bool = Field(
        default=False, description="Whether to include audio features for tracks"
    )

    @field_validator("features", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        """Parse boolean from string query parameter."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "t", "yes", "y", "1")
        return bool(v)


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
    for key in ["keep_first", "section_count", "min_spacing"]:
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
    for key in ["shuffle_location", "shuffle_within_albums", "direction"]:
        if key in form_data:
            parsed[key] = str(form_data[key])

    return ShuffleRequest(**parsed)


class WorkshopCommitRequest(BaseModel):
    """Schema for committing workshop changes to Spotify."""

    track_uris: List[str] = Field(
        ..., min_length=0, description="Ordered list of track URIs to save"
    )

    @field_validator("track_uris")
    @classmethod
    def validate_track_uris(cls, v: List[str]) -> List[str]:
        """Ensure all URIs look like Spotify track URIs."""
        for uri in v:
            if not uri.startswith("spotify:track:"):
                raise ValueError(f"Invalid track URI format: {uri}")
        return v


class WorkshopSearchRequest(BaseModel):
    """Schema for searching Spotify's catalog from the workshop."""

    query: str = Field(
        ..., min_length=1, max_length=200, description="Search query string"
    )
    limit: Annotated[int, Field(ge=1, le=50)] = 20
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
        """Ensure at least one of url or query is provided."""
        if not self.url and not self.query:
            raise ValueError(
                "Either 'url' or 'query' must be provided"
            )
