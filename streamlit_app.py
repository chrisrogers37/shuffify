import streamlit as st
from src.api.spotify_client import SpotifyClient
from src.utils.shuffify import shuffle_playlist

st.set_page_config(
    page_title="Shuffify",
    page_icon="🎵",
    layout="wide"
)

def initialize_session_state():
    """Initialize session state variables."""
    if 'spotify_client' not in st.session_state:
        st.session_state.spotify_client = None
    if 'has_shuffled' not in st.session_state:
        st.session_state.has_shuffled = False
    if 'last_playlist' not in st.session_state:
        st.session_state.last_playlist = None
    if 'shuffle_count' not in st.session_state:
        st.session_state.shuffle_count = 0
    if 'first_shuffle_done' not in st.session_state:
        st.session_state.first_shuffle_done = False

def main():
    st.title("🎵 Shuffify")

    initialize_session_state()

    # Initialize Spotify client
    try:
        if st.session_state.spotify_client is None:
            st.session_state.spotify_client = SpotifyClient()
            user = st.session_state.spotify_client.sp.current_user()
            st.success(f"Logged in as: {user['display_name']}")
    except Exception as e:
        st.error(f"Authentication failed: {str(e)}")
        return

    # Get playlists
    playlists = st.session_state.spotify_client.get_user_playlists()
    
    if not playlists:
        st.warning("No editable playlists found!")
        return

    # Create two columns for layout
    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown("### Step 1: Select Your Playlist")
        st.markdown("_Choose a playlist you'd like to shuffle from the dropdown below_")
        playlist_options = {f"{p['name']} ({p['tracks']['total']} tracks)": p 
                          for p in playlists if p['tracks']['total'] > 0}
        selected_name = st.selectbox(
            "Available playlists",
            options=list(playlist_options.keys()),
            help="Shows all playlists you can edit"
        )
        
        selected_playlist = playlist_options[selected_name]
        
        # Keep first N tracks - Alternative Options
        st.markdown("### Step 2: Configure Shuffle Settings")
        st.markdown("_Decide if you want to keep any tracks locked at the top of your playlist_")
        input_method = st.radio(
            "Select how many tracks should remain in their current position at the top of the playlist?",
            ["None", "Specify number of tracks", "Specify percentage of playlist"],
            help="Choose tracks to keep fixed at the start of the playlist while shuffling the rest"
        )

        if input_method == "Specify number of tracks":
            keep_first = st.number_input(
                "Number of tracks to keep at the top",
                min_value=0,
                max_value=selected_playlist['tracks']['total'],
                value=0,
                step=1,
                help="These tracks will stay in their current position at the start of the playlist while the rest are shuffled"
            )
        elif input_method == "Specify percentage of playlist":
            percentage = st.slider(
                "Percentage of playlist to keep at the top",
                0, 100, 0,
                help="This percentage of tracks will remain in their current position at the start of the playlist"
            )
            keep_first = int(selected_playlist['tracks']['total'] * percentage / 100)
        else:
            keep_first = 0

        if keep_first > 0:
            st.info(f"🎵 The first {keep_first} tracks will keep their current position at the top of the playlist. All other tracks will be shuffled.")

        st.markdown("### Step 3: Shuffle Your Playlist")
        st.markdown("_Click the button below to shuffle your playlist with the selected settings_")

    # Move playlist info to the right side
    with col2:
        # User info at the top
        if st.session_state.spotify_client:
            user = st.session_state.spotify_client.sp.current_user()
            st.markdown(f"### 👤 {user['display_name']}")
        
        # Playlist info section
        if selected_playlist:
            st.markdown("### Shuffle Info")
            st.markdown(f"Playlist name: **{selected_playlist['name']}**")
            
            # Add shuffle count - show after first shuffle is done
            if st.session_state.first_shuffle_done:
                shuffle_text = "shuffle" if st.session_state.shuffle_count == 1 else "shuffles"
                st.markdown(f"🔄 **{st.session_state.shuffle_count}** {shuffle_text} this session")
            
            # Playlist metrics in a clean layout
            st.metric("Total Tracks", selected_playlist['tracks']['total'])
            st.metric("Locked Tracks", keep_first)
            st.metric("Tracks to Shuffle", selected_playlist['tracks']['total'] - keep_first)
            
            # Additional playlist info
            if selected_playlist.get('collaborative'):
                st.markdown("📝 _Collaborative playlist_")
            st.markdown(f"Playlist created by: 👤{selected_playlist['owner']['display_name']}")

    # Action buttons in columns for better layout
    button_col1, button_col2 = st.columns([1, 2])
    
    with button_col1:
        if st.button("🎲 Shuffle Playlist", type="primary"):
            # Update state before any operations
            st.session_state.first_shuffle_done = True
            st.session_state.shuffle_count += 1
            st.session_state.has_shuffled = True
            st.session_state.last_playlist = selected_playlist
            st.session_state.last_keep_first = keep_first
            
            # Force a rerun to update the UI
            st.rerun()
            
            with st.spinner("Shuffling playlist..."):
                try:
                    shuffled_uris = shuffle_playlist(
                        st.session_state.spotify_client.sp,
                        selected_playlist['id'],
                        keep_first=keep_first
                    )
                    
                    if st.session_state.spotify_client.update_playlist_tracks(
                        selected_playlist['id'],
                        shuffled_uris
                    ):
                        st.success("✨ Playlist successfully shuffled! ✨")
                    else:
                        # Reset counters if shuffle fails
                        st.session_state.first_shuffle_done = False
                        st.session_state.shuffle_count -= 1
                        st.session_state.has_shuffled = False
                        st.error("Failed to shuffle playlist.")
                        st.rerun()
                except Exception as e:
                    # Reset counters if shuffle fails
                    st.session_state.first_shuffle_done = False
                    st.session_state.shuffle_count -= 1
                    st.session_state.has_shuffled = False
                    st.error(f"Error during shuffle: {str(e)}")
                    st.rerun()

    # Show re-shuffle and undo buttons if available
    if st.session_state.has_shuffled:
        st.markdown("### Step 4: Quick Actions")
        st.markdown("_You can now re-shuffle the playlist or restore its original order_")
        action_col1, action_col2 = st.columns(2)
        
        with action_col1:
            if st.button("🔄 Re-shuffle Same Playlist"):
                with st.spinner(f"Re-shuffling '{st.session_state.last_playlist['name']}'..."):
                    try:
                        shuffled_uris = shuffle_playlist(
                            st.session_state.spotify_client.sp,
                            st.session_state.last_playlist['id'],
                            keep_first=st.session_state.last_keep_first
                        )
                        
                        if st.session_state.spotify_client.update_playlist_tracks(
                            st.session_state.last_playlist['id'],
                            shuffled_uris
                        ):
                            st.success("✨ Playlist successfully re-shuffled! ✨")
                            st.session_state.shuffle_count += 1
                        else:
                            st.error("Failed to re-shuffle playlist.")
                    except Exception as e:
                        st.error(f"Error during re-shuffle: {str(e)}")
        
        with action_col2:
            if 'show_confirm' not in st.session_state:
                st.session_state.show_confirm = False

            if st.button("↩️ Restore Original Order"):
                st.session_state.show_confirm = True

            if st.session_state.show_confirm:
                st.warning("Are you sure you want to restore the original playlist order?")
                confirm_col1, confirm_col2 = st.columns(2)
                
                with confirm_col1:
                    if st.button("Yes, restore original"):
                        with st.spinner("Restoring original order..."):
                            if st.session_state.spotify_client.undo_last_shuffle():
                                st.success("✨ Successfully restored original playlist order! ✨")
                                st.session_state.has_shuffled = False
                                st.session_state.last_playlist = None
                                st.session_state.last_keep_first = None
                                st.session_state.show_confirm = False
                            else:
                                st.error("Failed to restore original order.")
                
                with confirm_col2:
                    if st.button("No, cancel"):
                        st.session_state.show_confirm = False
                        st.rerun()
        
        st.markdown("---")
        st.info("💡 To shuffle a different playlist, simply select it from the dropdown menu above.")

    # Add a sidebar with comprehensive information
    with st.sidebar:
        # Logo
        st.markdown("""
        <div style='text-align: center; font-size: 24px; margin-bottom: 20px;'>
            🎵 ⇄ 🎲<br>
            <span style='font-size: 32px; font-weight: bold; background: linear-gradient(45deg, #1DB954, #191414); -webkit-background-clip: text; color: transparent;'>
            Shuffify
            </span>
        </div>
        """, unsafe_allow_html=True)
        
        # Welcome and features
        st.markdown("### Welcome!")
        st.markdown("""
        Shuffify helps you rearrange songs in your Spotify playlists while 
        optionally locking tracks at the top of your playlist. Perfect for:
        - Shuffling large, cumbersome playlists
        - Placing new additions randomly alongside old favorites
        - Keeping your favorite tunes where you want them, at the top of your playlist
                    
        Follow the directions within the workflow in the main window to use the app.
        """)

if __name__ == "__main__":
    main() 