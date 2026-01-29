"""
Playlist service for managing Spotify playlist operations.

Handles playlist retrieval, track management, and playlist updates.
"""

import logging
from typing import Dict, List, Any, Optional

from shuffify.spotify.client import SpotifyClient
from shuffify.models.playlist import Playlist

logger = logging.getLogger(__name__)


class PlaylistError(Exception):
    """Base exception for playlist operations."""
    pass


class PlaylistNotFoundError(PlaylistError):
    """Raised when a playlist cannot be found."""
    pass


class PlaylistUpdateError(PlaylistError):
    """Raised when a playlist update fails."""
    pass


class PlaylistService:
    """Service for managing Spotify playlist operations."""

    def __init__(self, spotify_client: SpotifyClient):
        """
        Initialize the playlist service.

        Args:
            spotify_client: An authenticated SpotifyClient instance.
        """
        self._client = spotify_client

    def get_user_playlists(self) -> List[Dict[str, Any]]:
        """
        Fetch all playlists the user can edit.

        Returns:
            List of playlist dictionaries (owned or collaborative).

        Raises:
            PlaylistError: If fetching playlists fails.
        """
        try:
            playlists = self._client.get_user_playlists()
            logger.debug(f"Retrieved {len(playlists)} user playlists")
            return playlists
        except Exception as e:
            logger.error(f"Failed to get user playlists: {e}", exc_info=True)
            raise PlaylistError(f"Failed to fetch playlists: {e}")

    def get_playlist(
        self,
        playlist_id: str,
        include_features: bool = False
    ) -> Playlist:
        """
        Fetch a single playlist with its tracks.

        Args:
            playlist_id: The Spotify playlist ID.
            include_features: Whether to include audio features for tracks.

        Returns:
            A Playlist model instance.

        Raises:
            PlaylistNotFoundError: If the playlist doesn't exist.
            PlaylistError: If fetching fails for other reasons.
        """
        if not playlist_id:
            raise PlaylistNotFoundError("Playlist ID is required")

        try:
            playlist = Playlist.from_spotify(
                self._client,
                playlist_id,
                include_features=include_features
            )
            logger.debug(f"Retrieved playlist '{playlist.name}' with {len(playlist)} tracks")
            return playlist
        except ValueError as e:
            logger.error(f"Invalid playlist ID: {playlist_id}")
            raise PlaylistNotFoundError(f"Playlist not found: {playlist_id}")
        except Exception as e:
            logger.error(f"Failed to get playlist {playlist_id}: {e}", exc_info=True)
            raise PlaylistError(f"Failed to fetch playlist: {e}")

    def get_playlist_stats(self, playlist_id: str) -> Dict[str, Any]:
        """
        Get audio feature statistics for a playlist.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            Dictionary of feature statistics (tempo, energy, valence, danceability).

        Raises:
            PlaylistNotFoundError: If the playlist doesn't exist.
            PlaylistError: If fetching fails.
        """
        playlist = self.get_playlist(playlist_id, include_features=True)
        stats = playlist.get_feature_stats()
        logger.debug(f"Computed stats for playlist {playlist_id}")
        return stats

    def update_playlist_tracks(
        self,
        playlist_id: str,
        track_uris: List[str]
    ) -> bool:
        """
        Update a playlist with a new track order.

        Args:
            playlist_id: The Spotify playlist ID.
            track_uris: List of track URIs in the desired order.

        Returns:
            True if update succeeded.

        Raises:
            PlaylistUpdateError: If the update fails.
        """
        if not playlist_id:
            raise PlaylistUpdateError("Playlist ID is required")

        if not track_uris:
            logger.warning(f"Attempting to update playlist {playlist_id} with empty track list")

        try:
            success = self._client.update_playlist_tracks(playlist_id, track_uris)
            if success:
                logger.info(f"Updated playlist {playlist_id} with {len(track_uris)} tracks")
                return True
            else:
                raise PlaylistUpdateError("Spotify API returned failure")
        except PlaylistUpdateError:
            raise
        except Exception as e:
            logger.error(f"Failed to update playlist {playlist_id}: {e}", exc_info=True)
            raise PlaylistUpdateError(f"Failed to update playlist: {e}")

    def get_track_uris(self, playlist_id: str) -> List[str]:
        """
        Get just the track URIs for a playlist.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            List of track URIs in current order.

        Raises:
            PlaylistNotFoundError: If the playlist doesn't exist.
            PlaylistError: If fetching fails.
        """
        playlist = self.get_playlist(playlist_id, include_features=False)
        return playlist.get_track_uris()

    def validate_playlist_has_tracks(self, playlist: Playlist) -> None:
        """
        Validate that a playlist has tracks.

        Args:
            playlist: The Playlist to validate.

        Raises:
            PlaylistError: If playlist has no tracks.
        """
        if not playlist.tracks:
            raise PlaylistError(f"Playlist '{playlist.name}' has no tracks")
