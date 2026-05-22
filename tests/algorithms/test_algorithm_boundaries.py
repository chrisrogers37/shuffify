"""
Cross-algorithm boundary tests.

Validates that all registered algorithms handle edge cases uniformly:
two-track playlists, large playlists, all-identical artists, and
duplicate URIs in input.
"""

import pytest

from shuffify.shuffle_algorithms.registry import ShuffleRegistry


@pytest.fixture
def all_algorithms():
    """All registered algorithm instances (class -> instance)."""
    return {
        name: cls() for name, cls in ShuffleRegistry.get_available_algorithms().items()
    }


def _make_tracks(n, same_artist=False):
    """Build a list of n tracks with artist metadata."""
    return [
        {
            "uri": f"spotify:track:{'a' * 21}{i % 10}",
            "name": f"Track {i}",
            "artists": [{"name": "Same" if same_artist else f"Artist {i}"}],
            "album": {"name": f"Album {i}"},
        }
        for i in range(n)
    ]


class TestTwoTrackPlaylist:
    """Every algorithm must handle a 2-track playlist without crashing."""

    def test_two_tracks_returns_both(self, all_algorithms):
        tracks = _make_tracks(2)
        for name, algo in all_algorithms.items():
            result = algo.shuffle(tracks)
            assert len(result) == 2, f"{name} dropped a track"
            assert set(result) == {t["uri"] for t in tracks}, f"{name} altered URIs"


class TestLargePlaylist:
    """Algorithms must not degrade on playlists with 500+ tracks."""

    def test_500_tracks_preserves_count(self, all_algorithms):
        tracks = _make_tracks(500)
        for name, algo in all_algorithms.items():
            result = algo.shuffle(tracks)
            assert len(result) == 500, (
                f"{name} returned {len(result)} tracks, expected 500"
            )


class TestAllSameArtist:
    """All tracks from one artist shouldn't crash spacing algorithms."""

    def test_same_artist_returns_all(self, all_algorithms):
        tracks = _make_tracks(20, same_artist=True)
        for name, algo in all_algorithms.items():
            result = algo.shuffle(tracks)
            assert len(result) == 20, f"{name} dropped tracks"


class TestNoUriTracks:
    """Tracks without a 'uri' key should be silently dropped."""

    def test_mixed_valid_and_missing_uri(self, all_algorithms):
        tracks = _make_tracks(5) + [{"name": "No URI track"}]
        for name, algo in all_algorithms.items():
            result = algo.shuffle(tracks)
            assert len(result) == 5, f"{name} should return 5, got {len(result)}"


class TestBalancedShuffleSectionCountOne:
    """BalancedShuffle with section_count=1 should still return all tracks."""

    def test_section_count_one(self):
        from shuffify.shuffle_algorithms.balanced import BalancedShuffle

        algo = BalancedShuffle()
        tracks = _make_tracks(10)
        result = algo.shuffle(tracks, section_count=1)
        assert len(result) == 10


class TestPercentageShuffleTwoTracks:
    """PercentageShuffle at 50% with 2 tracks: 1 fixed, 1 shuffled."""

    def test_two_tracks_50_percent(self):
        from shuffify.shuffle_algorithms.percentage import PercentageShuffle

        algo = PercentageShuffle()
        tracks = _make_tracks(2)
        result = algo.shuffle(
            tracks,
            shuffle_percentage=50.0,
            shuffle_location="front",
        )
        assert len(result) == 2
        assert set(result) == {t["uri"] for t in tracks}
