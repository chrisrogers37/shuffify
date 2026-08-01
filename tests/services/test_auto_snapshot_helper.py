"""Guards on PlaylistSnapshotService.auto_snapshot_if_enabled.

The four executors used to spell these three guards out separately -- the
user's setting, a non-empty track list, and never letting a snapshot failure
take down the operation it was protecting. Spelling them out four times is what
let them drift: the scheduled shuffle ended up writing a different snapshot_type
from the interactive one, and only rotate guarded on an empty list.

The executor tests mock this helper out, so these are where the guards are
actually proved.
"""

from unittest.mock import patch

from shuffify.enums import SnapshotType
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)

ARGS = dict(
    user_id=1,
    playlist_id="pl1",
    playlist_name="Mine",
    track_uris=["spotify:track:1"],
    snapshot_type=SnapshotType.AUTO_PRE_SHUFFLE,
    trigger_description="Before something",
)


class TestAutoSnapshotIfEnabled:
    def test_creates_a_snapshot_when_enabled(self):
        with patch.object(
            PlaylistSnapshotService, "is_auto_snapshot_enabled", return_value=True
        ):
            with patch.object(
                PlaylistSnapshotService, "create_snapshot", return_value="snap"
            ) as create:
                result = PlaylistSnapshotService.auto_snapshot_if_enabled(**ARGS)

        assert result == "snap"
        create.assert_called_once()
        assert create.call_args.kwargs["playlist_id"] == "pl1"
        assert (
            create.call_args.kwargs["snapshot_type"]
            == SnapshotType.AUTO_PRE_SHUFFLE
        )

    def test_skips_when_the_user_has_it_off(self):
        with patch.object(
            PlaylistSnapshotService, "is_auto_snapshot_enabled", return_value=False
        ):
            with patch.object(
                PlaylistSnapshotService, "create_snapshot"
            ) as create:
                result = PlaylistSnapshotService.auto_snapshot_if_enabled(**ARGS)

        assert result is None
        create.assert_not_called()

    def test_skips_an_empty_track_list(self):
        """An empty snapshot restores nothing, so it is not worth a row."""
        with patch.object(
            PlaylistSnapshotService, "is_auto_snapshot_enabled", return_value=True
        ):
            with patch.object(
                PlaylistSnapshotService, "create_snapshot"
            ) as create:
                result = PlaylistSnapshotService.auto_snapshot_if_enabled(
                    **{**ARGS, "track_uris": []}
                )

        assert result is None
        create.assert_not_called()

    def test_a_failing_snapshot_never_raises(self):
        """The snapshot protects the operation; it must not be able to kill it."""
        with patch.object(
            PlaylistSnapshotService, "is_auto_snapshot_enabled", return_value=True
        ):
            with patch.object(
                PlaylistSnapshotService,
                "create_snapshot",
                side_effect=Exception("snap fail"),
            ):
                result = PlaylistSnapshotService.auto_snapshot_if_enabled(**ARGS)

        assert result is None

    def test_a_failing_enabled_check_never_raises(self):
        with patch.object(
            PlaylistSnapshotService,
            "is_auto_snapshot_enabled",
            side_effect=Exception("db down"),
        ):
            result = PlaylistSnapshotService.auto_snapshot_if_enabled(**ARGS)

        assert result is None


class TestScheduledShuffleSnapshotType:
    """A scheduled shuffle must label its snapshot like an interactive one.

    routes/shuffle.py writes AUTO_PRE_SHUFFLE and every sibling executor writes
    its own AUTO_PRE_*, but the scheduled shuffle wrote SCHEDULED_PRE_EXECUTION.
    The snapshot list renders those differently -- "Pre-Shuffle" versus
    "Auto-backup" -- so the same operation was labelled by what triggered it.
    """

    def test_scheduled_shuffle_uses_auto_pre_shuffle(self):
        from unittest.mock import MagicMock

        from shuffify.services.executors import shuffle_executor

        schedule = MagicMock()
        schedule.user_id = 1
        schedule.target_playlist_id = "pl1"
        schedule.target_playlist_name = "Mine"

        with patch.object(
            shuffle_executor, "PlaylistSnapshotService"
        ) as snap:
            shuffle_executor._auto_snapshot_before_shuffle(
                schedule, [{"uri": "spotify:track:1"}], "BasicShuffle"
            )

        kwargs = snap.auto_snapshot_if_enabled.call_args.kwargs
        assert kwargs["snapshot_type"] == SnapshotType.AUTO_PRE_SHUFFLE

    def test_every_executor_uses_its_own_auto_pre_type(self):
        """No executor may fall back to the generic scheduled type.

        Walks the AST rather than grepping: the word also appears in a comment
        explaining why it was dropped, and a text match would read that as a
        violation.
        """
        import ast
        import pathlib

        import shuffify.services.executors as pkg

        root = pathlib.Path(pkg.__file__).parent
        offenders = []
        for name in ("raid", "drip", "shuffle", "rotate"):
            tree = ast.parse((root / f"{name}_executor.py").read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "SCHEDULED_PRE_EXECUTION"
                ):
                    offenders.append(name)

        assert offenders == []
