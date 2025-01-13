import pytest

@pytest.fixture
def sample_tracks():
    return [
        {'track': {'uri': 'spotify:track:1', 'name': 'Song 1'}},
        {'track': {'uri': 'spotify:track:2', 'name': 'Song 2'}},
        {'track': {'uri': 'spotify:track:3', 'name': 'Song 3'}},
        {'track': {'uri': 'spotify:track:4', 'name': 'Song 4'}},
    ]

def test_shuffle_tracks_length(sample_tracks):
    shuffled = PlaylistShuffler.shuffle_tracks(sample_tracks)
    assert len(shuffled) == len(sample_tracks)

def test_shuffle_tracks_content(sample_tracks):
    shuffled = PlaylistShuffler.shuffle_tracks(sample_tracks)
    original_uris = {track['track']['uri'] for track in sample_tracks}
    shuffled_uris = set(shuffled)
    assert shuffled_uris == original_uris 