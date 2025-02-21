import random
import logging
from typing import List, Optional
from spotipy import Spotify
from spotipy.exceptions import SpotifyException

logger = logging.getLogger(__name__)

def shuffle_playlist(
    sp: Optional[Spotify] = None,
    playlist_id: Optional[str] = None,
    keep_first: int = 0,
    track_list: Optional[List[str]] = None
) -> List[str]:
    """
    Shuffle a playlist while optionally keeping the first N tracks in place.
    Can work with either a Spotify client and playlist ID, or a direct list of track URIs.
    
    Args:
        sp: Optional Spotify client instance
        playlist_id: Optional ID of the playlist to shuffle
        keep_first: Number of tracks to keep in their original position (default: 0)
        track_list: Optional list of track URIs to shuffle directly
        
    Returns:
        List of track URIs in their new order
    """
    try:
        # Handle empty input
        if not track_list and not (sp and playlist_id):
            logger.error("No tracks provided and no Spotify client/playlist ID to fetch tracks")
            return []
            
        # Get tracks if not provided directly
        if not track_list:
            try:
                results = sp.playlist_items(playlist_id)
                tracks = []
                
                while results:
                    tracks.extend([
                        item['track']['uri'] for item in results['items']
                        if item.get('track', {}).get('uri')
                    ])
                    results = sp.next(results) if results['next'] else None
                    
                track_list = tracks
                
            except Exception as e:
                logger.error(f"Error fetching playlist tracks: {str(e)}")
                return []
        
        # Handle single track case
        if len(track_list) <= 1:
            logger.debug("Track list has 1 or fewer tracks, returning as is")
            return track_list
            
        # Perform the shuffle
        if keep_first > 0:
            logger.debug(f"Keeping first {keep_first} tracks in place")
            kept_tracks = track_list[:keep_first]
            to_shuffle = track_list[keep_first:]
            random.shuffle(to_shuffle)
            shuffled = kept_tracks + to_shuffle
        else:
            shuffled = track_list.copy()
            random.shuffle(shuffled)
            
        logger.info(f"Successfully shuffled {len(shuffled)} tracks")
        return shuffled
            
    except Exception as e:
        logger.error(f"Error during shuffle operation: {str(e)}")
        return []

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