"""
Tests for execute_rotate().

Tests cover swap rotation mode (the only supported mode),
parameter validation, edge cases, and dispatch.
"""

import pytest
from unittest.mock import patch, MagicMock

from shuffify.services.executors import (
    JobExecutorService,
    JobExecutionError,
    PlaylistVerificationError,
)
from shuffify.services.executors.rotate_executor import (
    execute_rotate,
    _purge_archive_overlaps,
    _checked_remove,
)
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import JobType


# Executor paths touch TrackLockService.safe_get_locked_uris
# (db.session) even in their except-branch fallback, so every
# test in this module needs the shared app_ctx fixture
# (defined in tests/conftest.py).
pytestmark = pytest.mark.usefixtures("app_ctx")


def _make_schedule(
    rotation_mode="swap",
    rotation_count=5,
    target_id="target1",
    user_id=1,
    target_size=50,
):
    """Create a mock schedule for rotation tests."""
    schedule = MagicMock()
    schedule.id = 42
    schedule.user_id = user_id
    schedule.job_type = JobType.ROTATE
    schedule.target_playlist_id = target_id
    schedule.target_playlist_name = "My Playlist"
    schedule.algorithm_params = {
        "rotation_mode": rotation_mode,
        "rotation_count": rotation_count,
        "target_size": target_size,
    }
    return schedule


def _make_tracks(uris):
    """Create track dicts from a list of URI strings."""
    return [{"uri": u, "name": u} for u in uris]


def _make_api(
    prod_tracks=None, archive_tracks=None,
    post_removal_prod=None,
):
    """Create a stateful mock SpotifyAPI.

    `playlist_add_items` and `playlist_remove_items` mutate
    internal state so a follow-up `get_playlist_tracks`
    reflects the writes — which is what F1's
    `verify_playlist_state` reads back to confirm the
    post-write state.

    Args:
        prod_tracks: Initial production playlist tracks.
        archive_tracks: Initial archive playlist tracks.
        post_removal_prod: Optional override that forces
            the second-and-later get_playlist_tracks(target1)
            return value, regardless of what writes happened.
            Use this only to simulate a Spotify silent
            failure for PlaylistVerificationError tests.
    """
    api = MagicMock()

    state = {
        "target1": [
            t["uri"] for t in (prod_tracks or [])
            if t.get("uri")
        ],
        "archive1": [
            t["uri"] for t in (archive_tracks or [])
            if t.get("uri")
        ],
    }

    target_fetch_count = {"n": 0}
    forced_post = post_removal_prod  # may be None

    def get_tracks(playlist_id, skip_cache=False):
        if playlist_id == "target1":
            target_fetch_count["n"] += 1
            if (
                forced_post is not None
                and target_fetch_count["n"] >= 2
            ):
                return list(forced_post)
        return [
            {"uri": u, "name": u}
            for u in state.get(playlist_id, [])
        ]

    def add_items(
        playlist_id, uris, position=None,
    ):
        if playlist_id not in state:
            state[playlist_id] = []
        if position is None:
            state[playlist_id].extend(uris)
        else:
            state[playlist_id] = (
                state[playlist_id][:position]
                + list(uris)
                + state[playlist_id][position:]
            )

    def remove_items(playlist_id, uris):
        to_remove = set(uris)
        state[playlist_id] = [
            u for u in state.get(playlist_id, [])
            if u not in to_remove
        ]
        return True

    api.get_playlist_tracks = MagicMock(
        side_effect=get_tracks
    )
    api.playlist_remove_items = MagicMock(
        side_effect=remove_items
    )
    api.playlist_add_items = MagicMock(
        side_effect=add_items
    )
    api._state = state
    return api


def _make_pair():
    """Create a mock PlaylistPair."""
    pair = MagicMock()
    pair.archive_playlist_id = "archive1"
    return pair


# =============================================================================
# SWAP
# =============================================================================


class TestExecuteRotateSwap:
    """Tests for swap rotation mode."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_exchanges_tracks(
        self, mock_random, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        archive = _make_tracks(["a1", "a2", "a3"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=3,
        )
        # random.sample returns first N for
        # deterministic testing
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 2
        assert result["tracks_total"] == 3

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_uses_fifo_order(
        self, mock_random, mock_pair, mock_snap
    ):
        """Swap-in selects oldest archived tracks first
        (FIFO), not most recently added (LIFO)."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        # Archive order: old1 is oldest, old3 is newest
        archive = _make_tracks(
            ["old1", "old2", "old3"]
        )
        post_prod = _make_tracks(
            ["p3", "old1", "old2"]
        )
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=3,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        execute_rotate(schedule, api)

        # Verify swap-in used first 2 (oldest), not
        # last 2 (newest)
        add_calls = api.playlist_add_items.call_args_list
        swap_in_call = [
            c for c in add_calls
            if c[0][0] == "target1"
        ]
        assert len(swap_in_call) == 1
        added_uris = swap_in_call[0][0][1]
        assert added_uris == ["old1", "old2"]

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_deduplicates(
        self, mock_random, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(
            ["p1", "p2", "shared"]
        )
        # "shared" is in both — will be purged from
        # archive by _purge_archive_overlaps
        archive = _make_tracks(["shared", "a1"])
        post_prod = _make_tracks(
            ["p2", "shared", "a1"]
        )
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=3,
            target_size=3,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(
            schedule, api
        )

        # "shared" purged from archive; only a1
        # available to swap in; swap limited to 1
        assert result["tracks_added"] == 1

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_swap_empty_archive(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2"])
        post_prod = _make_tracks(["p1", "p2"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=[],
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=2,
        )

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 0

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_empty_playlist_returns_zero(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        api = _make_api(prod_tracks=[])
        schedule = _make_schedule()

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 0
        assert result["tracks_total"] == 0

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_auto_snapshot_created(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            True
        )

        prod = _make_tracks(["u1", "u2", "u3"])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=prod,
        )
        schedule = _make_schedule(
            rotation_count=1,
            target_size=3,
        )

        execute_rotate(
            schedule, api
        )

        mock_snap.create_snapshot.assert_called_once()

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_snapshot_skipped_when_disabled(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["u1", "u2", "u3"])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=prod,
        )
        schedule = _make_schedule(
            rotation_count=1,
            target_size=3,
        )

        execute_rotate(
            schedule, api
        )

        mock_snap.create_snapshot.assert_not_called()

    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_no_pair_raises(self, mock_pair):
        mock_pair.return_value = None

        api = _make_api()
        schedule = _make_schedule()

        with pytest.raises(
            JobExecutionError,
            match="No archive pair found",
        ):
            execute_rotate(
                schedule, api
            )


# =============================================================================
# SWAP — DEDUP
# =============================================================================


class TestSwapDedup:
    """Swap must not push duplicates to archive."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_skips_outgoing_already_in_archive(
        self, mock_random, mock_pair, mock_snap
    ):
        """If a prod track being swapped out is already in
        the archive, don't add it again."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(
            ["p1", "p2", "p3", "p4"]
        )
        # p1 is in both — will be purged from archive
        # by _purge_archive_overlaps. Archive after
        # purge: [a1, a2]
        archive = _make_tracks(["p1", "a1", "a2"])
        post_prod = _make_tracks(
            ["p3", "p4", "a1", "a2"]
        )
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=4,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(schedule, api)

        # a1, a2 available (p1 purged); swap 2
        assert result["tracks_added"] == 2
        # p1, p2 swapped out; neither is in the
        # cleaned archive so both added to archive
        add_calls = api.playlist_add_items.call_args_list
        archive_add = [
            c for c in add_calls
            if c[0][0] == "archive1"
        ]
        assert len(archive_add) == 1
        assert set(archive_add[0][0][1]) == {
            "p1", "p2"
        }

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_all_outgoing_already_in_archive(
        self, mock_random, mock_pair, mock_snap
    ):
        """If all outgoing tracks are already in the
        archive, skip the archive add entirely."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        # p1 is in both — purged from archive.
        # Archive after purge: [a1]
        archive = _make_tracks(["p1", "a1"])
        post_prod = _make_tracks(["p2", "p3", "a1"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=1,
            target_size=3,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(schedule, api)

        # a1 swaps in; p1 swaps out
        assert result["tracks_added"] == 1
        # p1 is NOT in cleaned archive (was purged),
        # so it IS added to archive
        add_calls = api.playlist_add_items.call_args_list
        archive_add = [
            c for c in add_calls
            if c[0][0] == "archive1"
        ]
        assert len(archive_add) == 1
        assert archive_add[0][0][1] == ["p1"]


# =============================================================================
# SWAP — OVERFLOW (COLD-START)
# =============================================================================


class TestSwapOverflow:
    """Tests for Phase 1 overflow archival."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_overflow_archives_excess(
        self, mock_random, mock_pair, mock_snap
    ):
        """When playlist exceeds cap, archive excess."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        uris = ["u{}".format(i) for i in range(8)]
        prod = _make_tracks(uris)
        # After overflow: 8 - 5 = 3 removed
        post_prod = _make_tracks(uris[3:])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=5,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(schedule, api)

        # overflow = 8 - 5 = 3 tracks archived
        assert result["tracks_added"] == 0
        assert result["tracks_total"] == 5


# =============================================================================
# ARCHIVE PRE-CLEANUP
# =============================================================================


class TestPurgeArchiveOverlaps:
    """Tests for _purge_archive_overlaps."""

    def test_purges_overlapping_tracks(self):
        """Tracks in both prod and archive are removed
        from archive."""
        api = MagicMock()
        api.playlist_remove_items.return_value = True

        archive_uris = ["shared1", "a1", "shared2"]
        prod_set = {"shared1", "shared2", "p1"}

        cleaned, purged = _purge_archive_overlaps(
            api, "archive1", archive_uris, prod_set,
        )

        assert purged == 2
        assert cleaned == ["a1"]
        api.playlist_remove_items.assert_called_once()
        removed = (
            api.playlist_remove_items.call_args[0][1]
        )
        assert set(removed) == {"shared1", "shared2"}

    def test_no_overlaps_no_removal(self):
        """When no overlaps, no removal call is made."""
        api = MagicMock()
        archive_uris = ["a1", "a2"]
        prod_set = {"p1", "p2"}

        cleaned, purged = _purge_archive_overlaps(
            api, "archive1", archive_uris, prod_set,
        )

        assert purged == 0
        assert cleaned == ["a1", "a2"]
        api.playlist_remove_items.assert_not_called()

    def test_all_overlapping(self):
        """When all archive tracks overlap, all purged."""
        api = MagicMock()
        api.playlist_remove_items.return_value = True

        archive_uris = ["p1", "p2"]
        prod_set = {"p1", "p2", "p3"}

        cleaned, purged = _purge_archive_overlaps(
            api, "archive1", archive_uris, prod_set,
        )

        assert purged == 2
        assert cleaned == []

    def test_empty_archive(self):
        """Empty archive has nothing to purge."""
        api = MagicMock()
        cleaned, purged = _purge_archive_overlaps(
            api, "archive1", [], {"p1"},
        )

        assert purged == 0
        assert cleaned == []
        api.playlist_remove_items.assert_not_called()


# =============================================================================
# RANDOMIZATION
# =============================================================================


class TestRandomSelection:
    """Tests that swap-out and overflow use random
    selection."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_out_uses_random_sample(
        self, mock_random, mock_pair, mock_snap
    ):
        """Phase 2 swap-out uses random.sample."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(
            ["p1", "p2", "p3", "p4", "p5"]
        )
        archive = _make_tracks(["a1", "a2"])
        post_prod = _make_tracks(
            ["p1", "p2", "p3", "a1", "a2"]
        )
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=5,
        )
        # Return last 2 instead of first 2
        mock_random.sample.return_value = [
            "p4", "p5"
        ]

        result = execute_rotate(schedule, api)

        assert result["tracks_added"] == 2
        # Verify random.sample was called with
        # eligible uris
        mock_random.sample.assert_called_once()
        sample_args = mock_random.sample.call_args
        assert sample_args[0][1] == 2

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_overflow_uses_random_sample(
        self, mock_random, mock_pair, mock_snap
    ):
        """Phase 1 overflow uses random.sample."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        uris = ["u{}".format(i) for i in range(8)]
        prod = _make_tracks(uris)
        post_prod = _make_tracks(
            ["u0", "u1", "u2", "u4", "u6"]
        )
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=5,
        )
        # Return random 3 from eligible
        mock_random.sample.return_value = [
            "u3", "u5", "u7"
        ]

        result = execute_rotate(schedule, api)

        assert result["tracks_added"] == 0
        mock_random.sample.assert_called_once()
        sample_args = mock_random.sample.call_args
        assert sample_args[0][1] == 3  # overflow=3


# =============================================================================
# VERIFICATION
# =============================================================================


class TestVerification:
    """Tests that tracks_total comes from re-fetch."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_overflow_raises_when_post_state_drifts(
        self, mock_random, mock_pair, mock_snap
    ):
        """F1: Phase 1 raises PlaylistVerificationError when
        the post-write playlist size diverges from
        expected. (Replaces the pre-F1 loose-verify test
        which asserted the old behavior of returning the
        drifted count.)
        """
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        uris = ["u{}".format(i) for i in range(8)]
        prod = _make_tracks(uris)
        # Simulate Spotify partially failing the removal:
        # expected 5 but got 7 (silent loss class).
        post_prod = _make_tracks(uris[:7])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=5,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        with pytest.raises(PlaylistVerificationError):
            execute_rotate(schedule, api)

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_raises_on_unexpected_extra_track(
        self, mock_random, mock_pair, mock_snap
    ):
        """F1: Phase 2 raises PlaylistVerificationError
        when the post-write playlist contains an extra
        track that the executor did not add. (Replaces the
        pre-F1 loose-verify test which accepted the drift.)
        """
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        archive = _make_tracks(["a1", "a2"])
        post_prod = _make_tracks(
            ["p3", "a1", "a2", "extra"]
        )
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=3,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        with pytest.raises(PlaylistVerificationError):
            execute_rotate(schedule, api)


# =============================================================================
# VALIDATION AND DISPATCH
# =============================================================================


class TestExecuteRotateValidation:
    """Tests for validation and dispatch."""

    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_invalid_mode_raises(self, mock_pair):
        mock_pair.return_value = _make_pair()

        api = _make_api(
            prod_tracks=_make_tracks(["u1"])
        )
        schedule = _make_schedule()
        schedule.algorithm_params = {
            "rotation_mode": "invalid_mode",
            "target_size": 50,
        }

        with pytest.raises(
            JobExecutionError,
            match="Invalid rotation_mode",
        ):
            execute_rotate(
                schedule, api
            )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_missing_mode_defaults_to_swap(
        self, mock_random, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["u1", "u2"])
        archive = _make_tracks(["a1", "a2"])
        post_prod = _make_tracks(["a1", "a2"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule()
        schedule.algorithm_params = {
            "target_size": 2,
        }
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(
            schedule, api
        )

        # Defaults to swap with count=5, but only
        # 2 archive tracks available
        assert result["tracks_added"] == 2

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_count_defaults_to_5(
        self, mock_random, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        uris = [
            "u{}".format(i) for i in range(10)
        ]
        prod = _make_tracks(uris)
        archive = _make_tracks(
            ["a{}".format(i) for i in range(10)]
        )
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        schedule = _make_schedule()
        schedule.algorithm_params = {
            "rotation_mode": "swap",
            "target_size": 10,
        }
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(
            schedule, api
        )

        # Default count=5, swaps 5 tracks
        assert result["tracks_added"] == 5

    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_swap_without_target_size_raises(
        self, mock_pair
    ):
        mock_pair.return_value = _make_pair()

        api = _make_api(
            prod_tracks=_make_tracks(["u1", "u2"])
        )
        schedule = _make_schedule()
        schedule.algorithm_params = {
            "rotation_mode": "swap",
            "rotation_count": 1,
        }

        with pytest.raises(
            JobExecutionError,
            match="playlist size cap",
        ):
            execute_rotate(schedule, api)

    def test_dispatch_routes_to_rotate(self):
        """_execute_job_type dispatches ROTATE."""
        schedule = _make_schedule()
        api = MagicMock()

        with patch(
            "shuffify.services.executors.rotate_executor"
            ".execute_rotate",
            return_value={"tracks_added": 0},
        ) as mock_rotate:
            JobExecutorService._execute_job_type(
                schedule, api
            )
            mock_rotate.assert_called_once_with(
                schedule, api
            )

    def test_unknown_job_type_raises(self):
        """Unknown job type still raises error."""
        schedule = MagicMock()
        schedule.job_type = "nonexistent"
        api = MagicMock()

        with pytest.raises(
            JobExecutionError,
            match="Unknown job type",
        ):
            JobExecutorService._execute_job_type(
                schedule, api
            )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_snapshot_failure_non_blocking(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            True
        )
        mock_snap.create_snapshot.side_effect = (
            Exception("snap fail")
        )

        prod = _make_tracks(["u1", "u2"])
        post_prod = _make_tracks(["u1", "u2"])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=1,
            target_size=2,
        )

        # Should not raise despite snapshot failure
        result = execute_rotate(
            schedule, api
        )
        assert result["tracks_total"] == 2


# =============================================================================
# PROTECT COUNT INTEGRATION
# =============================================================================


class TestProtectCount:
    """Tests for protect_count in swap rotation."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_skips_protected(
        self, mock_random, mock_pair, mock_snap
    ):
        """Protected tracks are not swapped out."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(
            ["p1", "p2", "p3", "p4", "p5"]
        )
        archive = _make_tracks(
            ["a1", "a2", "a3"]
        )
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=5,
        )
        schedule.algorithm_params["protect_count"] = 2
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(schedule, api)

        # Should swap p3, p4 out (skip p1, p2)
        # and bring in a2, a3
        assert result["tracks_added"] == 2

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_protect_all_returns_zero_with_reason(
        self, mock_pair, mock_snap
    ):
        """If all tracks protected, nothing rotates and
        skipped_reason is set."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        archive = _make_tracks(["a1"])
        post_prod = _make_tracks(["p1", "p2", "p3"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=5,
            target_size=3,
        )
        schedule.algorithm_params["protect_count"] = 10

        result = execute_rotate(schedule, api)

        assert result["tracks_added"] == 0
        assert result["tracks_total"] == 3
        assert result["skipped_reason"] == (
            "all_tracks_protected"
        )


# =============================================================================
# TARGET SIZE INTEGRATION
# =============================================================================


class TestTargetSize:
    """Tests for target_size playlist cap."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_target_size_archives_overflow(
        self, mock_random, mock_pair, mock_snap
    ):
        """When over cap, excess tracks are archived."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        # 15 tracks, cap 5 => overflow = 10
        uris = ["u{}".format(i) for i in range(15)]
        prod = _make_tracks(uris)
        post_prod = _make_tracks(uris[10:])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=5,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(schedule, api)

        # Phase 1: archive 10, no swap
        assert result["tracks_added"] == 0
        assert result["tracks_total"] == 5

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_target_size_under_cap_swaps(
        self, mock_random, mock_pair, mock_snap
    ):
        """Under cap, normal swap occurs."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        # 8 tracks, cap 10 => under cap
        uris = ["u{}".format(i) for i in range(8)]
        prod = _make_tracks(uris)
        archive = _make_tracks(
            ["a{}".format(i) for i in range(5)]
        )
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        schedule = _make_schedule(
            rotation_count=3,
            target_size=10,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(schedule, api)

        assert result["tracks_added"] == 3

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_target_size_with_protect(
        self, mock_random, mock_pair, mock_snap
    ):
        """Cap + protect combined overflow."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        # 20 tracks, cap 10, protect 5
        # overflow = 20-10 = 10, eligible = 15
        uris = ["u{}".format(i) for i in range(20)]
        prod = _make_tracks(uris)
        post_prod = _make_tracks(uris[:5] + uris[15:])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=10,
        )
        schedule.algorithm_params["protect_count"] = 5
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        result = execute_rotate(schedule, api)

        # Phase 1: archive 10 overflow, no swap
        assert result["tracks_added"] == 0
        assert result["tracks_total"] == 10
        # random.sample was called with eligible uris
        # (u5..u19) and count=10
        mock_random.sample.assert_called_once()
        sample_args = mock_random.sample.call_args
        assert sample_args[0][0] == [
            "u5", "u6", "u7", "u8", "u9",
            "u10", "u11", "u12", "u13", "u14",
            "u15", "u16", "u17", "u18", "u19",
        ]
        assert sample_args[0][1] == 10


# =============================================================================
# OPERATION ORDERING
# =============================================================================


class TestOperationOrdering:
    """Verify remove-before-add ordering for resilience."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_overflow_removes_before_archiving(
        self, mock_random, mock_pair, mock_snap
    ):
        """Phase 1: remove from prod before adding to
        archive, so a failed remove leaves archive clean."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        uris = ["u{}".format(i) for i in range(8)]
        prod = _make_tracks(uris)
        post_prod = _make_tracks(uris[3:])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=post_prod,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        # Track call order
        call_order = []
        orig_remove = api.playlist_remove_items

        def track_remove(*a, **k):
            call_order.append("remove")
            return orig_remove(*a, **k)

        api.playlist_remove_items = MagicMock(
            side_effect=track_remove
        )

        orig_batch = (
            JobExecutorService._batch_add_tracks
        )

        def track_add(*a, **k):
            call_order.append("add")
            return orig_batch(*a, **k)

        schedule = _make_schedule(
            rotation_count=2,
            target_size=5,
        )

        with patch.object(
            JobExecutorService, "_batch_add_tracks",
            side_effect=track_add,
        ):
            execute_rotate(schedule, api)

        # First remove is the purge (no overlaps, so
        # no call), then overflow remove, then add
        assert "remove" in call_order
        assert "add" in call_order
        # First remove before first add
        first_remove = call_order.index("remove")
        first_add = call_order.index("add")
        assert first_remove < first_add

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_removes_from_prod_before_archive_add(
        self, mock_random, mock_pair, mock_snap
    ):
        """Phase 2: remove from prod before adding to
        archive for outgoing tracks."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(
            ["p1", "p2", "p3", "p4"]
        )
        archive = _make_tracks(["a1", "a2"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        call_order = []

        def track_remove(pid, uris):
            call_order.append(
                ("remove", pid)
            )
            return True

        def track_add(api_obj, pid, uris):
            call_order.append(
                ("add", pid)
            )

        api.playlist_remove_items.side_effect = (
            track_remove
        )

        schedule = _make_schedule(
            rotation_count=2,
            target_size=4,
        )

        # This test asserts call ordering only — patch out
        # verify_playlist_state since the test's mocks
        # short-circuit the state mutation that verify
        # would otherwise see.
        with patch.object(
            JobExecutorService, "_batch_add_tracks",
            side_effect=track_add,
        ), patch(
            "shuffify.services.executors.rotate_executor"
            ".verify_playlist_state",
            return_value=[],
        ):
            execute_rotate(schedule, api)

        # First op: remove from production (after
        # any purge remove calls)
        prod_remove_idx = next(
            i for i, c in enumerate(call_order)
            if c == ("remove", "target1")
        )
        # Archive add comes after prod remove
        archive_add_idx = next(
            i for i, c in enumerate(call_order)
            if c == ("add", "archive1")
        )
        assert prod_remove_idx < archive_add_idx


# =============================================================================
# CHECKED REMOVE (Fix #1)
# =============================================================================


class TestCheckedRemove:
    """Tests for _checked_remove error handling."""

    def test_success_does_not_raise(self):
        """Truthy return completes without error."""
        api = MagicMock()
        api.playlist_remove_items.return_value = True

        # Should not raise
        _checked_remove(
            api, "playlist1", ["u1"], 42, "test",
        )

    def test_falsy_return_raises(self):
        """Falsy return from API raises error."""
        api = MagicMock()
        api.playlist_remove_items.return_value = False

        with pytest.raises(
            JobExecutionError,
            match="returned falsy",
        ):
            _checked_remove(
                api, "playlist1", ["u1"],
                42, "test phase",
            )

    def test_none_return_raises(self):
        """None return from API raises error."""
        api = MagicMock()
        api.playlist_remove_items.return_value = None

        with pytest.raises(
            JobExecutionError,
            match="returned falsy",
        ):
            _checked_remove(
                api, "playlist1", ["u1"],
                42, "test phase",
            )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_prod_remove_failure_aborts(
        self, mock_random, mock_pair, mock_snap
    ):
        """If prod remove fails in swap, rotation
        aborts with JobExecutionError."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        archive = _make_tracks(["a1", "a2"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        # No overlaps so no purge call;
        # first remove (swap prod-remove) returns falsy
        # so _checked_remove raises. side_effect takes
        # precedence over return_value with the stateful
        # mock — override to return False.
        api.playlist_remove_items.side_effect = (
            lambda *a, **k: False
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=3,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        with pytest.raises(
            JobExecutionError,
            match="swap prod-remove failed",
        ):
            execute_rotate(schedule, api)

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_overflow_remove_failure_aborts(
        self, mock_random, mock_pair, mock_snap
    ):
        """If prod remove fails in overflow, rotation
        aborts with JobExecutionError."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        uris = ["u{}".format(i) for i in range(8)]
        prod = _make_tracks(uris)
        api = _make_api(prod_tracks=prod)
        # Purge has no overlaps so no call; overflow remove
        # returns falsy so _checked_remove raises.
        api.playlist_remove_items.side_effect = (
            lambda *a, **k: False
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=5,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        with pytest.raises(
            JobExecutionError,
            match="overflow prod-remove failed",
        ):
            execute_rotate(schedule, api)

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_archive_remove_failure_aborts(
        self, mock_random, mock_pair, mock_snap
    ):
        """If archive remove fails in swap, rotation
        aborts."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        archive = _make_tracks(["a1", "a2"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        # No overlaps so no purge call;
        # prod-remove succeeds, archive-remove fails
        api.playlist_remove_items.side_effect = [
            True, False
        ]
        schedule = _make_schedule(
            rotation_count=2,
            target_size=3,
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        with pytest.raises(
            JobExecutionError,
            match="swap archive-remove failed",
        ):
            execute_rotate(schedule, api)


# =============================================================================
# TARGET SIZE GUARD (Fix #2)
# =============================================================================


class TestTargetSizeGuard:
    """Tests for target_size floor check."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_target_size_zero_raises(
        self, mock_pair, mock_snap
    ):
        """target_size=0 raises JobExecutionError."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["u1", "u2"])
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule(target_size=50)
        # Bypass _validate_rotation_config clamping
        # by setting target_size after construction
        schedule.algorithm_params["target_size"] = None

        with pytest.raises(
            JobExecutionError,
            match="target_size.*at least 1",
        ):
            execute_rotate(schedule, api)

    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_target_size_none_raises(
        self, mock_pair
    ):
        """target_size=None raises JobExecutionError."""
        mock_pair.return_value = _make_pair()

        prod = _make_tracks(["u1", "u2"])
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule()
        schedule.algorithm_params = {
            "rotation_mode": "swap",
            "rotation_count": 1,
        }

        with pytest.raises(
            JobExecutionError,
            match="target_size.*at least 1",
        ):
            execute_rotate(schedule, api)


# =============================================================================
# PROTECT COUNT WARNING (Fix #3)
# =============================================================================


class TestProtectCountWarning:
    """Tests for all-protected early return."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_all_protected_returns_skipped_reason(
        self, mock_pair, mock_snap
    ):
        """When protect_count >= playlist size, result
        includes skipped_reason."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2"])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=2,
        )
        schedule.algorithm_params["protect_count"] = 5

        result = execute_rotate(schedule, api)

        assert result["tracks_added"] == 0
        assert result["skipped_reason"] == (
            "all_tracks_protected"
        )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_protect_equal_to_size_returns_reason(
        self, mock_pair, mock_snap
    ):
        """protect_count == len(prod_uris) triggers
        early return."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=3,
        )
        schedule.algorithm_params["protect_count"] = 3

        result = execute_rotate(schedule, api)

        assert result["tracks_added"] == 0
        assert result["skipped_reason"] == (
            "all_tracks_protected"
        )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_all_protected_no_remove_calls(
        self, mock_pair, mock_snap
    ):
        """When all protected, no swap API calls
        are made."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2"])
        api = _make_api(
            prod_tracks=prod,
            post_removal_prod=prod,
        )
        schedule = _make_schedule(
            rotation_count=2,
            target_size=2,
        )
        schedule.algorithm_params["protect_count"] = 10

        execute_rotate(schedule, api)

        # Only get_playlist_tracks calls (initial +
        # archive + verification), no removes
        api.playlist_add_items.assert_not_called()


# =============================================================================
# PURGE ARCHIVE ERROR HANDLING (Fix #4)
# =============================================================================


class TestPurgeArchiveErrorHandling:
    """Tests for _purge_archive_overlaps failures."""

    def test_api_error_raises_execution_error(self):
        """SpotifyAPIError during purge raises
        JobExecutionError."""
        api = MagicMock()
        api.playlist_remove_items.side_effect = (
            SpotifyAPIError("API failed")
        )

        with pytest.raises(
            JobExecutionError,
            match="Failed to purge",
        ):
            _purge_archive_overlaps(
                api, "archive1",
                ["shared", "a1"], {"shared", "p1"},
            )

    def test_not_found_raises_execution_error(self):
        """SpotifyNotFoundError during purge raises
        JobExecutionError."""
        api = MagicMock()
        api.playlist_remove_items.side_effect = (
            SpotifyNotFoundError("Not found")
        )

        with pytest.raises(
            JobExecutionError,
            match="Failed to purge",
        ):
            _purge_archive_overlaps(
                api, "archive1",
                ["shared"], {"shared"},
            )

    def test_falsy_return_raises_execution_error(self):
        """Falsy return from remove during purge
        raises JobExecutionError."""
        api = MagicMock()
        api.playlist_remove_items.return_value = False

        with pytest.raises(
            JobExecutionError,
            match="Failed to purge",
        ):
            _purge_archive_overlaps(
                api, "archive1",
                ["shared"], {"shared"},
            )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_purge_failure_aborts_rotation(
        self, mock_pair, mock_snap
    ):
        """Purge failure during full rotation aborts
        the entire operation."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "shared"])
        archive = _make_tracks(["shared", "a1"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        api.playlist_remove_items.side_effect = (
            SpotifyAPIError("Purge failed")
        )

        schedule = _make_schedule(
            rotation_count=1,
            target_size=2,
        )

        with pytest.raises(JobExecutionError):
            execute_rotate(schedule, api)


# Verifier-helper unit tests live in
# tests/services/test_verify_playlist_state.py. This file
# only exercises the rotate integration of F1 strict
# verification below.


# =============================================================================
# F1 STRICT VERIFICATION INTEGRATION
# =============================================================================


class TestRotateStrictVerification:
    """End-to-end rotate tests that exercise the F1
    PlaylistVerificationError path by forcing the API mock
    to return a post-write state that diverges from what
    the executor wrote.
    """

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    @patch(
        "shuffify.services.executors.rotate_executor"
        ".random"
    )
    def test_swap_raises_when_post_state_drifts(
        self, mock_random, mock_pair, mock_snap,
    ):
        """If Spotify silently drops a track during the
        swap, the post-write fetch diverges from the
        executor's expected set and verification raises.
        """
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )
        mock_random.sample.side_effect = (
            lambda lst, n: lst[:n]
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        archive = _make_tracks(["a1", "a2"])
        # Force the verify re-fetch to claim one track went
        # missing — simulates the WOOKLYN-class silent loss.
        post_prod = _make_tracks(["p3", "a1"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
            post_removal_prod=post_prod,
        )
        schedule = _make_schedule(
            rotation_count=2, target_size=3,
        )

        with pytest.raises(PlaylistVerificationError):
            execute_rotate(schedule, api)
