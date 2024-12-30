from src.api.spotify_client import SpotifyClient
from src.utils.playlist_shuffler import PlaylistShuffler

def display_playlists(playlists):
    """Displays available playlists with numbering."""
    print("\nYour Playlists:")
    for idx, playlist in enumerate(playlists, 1):
        print(f"{idx}. {playlist['name']} ({playlist['tracks']['total']} tracks)")
    return playlists

def main():
    print("Welcome to Shuffify!")
    print("-------------------")
    
    try:
        # Initialize Spotify client
        spotify_client = SpotifyClient()
        
        # Get user's playlists
        playlists = spotify_client.get_user_playlists()
        if not playlists:
            print("No playlists found!")
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
                    break
                print("Invalid selection. Please try again.")
            except ValueError:
                print("Please enter a valid number.")

        selected_playlist = playlists[selection]
        print(f"\nShuffling '{selected_playlist['name']}'...")

        # Get and shuffle tracks
        tracks = spotify_client.get_playlist_tracks(selected_playlist['id'])
        shuffled_uris = PlaylistShuffler.shuffle_tracks(tracks)
        
        # Update playlist
        if spotify_client.update_playlist_tracks(selected_playlist['id'], shuffled_uris):
            print("✨ Playlist successfully shuffled! ✨")
        else:
            print("Failed to shuffle playlist.")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main() 