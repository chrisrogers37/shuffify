import pytest
from app.utils.shuffify import shuffle_playlist

def test_shuffle_playlist():
    # Test with empty list
    assert shuffle_playlist([]) == []
    
    # Test with single track
    single_track = ['spotify:track:1']
    assert shuffle_playlist(single_track) == single_track
    
    # Test with multiple tracks
    tracks = [f'spotify:track:{i}' for i in range(10)]
    shuffled = shuffle_playlist(tracks.copy())
    
    # Verify all tracks are present
    assert len(shuffled) == len(tracks)
    assert set(shuffled) == set(tracks)
    
    # Verify order is different (note: this test has a tiny chance of failing 
    # if the shuffle happens to produce the same order)
    assert shuffled != tracks

def test_shuffle_playlist_maintains_uniqueness():
    # Test with duplicate tracks
    tracks = ['spotify:track:1', 'spotify:track:2', 'spotify:track:1']
    shuffled = shuffle_playlist(tracks)
    
    # Verify duplicates are preserved
    assert len(shuffled) == len(tracks)
    assert shuffled.count('spotify:track:1') == 2
    assert shuffled.count('spotify:track:2') == 1

def test_shuffle_playlist_large():
    # Test with a larger playlist
    tracks = [f'spotify:track:{i}' for i in range(1000)]
    shuffled = shuffle_playlist(tracks)
    
    # Verify all tracks are present
    assert len(shuffled) == len(tracks)
    assert set(shuffled) == set(tracks)
    
    # Check that the order has changed significantly
    # Count how many items are in their original position
    same_position = sum(1 for i, track in enumerate(shuffled) if track == f'spotify:track:{i}')
    
    # In a good shuffle, very few items should remain in their original position
    # (approximately 1% for large lists)
    assert same_position < len(tracks) * 0.05  # Allow up to 5% to be in the same position 