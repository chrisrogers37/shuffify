"""
Request validation schemas using Pydantic.

Provides type-safe validation for all API request parameters.
"""

from typing import Optional, Literal, Annotated, Any, Dict
from pydantic import BaseModel, Field, field_validator, model_validator


class ShuffleRequestBase(BaseModel):
    """Base schema for shuffle requests."""

    algorithm: str = Field(
        default="BasicShuffle",
        description="Name of the shuffle algorithm to use"
    )

    @field_validator('algorithm')
    @classmethod
    def validate_algorithm_name(cls, v: str) -> str:
        """Ensure algorithm name is not empty."""
        if not v or not v.strip():
            raise ValueError('Algorithm name cannot be empty')
        return v.strip()


class BasicShuffleParams(BaseModel):
    """Parameters for BasicShuffle algorithm."""

    keep_first: Annotated[int, Field(ge=0, default=0)] = 0

    class Config:
        extra = 'ignore'


class BalancedShuffleParams(BaseModel):
    """Parameters for BalancedShuffle algorithm."""

    keep_first: Annotated[int, Field(ge=0, default=0)] = 0
    section_count: Annotated[int, Field(ge=1, le=100, default=4)] = 4

    class Config:
        extra = 'ignore'


class StratifiedShuffleParams(BaseModel):
    """Parameters for StratifiedShuffle algorithm."""

    keep_first: Annotated[int, Field(ge=0, default=0)] = 0
    section_count: Annotated[int, Field(ge=1, le=100, default=5)] = 5

    class Config:
        extra = 'ignore'


class PercentageShuffleParams(BaseModel):
    """Parameters for PercentageShuffle algorithm."""

    shuffle_percentage: Annotated[float, Field(ge=0.0, le=100.0, default=50.0)] = 50.0
    shuffle_location: Literal['front', 'back'] = 'front'

    class Config:
        extra = 'ignore'


class ShuffleRequest(BaseModel):
    """
    Complete shuffle request schema.

    Validates both algorithm selection and algorithm-specific parameters.
    """

    algorithm: str = Field(
        default="BasicShuffle",
        description="Name of the shuffle algorithm to use"
    )

    # Common parameters (used by multiple algorithms)
    keep_first: Annotated[int, Field(ge=0)] = 0
    section_count: Annotated[int, Field(ge=1, le=100)] = 4

    # PercentageShuffle specific
    shuffle_percentage: Annotated[float, Field(ge=0.0, le=100.0)] = 50.0
    shuffle_location: Literal['front', 'back'] = 'front'

    class Config:
        extra = 'ignore'

    @field_validator('algorithm')
    @classmethod
    def validate_algorithm_name(cls, v: str) -> str:
        """Ensure algorithm name is valid."""
        valid_algorithms = {
            'BasicShuffle',
            'BalancedShuffle',
            'StratifiedShuffle',
            'PercentageShuffle'
        }
        if not v or not v.strip():
            raise ValueError('Algorithm name cannot be empty')
        v = v.strip()
        if v not in valid_algorithms:
            raise ValueError(
                f"Invalid algorithm '{v}'. "
                f"Valid options: {', '.join(sorted(valid_algorithms))}"
            )
        return v

    def get_algorithm_params(self) -> Dict[str, Any]:
        """
        Extract only the parameters relevant to the selected algorithm.

        Returns:
            Dictionary of algorithm-specific parameters.
        """
        if self.algorithm == 'BasicShuffle':
            return {'keep_first': self.keep_first}
        elif self.algorithm == 'BalancedShuffle':
            return {
                'keep_first': self.keep_first,
                'section_count': self.section_count
            }
        elif self.algorithm == 'StratifiedShuffle':
            return {
                'keep_first': self.keep_first,
                'section_count': self.section_count
            }
        elif self.algorithm == 'PercentageShuffle':
            return {
                'shuffle_percentage': self.shuffle_percentage,
                'shuffle_location': self.shuffle_location
            }
        else:
            return {}


class PlaylistQueryParams(BaseModel):
    """Query parameters for playlist endpoints."""

    features: bool = Field(
        default=False,
        description="Whether to include audio features for tracks"
    )

    @field_validator('features', mode='before')
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        """Parse boolean from string query parameter."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ('true', 't', 'yes', 'y', '1')
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
    if 'algorithm' in form_data:
        parsed['algorithm'] = str(form_data['algorithm'])

    # Integer parameters
    for key in ['keep_first', 'section_count']:
        if key in form_data:
            try:
                parsed[key] = int(form_data[key])
            except (ValueError, TypeError):
                parsed[key] = form_data[key]  # Let Pydantic handle the error

    # Float parameters
    if 'shuffle_percentage' in form_data:
        try:
            parsed['shuffle_percentage'] = float(form_data['shuffle_percentage'])
        except (ValueError, TypeError):
            parsed['shuffle_percentage'] = form_data['shuffle_percentage']

    # String parameters
    if 'shuffle_location' in form_data:
        parsed['shuffle_location'] = str(form_data['shuffle_location'])

    return ShuffleRequest(**parsed)
