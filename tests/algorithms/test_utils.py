"""
Tests for shuffle algorithm utility functions.

Tests extract_uris, split_keep_first, and split_into_sections
pure functions used across multiple shuffle algorithms.
"""

from shuffify.shuffle_algorithms.utils import (
    extract_uris,
    split_keep_first,
    split_into_sections,
)


# =============================================================================
# extract_uris
# =============================================================================

class TestExtractUris:
    """Tests for extract_uris()."""

    def test_normal_tracks(self):
        tracks = [{"uri": "a"}, {"uri": "b"}, {"uri": "c"}]
        assert extract_uris(tracks) == ["a", "b", "c"]

    def test_empty_list(self):
        assert extract_uris([]) == []

    def test_track_without_uri_key(self):
        tracks = [{"uri": "a"}, {"name": "b"}]
        assert extract_uris(tracks) == ["a"]

    def test_track_with_empty_uri(self):
        tracks = [{"uri": "a"}, {"uri": ""}]
        assert extract_uris(tracks) == ["a"]

    def test_preserves_order(self):
        tracks = [{"uri": f"uri_{i}"} for i in range(10)]
        result = extract_uris(tracks)
        assert result == [f"uri_{i}" for i in range(10)]

    def test_all_tracks_missing_uri(self):
        tracks = [{"name": "a"}, {"name": "b"}]
        assert extract_uris(tracks) == []

    def test_mixed_valid_and_invalid(self):
        tracks = [
            {"uri": "valid1"},
            {"name": "no_uri"},
            {"uri": "valid2"},
            {"uri": ""},
            {"uri": "valid3"},
        ]
        assert extract_uris(tracks) == ["valid1", "valid2", "valid3"]


# =============================================================================
# split_keep_first
# =============================================================================

class TestSplitKeepFirst:
    """Tests for split_keep_first()."""

    def test_keep_zero(self):
        kept, rest = split_keep_first(["a", "b", "c"], 0)
        assert kept == []
        assert rest == ["a", "b", "c"]

    def test_keep_two(self):
        kept, rest = split_keep_first(["a", "b", "c", "d"], 2)
        assert kept == ["a", "b"]
        assert rest == ["c", "d"]

    def test_keep_all(self):
        kept, rest = split_keep_first(["a", "b"], 5)
        assert kept == ["a", "b"]
        assert rest == []

    def test_keep_exact_length(self):
        kept, rest = split_keep_first(["a", "b", "c"], 3)
        assert kept == ["a", "b", "c"]
        assert rest == []

    def test_negative_keep_first(self):
        kept, rest = split_keep_first(["a", "b"], -1)
        assert kept == []
        assert rest == ["a", "b"]

    def test_empty_list(self):
        kept, rest = split_keep_first([], 3)
        assert kept == []
        assert rest == []

    def test_keep_one(self):
        kept, rest = split_keep_first(["a", "b", "c"], 1)
        assert kept == ["a"]
        assert rest == ["b", "c"]


# =============================================================================
# split_into_sections
# =============================================================================

class TestSplitIntoSections:
    """Tests for split_into_sections()."""

    def test_even_split(self):
        result = split_into_sections(["a", "b", "c", "d"], 2)
        assert result == [["a", "b"], ["c", "d"]]

    def test_uneven_split(self):
        result = split_into_sections(["a", "b", "c", "d", "e"], 2)
        assert result == [["a", "b", "c"], ["d", "e"]]

    def test_more_sections_than_items(self):
        result = split_into_sections(["a", "b"], 5)
        assert result == [["a"], ["b"]]

    def test_single_section(self):
        result = split_into_sections(["a", "b", "c"], 1)
        assert result == [["a", "b", "c"]]

    def test_empty_list(self):
        result = split_into_sections([], 3)
        assert result == []

    def test_seven_items_three_sections(self):
        result = split_into_sections(list("abcdefg"), 3)
        # 7/3 = base 2, remainder 1 → first section gets +1
        assert result == [["a", "b", "c"], ["d", "e"], ["f", "g"]]

    def test_remainder_distributed_to_first_sections(self):
        result = split_into_sections(list("abcde"), 3)
        # 5 / 3 = 1 remainder 2, so first 2 sections get +1
        assert result == [["a", "b"], ["c", "d"], ["e"]]

    def test_one_item_one_section(self):
        result = split_into_sections(["x"], 1)
        assert result == [["x"]]

    def test_items_equal_sections(self):
        result = split_into_sections(["a", "b", "c"], 3)
        assert result == [["a"], ["b"], ["c"]]
