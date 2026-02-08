"""
Tests for TempoGradientShuffle algorithm.

Tests cover tempo sorting, direction parameter, and edge cases.

Note: This algorithm requires Spotify Audio Features API access,
which was deprecated in Nov 2024. The algorithm is hidden from the
UI but kept for when extended API access is available.
"""

import pytest

from shuffify.shuffle_algorithms.tempo_gradient import TempoGradientShuffle


class TestTempoGradientShuffleProperties:
    """Tests for TempoGradientShuffle metadata properties."""

    def test_name_is_tempo_gradient(self):
        """Should return 'Tempo Gradient' as name."""
        algo = TempoGradientShuffle()
        assert algo.name == "Tempo Gradient"

    def test_description_is_present(self):
        """Should have a meaningful description."""
        algo = TempoGradientShuffle()
        assert algo.description
        assert len(algo.description) > 10

    def test_parameters_includes_direction(self):
        """Should include direction parameter."""
        algo = TempoGradientShuffle()
        params = algo.parameters

        assert "direction" in params
        assert params["direction"]["type"] == "string"
        assert params["direction"]["default"] == "ascending"
        assert "ascending" in params["direction"]["options"]
        assert "descending" in params["direction"]["options"]

    def test_requires_features_is_true(self):
        """Should require audio features."""
        algo = TempoGradientShuffle()
        assert algo.requires_features is True


class TestTempoGradientShuffleShuffle:
    """Tests for TempoGradientShuffle.shuffle method."""

    @pytest.fixture
    def algorithm(self):
        """TempoGradientShuffle instance."""
        return TempoGradientShuffle()

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks with IDs for feature lookup."""
        return [
            {"uri": "spotify:track:1", "id": "track1", "name": "Slow Song"},
            {"uri": "spotify:track:2", "id": "track2", "name": "Medium Song"},
            {"uri": "spotify:track:3", "id": "track3", "name": "Fast Song"},
            {"uri": "spotify:track:4", "id": "track4", "name": "Faster Song"},
            {"uri": "spotify:track:5", "id": "track5", "name": "Fastest Song"},
        ]

    @pytest.fixture
    def sample_features(self):
        """Audio features with varying tempos."""
        return {
            "track1": {"tempo": 80.0, "energy": 0.3},
            "track2": {"tempo": 110.0, "energy": 0.5},
            "track3": {"tempo": 130.0, "energy": 0.7},
            "track4": {"tempo": 150.0, "energy": 0.8},
            "track5": {"tempo": 175.0, "energy": 0.9},
        }

    def test_shuffle_returns_all_uris(
        self, algorithm, sample_tracks, sample_features
    ):
        """Should return all URIs from input tracks."""
        result = algorithm.shuffle(sample_tracks, features=sample_features)

        original_uris = {t["uri"] for t in sample_tracks}
        result_uris = set(result)

        assert original_uris == result_uris

    def test_shuffle_returns_list_of_strings(
        self, algorithm, sample_tracks, sample_features
    ):
        """Should return list of URI strings."""
        result = algorithm.shuffle(sample_tracks, features=sample_features)

        assert isinstance(result, list)
        assert all(isinstance(uri, str) for uri in result)

    def test_shuffle_preserves_count(
        self, algorithm, sample_tracks, sample_features
    ):
        """Should preserve the number of tracks."""
        result = algorithm.shuffle(sample_tracks, features=sample_features)
        assert len(result) == len(sample_tracks)

    def test_shuffle_empty_list(self, algorithm):
        """Should handle empty track list."""
        result = algorithm.shuffle([])
        assert result == []

    def test_shuffle_single_track(self, algorithm):
        """Should handle single track."""
        tracks = [{"uri": "spotify:track:single", "id": "s1"}]
        result = algorithm.shuffle(tracks)
        assert result == ["spotify:track:single"]

    def test_ascending_sorts_by_tempo(
        self, algorithm, sample_tracks, sample_features
    ):
        """Ascending should sort slow to fast."""
        result = algorithm.shuffle(
            sample_tracks, features=sample_features, direction="ascending"
        )
        assert result == [
            "spotify:track:1",  # 80 BPM
            "spotify:track:2",  # 110 BPM
            "spotify:track:3",  # 130 BPM
            "spotify:track:4",  # 150 BPM
            "spotify:track:5",  # 175 BPM
        ]

    def test_descending_sorts_by_tempo(
        self, algorithm, sample_tracks, sample_features
    ):
        """Descending should sort fast to slow."""
        result = algorithm.shuffle(
            sample_tracks, features=sample_features, direction="descending"
        )
        assert result == [
            "spotify:track:5",  # 175 BPM
            "spotify:track:4",  # 150 BPM
            "spotify:track:3",  # 130 BPM
            "spotify:track:2",  # 110 BPM
            "spotify:track:1",  # 80 BPM
        ]

    def test_default_direction_is_ascending(
        self, algorithm, sample_tracks, sample_features
    ):
        """Default direction should be ascending."""
        result = algorithm.shuffle(sample_tracks, features=sample_features)
        assert result == [
            "spotify:track:1",
            "spotify:track:2",
            "spotify:track:3",
            "spotify:track:4",
            "spotify:track:5",
        ]


class TestTempoGradientShuffleNoFeatures:
    """Tests for behavior when audio features are unavailable."""

    @pytest.fixture
    def algorithm(self):
        """TempoGradientShuffle instance."""
        return TempoGradientShuffle()

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks."""
        return [
            {"uri": f"spotify:track:{i}", "id": f"track{i}"}
            for i in range(5)
        ]

    def test_no_features_uses_default_tempo(self, algorithm, sample_tracks):
        """With no features, all tracks get default tempo (stable sort = original order)."""
        result = algorithm.shuffle(sample_tracks)
        expected = [t["uri"] for t in sample_tracks]
        assert result == expected

    def test_empty_features_dict(self, algorithm, sample_tracks):
        """Empty features dict should use default tempo for all."""
        result = algorithm.shuffle(sample_tracks, features={})
        expected = [t["uri"] for t in sample_tracks]
        assert result == expected

    def test_partial_features(self, algorithm):
        """Some tracks with features, some without."""
        tracks = [
            {"uri": "spotify:track:1", "id": "t1"},
            {"uri": "spotify:track:2", "id": "t2"},
            {"uri": "spotify:track:3", "id": "t3"},
        ]
        features = {
            "t1": {"tempo": 160.0},  # Fast
            # t2 missing -> defaults to 120.0
            "t3": {"tempo": 80.0},  # Slow
        }
        result = algorithm.shuffle(tracks, features=features, direction="ascending")
        assert result == [
            "spotify:track:3",  # 80 BPM
            "spotify:track:2",  # 120 BPM (default)
            "spotify:track:1",  # 160 BPM
        ]


class TestTempoGradientShuffleEdgeCases:
    """Edge case tests for TempoGradientShuffle."""

    @pytest.fixture
    def algorithm(self):
        """TempoGradientShuffle instance."""
        return TempoGradientShuffle()

    def test_tracks_without_uri_skipped(self, algorithm):
        """Should skip tracks without URI."""
        tracks = [
            {"uri": "spotify:track:1", "id": "t1"},
            {"name": "No URI", "id": "t2"},
            {"uri": "spotify:track:3", "id": "t3"},
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 2

    def test_features_keyed_by_uri(self, algorithm):
        """Should look up features by URI as fallback."""
        tracks = [
            {"uri": "spotify:track:1", "id": ""},
            {"uri": "spotify:track:2", "id": ""},
        ]
        features = {
            "spotify:track:1": {"tempo": 150.0},
            "spotify:track:2": {"tempo": 90.0},
        }
        result = algorithm.shuffle(
            tracks, features=features, direction="ascending"
        )
        assert result == ["spotify:track:2", "spotify:track:1"]

    def test_same_tempo_preserves_order(self, algorithm):
        """Tracks with same tempo should maintain relative order (stable sort)."""
        tracks = [
            {"uri": f"spotify:track:{i}", "id": f"t{i}"}
            for i in range(5)
        ]
        features = {f"t{i}": {"tempo": 120.0} for i in range(5)}

        result = algorithm.shuffle(tracks, features=features)
        expected = [t["uri"] for t in tracks]
        assert result == expected

    def test_features_missing_tempo_key(self, algorithm):
        """Features dict without tempo key should use default."""
        tracks = [
            {"uri": "spotify:track:1", "id": "t1"},
            {"uri": "spotify:track:2", "id": "t2"},
        ]
        features = {
            "t1": {"energy": 0.8},  # No tempo key
            "t2": {"tempo": 80.0},
        }
        result = algorithm.shuffle(
            tracks, features=features, direction="ascending"
        )
        assert result == [
            "spotify:track:2",  # 80 BPM
            "spotify:track:1",  # 120 BPM (default)
        ]
