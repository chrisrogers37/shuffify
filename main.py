from src.api.spotify_client import SpotifyClient
from src.utils.shuffify import shuffle_playlist

def display_playlists(playlists):
    """Displays available playlists with numbering."""
    print("\nYour Playlists:")
    for idx, playlist in enumerate(playlists, 1):
        track_count = playlist['tracks']['total']
        status = "🟢" if track_count > 0 else "⚠️"
        owner_name = playlist['owner']['display_name']
        is_collab = "👥 " if playlist.get('collaborative', False) else ""
        print(f"{idx}. {status} {is_collab}{playlist['name']} ({track_count} tracks) - by {owner_name}")
    return playlists

def get_keep_first_count(total_tracks: int) -> int:
    """Asks user how many tracks to keep in their original position."""
    while True:
        try:
            keep_str = input("\nHow many tracks would you like to keep unchanged at the start? (0 for none): ").strip()
            if not keep_str:  # Handle empty input
                return 0
            keep = int(keep_str)
            if 0 <= keep <= total_tracks:
                return keep
            print(f"Please enter a number between 0 and {total_tracks}")
        except ValueError:
            print("Please enter a valid number")

def main():
    print("Welcome to Shuffify!")
    print("-------------------")
    print("Note: Only showing playlists you can edit")
    
    try:
        # Initialize Spotify client
        spotify_client = SpotifyClient()
        
        # Get user's playlists
        playlists = spotify_client.get_user_playlists()
        if not playlists:
            print("No editable playlists found!")
            return

        # Display playlists and get user selection
        playlists = display_playlists(playlists)
        
        while True:
            try:
                selection = int(input("\nEnter playlist number to shuffle (0 to exit): ")) - 1
                if selection == -1:
                    print("Thanks for using Shuffify!")
                    return
                if 0 <= selection < len(playlists):
                    selected_playlist = playlists[selection]
                    # Check if playlist is empty
                    if selected_playlist['tracks']['total'] == 0:
                        print(f"\n⚠️ The playlist '{selected_playlist['name']}' is empty. Please select a different playlist.")
                        continue
                    break
                print("Invalid selection. Please try again.")
            except ValueError:
                print("Please enter a valid number.")

        # Get number of tracks to keep unchanged
        keep_first = get_keep_first_count(selected_playlist['tracks']['total'])

        print(f"\nShuffling '{selected_playlist['name']}'...")
        if keep_first > 0:
            print(f"(keeping first {keep_first} tracks unchanged)")

        # Shuffle the tracks
        shuffled_uris = shuffle_playlist(spotify_client.sp, selected_playlist['id'], keep_first=keep_first)
        
        # Update playlist
        if spotify_client.update_playlist_tracks(selected_playlist['id'], shuffled_uris):
            print("✨ Playlist successfully shuffled! ✨")
        else:
            print("Failed to shuffle playlist.")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main() 