import random
import time
from tqdm import tqdm

def shuffle_playlist(sp, playlist_id, keep_first=0):
    print(f"\nFetching playlist tracks...")
    # Get all tracks from the playlist
    results = sp.playlist_items(playlist_id)
    tracks = results['items']
    
    # Get all tracks if there are more (pagination)
    with tqdm(
        desc="Loading tracks",
        bar_format="{desc}: {n_fmt} songs loaded [{elapsed}]"
    ) as pbar:
        while results['next']:
            results = sp.next(results)
            tracks.extend(results['items'])
            pbar.update(len(results['items']))
    
    print(f"Found {len(tracks)} total tracks")
    
    # Extract track URIs and validate
    track_uris = []
    with tqdm(
        total=len(tracks),
        desc="Processing tracks",
        bar_format="{desc}: {percentage:3.0f}% |{bar}| {n_fmt}/{total_fmt} songs [elapsed: {elapsed}, remaining: {remaining}]"
    ) as pbar:
        for item in tracks:
            if item.get('track'):
                track_uris.append(item['track']['uri'])
            pbar.update(1)
    
    print(f"Extracted {len(track_uris)} valid track URIs")
    
    try:
        # Handle the shuffling
        if keep_first > 0:
            kept_tracks = track_uris[:keep_first]
            shuffled_tracks = track_uris[keep_first:]
            print(f"\nShuffling {len(shuffled_tracks)} tracks...")
            random.shuffle(shuffled_tracks)
            track_uris = kept_tracks + shuffled_tracks
            print(f"Keeping first {keep_first} tracks unchanged")
        else:
            print(f"\nShuffling all {len(track_uris)} tracks...")
            random.shuffle(track_uris)
        
        return track_uris
            
    except Exception as e:
        print(f"\nError during shuffle: {str(e)}")
        raise e 