import random
import streamlit as st

def shuffle_playlist(sp, playlist_id, keep_first=0):
    with st.spinner("Loading playlist tracks..."):
        # Get all tracks from the playlist
        results = sp.playlist_items(playlist_id)
        tracks = results['items']
        
        # Get all tracks if there are more (pagination)
        progress = st.progress(0.0)
        status = st.empty()
        track_count = len(tracks)
        total_tracks = sp.playlist(playlist_id)['tracks']['total']
        
        # Fetch all tracks with progress
        while results['next']:
            results = sp.next(results)
            tracks.extend(results['items'])
            track_count += len(results['items'])
            current_progress = track_count / total_tracks
            progress.progress(current_progress)
            status.text(f"Loading tracks: {track_count}/{total_tracks}")
        
        # Extract track URIs and validate
        track_uris = []
        progress.progress(0.0)
        
        # Process tracks with progress
        for i, item in enumerate(tracks):
            if item.get('track'):
                track_uris.append(item['track']['uri'])
            current_progress = (i + 1) / len(tracks)
            progress.progress(current_progress)
            status.text(f"Processing tracks: {i + 1}/{len(tracks)}")
        
        try:
            # Handle the shuffling
            status.text("Shuffling tracks...")
            if keep_first > 0:
                kept_tracks = track_uris[:keep_first]
                shuffled_tracks = track_uris[keep_first:]
                random.shuffle(shuffled_tracks)
                track_uris = kept_tracks + shuffled_tracks
            else:
                random.shuffle(track_uris)
            
            status.empty()
            return track_uris
                
        except Exception as e:
            st.error(f"Error during shuffle: {str(e)}")
            raise e 