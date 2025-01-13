import random
import streamlit as st

def shuffle_playlist(sp, playlist_id, keep_first=0):
    st.write("Fetching playlist tracks...")
    # Get all tracks from the playlist
    results = sp.playlist_items(playlist_id)
    tracks = results['items']
    
    # Get all tracks if there are more (pagination)
    progress_text = "Loading tracks"
    progress_bar = st.progress(0)
    track_count = len(tracks)
    total_tracks = sp.playlist(playlist_id)['tracks']['total']
    
    while results['next']:
        results = sp.next(results)
        tracks.extend(results['items'])
        track_count += len(results['items'])
        progress = track_count / total_tracks
        progress_bar.progress(progress)
        st.write(f"{progress_text}: {track_count} songs loaded")
    
    st.write(f"Found {len(tracks)} total tracks")
    
    # Extract track URIs and validate
    track_uris = []
    progress_text = "Processing tracks"
    progress_bar = st.progress(0)
    
    for i, item in enumerate(tracks):
        if item.get('track'):
            track_uris.append(item['track']['uri'])
        progress = (i + 1) / len(tracks)
        progress_bar.progress(progress)
        st.write(f"{progress_text}: {i + 1}/{len(tracks)} songs")
    
    st.write(f"Extracted {len(track_uris)} valid track URIs")
    
    try:
        # Handle the shuffling
        if keep_first > 0:
            kept_tracks = track_uris[:keep_first]
            shuffled_tracks = track_uris[keep_first:]
            st.write(f"Shuffling {len(shuffled_tracks)} tracks...")
            random.shuffle(shuffled_tracks)
            track_uris = kept_tracks + shuffled_tracks
            st.write(f"Keeping first {keep_first} tracks unchanged")
        else:
            st.write(f"Shuffling all {len(track_uris)} tracks...")
            random.shuffle(track_uris)
        
        return track_uris
            
    except Exception as e:
        st.error(f"Error during shuffle: {str(e)}")
        raise e 