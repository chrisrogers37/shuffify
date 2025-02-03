import random
import logging
from typing import List, Optional
from spotipy import Spotify
from spotipy.exceptions import SpotifyException

logger = logging.getLogger(__name__)

def shuffle_playlist(
    sp: Spotify,
    playlist_id: str,
    keep_first: int = 0,
    smart_shuffle: bool = False
) -> Optional[List[str]]:
    """
    Shuffle a Spotify playlist while optionally keeping the first N tracks in place.
    
    Args:
        sp: Spotify client instance
        playlist_id: ID of the playlist to shuffle
        keep_first: Number of tracks to keep in their original position (default: 0)
        smart_shuffle: Whether to use smart shuffling considering track features (default: False)
    
    Returns:
        List of track URIs in their new order, or None if an error occurs
    """
    try:
        # Get all tracks from the playlist
        results = sp.playlist_items(playlist_id)
        tracks = results['items']
        
        # Get all tracks if there are more (pagination)
        track_count = len(tracks)
        total_tracks = sp.playlist(playlist_id)['tracks']['total']
        
        logger.info(f"Fetching {total_tracks} tracks from playlist {playlist_id}")
        
        # Fetch all tracks
        while results['next']:
            results = sp.next(results)
            tracks.extend(results['items'])
            track_count += len(results['items'])
            logger.info(f"Loaded {track_count}/{total_tracks} tracks")
        
        # Extract track URIs and validate
        track_uris: List[str] = []
        
        # Process tracks
        for item in tracks:
            if item.get('track'):
                track_uris.append(item['track']['uri'])
        
        try:
            # Handle the shuffling
            logger.info("Shuffling tracks...")
            if keep_first > 0:
                kept_tracks = track_uris[:keep_first]
                to_shuffle = track_uris[keep_first:]
                
                if smart_shuffle:
                    shuffled_tracks = smart_shuffle_tracks(sp, to_shuffle)
                else:
                    random.shuffle(to_shuffle)
                    shuffled_tracks = to_shuffle
                    
                track_uris = kept_tracks + shuffled_tracks
            else:
                if smart_shuffle:
                    track_uris = smart_shuffle_tracks(sp, track_uris)
                else:
                    random.shuffle(track_uris)
            
            logger.info(f"Successfully shuffled {len(track_uris)} tracks")
            return track_uris
                
        except Exception as e:
            logger.error(f"Error during shuffle operation: {str(e)}")
            raise e
            
    except SpotifyException as e:
        logger.error(f"Spotify API error during shuffle: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during shuffle: {str(e)}")
        return None

def smart_shuffle_tracks(sp: Spotify, track_uris: List[str]) -> List[str]:
    """
    Implement smart shuffling by considering track features and transitions.
    This is a placeholder for future implementation.
    
    Args:
        sp: Spotify client instance
        track_uris: List of track URIs to shuffle
    
    Returns:
        Shuffled list of track URIs
    """
    # TODO: Implement smart shuffling logic considering:
    # - Track tempo (BPM)
    # - Key compatibility
    # - Energy levels
    # - Genre transitions
    # For now, just do a random shuffle
    random.shuffle(track_uris)
    return track_uris 