"""Guards on PlaylistSnapshotService.auto_snapshot_if_enabled, which the
live actions share: the user's setting, a non-empty track list, and a snapshot
failure never taking down the operation it protects.

The live action tests mock this helper out, so these are where the guards are
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
