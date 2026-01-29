"""
Shuffle service for managing playlist shuffle operations.

Handles algorithm selection, parameter parsing, and shuffle execution.
"""

import logging
from typing import Dict, List, Any, Optional, Type

from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.spotify.client import SpotifyClient

logger = logging.getLogger(__name__)


class ShuffleError(Exception):
    """Base exception for shuffle operations."""
    pass


class InvalidAlgorithmError(ShuffleError):
    """Raised when an invalid algorithm is requested."""
    pass


class ParameterValidationError(ShuffleError):
    """Raised when algorithm parameters are invalid."""
    pass


class ShuffleExecutionError(ShuffleError):
    """Raised when shuffle execution fails."""
    pass


class ShuffleService:
    """Service for managing shuffle algorithm execution."""

    # Type conversion mapping for algorithm parameters
    TYPE_CONVERTERS = {
        'integer': int,
        'float': float,
        'boolean': lambda v: str(v).lower() in ('true', 't', 'yes', 'y', '1'),
        'string': str,
    }

    @staticmethod
    def list_algorithms() -> List[Dict[str, Any]]:
        """
        Get all available shuffle algorithms.

        Returns:
            List of algorithm metadata dictionaries.
        """
        return ShuffleRegistry.list_algorithms()

    @staticmethod
    def get_algorithm(name: str) -> Any:
        """
        Get an algorithm instance by name.

        Args:
            name: The algorithm name (e.g., 'BasicShuffle').

        Returns:
            An instantiated algorithm object.

        Raises:
            InvalidAlgorithmError: If the algorithm doesn't exist.
        """
        try:
            algorithm_class = ShuffleRegistry.get_algorithm(name)
            return algorithm_class()
        except ValueError as e:
            logger.error(f"Invalid algorithm requested: {name}")
            raise InvalidAlgorithmError(f"Unknown algorithm: {name}")

    @staticmethod
    def parse_parameters(
        algorithm: Any,
        form_data: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Parse and validate algorithm parameters from form data.

        Converts string form values to the appropriate types based on
        the algorithm's parameter definitions.

        Args:
            algorithm: An algorithm instance with a 'parameters' property.
            form_data: Dictionary of string values from a form submission.

        Returns:
            Dictionary of parsed parameters with correct types.

        Raises:
            ParameterValidationError: If a parameter cannot be converted.
        """
        params = {}

        for param_name, param_info in algorithm.parameters.items():
            if param_name not in form_data:
                continue

            value = form_data[param_name]
            param_type = param_info.get('type', 'string')

            try:
                converter = ShuffleService.TYPE_CONVERTERS.get(param_type, str)
                params[param_name] = converter(value)
            except (ValueError, TypeError) as e:
                logger.error(f"Failed to convert parameter '{param_name}' to {param_type}: {e}")
                raise ParameterValidationError(
                    f"Invalid value for parameter '{param_name}': expected {param_type}"
                )

        logger.debug(f"Parsed parameters: {params}")
        return params

    @staticmethod
    def execute(
        algorithm_name: str,
        tracks: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
        spotify_client: Optional[SpotifyClient] = None
    ) -> List[str]:
        """
        Execute a shuffle algorithm on a list of tracks.

        Args:
            algorithm_name: The name of the algorithm to use.
            tracks: List of track dictionaries with at least 'uri' key.
            params: Optional algorithm parameters.
            spotify_client: Optional SpotifyClient for algorithms that need it.

        Returns:
            List of track URIs in the new shuffled order.

        Raises:
            InvalidAlgorithmError: If the algorithm doesn't exist.
            ShuffleExecutionError: If shuffle execution fails.
        """
        params = params or {}

        try:
            algorithm = ShuffleService.get_algorithm(algorithm_name)

            # Some algorithms accept a Spotify client for additional features
            if spotify_client:
                params['sp'] = spotify_client

            shuffled_uris = algorithm.shuffle(tracks, **params)

            logger.info(f"Executed {algorithm_name} on {len(tracks)} tracks")
            return shuffled_uris

        except InvalidAlgorithmError:
            raise
        except Exception as e:
            logger.error(f"Shuffle execution failed: {e}", exc_info=True)
            raise ShuffleExecutionError(f"Failed to execute shuffle: {e}")

    @staticmethod
    def shuffle_changed_order(
        original_uris: List[str],
        shuffled_uris: List[str]
    ) -> bool:
        """
        Check if the shuffle actually changed the track order.

        Args:
            original_uris: List of URIs before shuffle.
            shuffled_uris: List of URIs after shuffle.

        Returns:
            True if the order changed, False if unchanged.
        """
        if not shuffled_uris:
            return False
        return shuffled_uris != original_uris

    @staticmethod
    def prepare_tracks_for_shuffle(
        tracks: List[Dict[str, Any]],
        current_uris: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Prepare tracks for shuffling by ordering them according to current URIs.

        This ensures the tracks are in the same order as the state we're shuffling from,
        which may differ from the order Spotify returns if the user has manually reordered.

        Args:
            tracks: List of track dictionaries from the playlist.
            current_uris: List of URIs representing the current order.

        Returns:
            List of track dictionaries ordered according to current_uris.
        """
        uri_to_track = {t['uri']: t for t in tracks}
        return [uri_to_track[uri] for uri in current_uris if uri in uri_to_track]
