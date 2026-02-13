"""
Tests for PlaylistSnapshotService.

Tests cover create, list, get, restore, delete, cleanup,
and auto-snapshot settings integration.
"""

import pytest

from shuffify.models.db import db
from shuffify.services.user_service import UserService
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
    PlaylistSnapshotError,
    PlaylistSnapshotNotFoundError,
    DEFAULT_MAX_SNAPSHOTS_PER_PLAYLIST,
)
from shuffify.enums import SnapshotType


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app_ctx(db_app):
    """Provide app context with a test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield result.user


class TestPlaylistSnapshotServiceCreate:
    """Tests for create_snapshot."""

    def test_create_snapshot(self, app_ctx):
        user = app_ctx
        uris = ["spotify:track:a", "spotify:track:b"]
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="playlist1",
            playlist_name="My Playlist",
            track_uris=uris,
            snapshot_type=SnapshotType.MANUAL,
        )

        assert snap.id is not None
        assert snap.track_uris == uris
        assert snap.track_count == 2
        assert snap.snapshot_type == SnapshotType.MANUAL
        assert snap.playlist_name == "My Playlist"

    def test_create_snapshot_auto_type(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:x"],
            snapshot_type=SnapshotType.AUTO_PRE_SHUFFLE,
            trigger_description="Before BasicShuffle",
        )
        assert (
            snap.snapshot_type
            == SnapshotType.AUTO_PRE_SHUFFLE
        )
        assert (
            snap.trigger_description
            == "Before BasicShuffle"
        )

    def test_create_snapshot_empty_uris(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Empty",
            track_uris=[],
            snapshot_type=SnapshotType.MANUAL,
        )
        assert snap.track_count == 0
        assert snap.track_uris == []

    def test_create_snapshot_with_trigger_description(
        self, app_ctx
    ):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:a"],
            snapshot_type=SnapshotType.MANUAL,
            trigger_description="Manual backup",
        )
        assert snap.trigger_description == "Manual backup"

    def test_create_snapshot_without_trigger_description(
        self, app_ctx
    ):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:a"],
            snapshot_type=SnapshotType.MANUAL,
        )
        assert snap.trigger_description is None

    def test_create_snapshot_pre_raid_type(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:a"],
            snapshot_type=SnapshotType.AUTO_PRE_RAID,
        )
        assert (
            snap.snapshot_type
            == SnapshotType.AUTO_PRE_RAID
        )

    def test_create_snapshot_pre_commit_type(
        self, app_ctx
    ):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:a"],
            snapshot_type=SnapshotType.AUTO_PRE_COMMIT,
        )
        assert (
            snap.snapshot_type
            == SnapshotType.AUTO_PRE_COMMIT
        )

    def test_create_snapshot_scheduled_type(
        self, app_ctx
    ):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:a"],
            snapshot_type=(
                SnapshotType.SCHEDULED_PRE_EXECUTION
            ),
        )
        assert (
            snap.snapshot_type
            == SnapshotType.SCHEDULED_PRE_EXECUTION
        )

    def test_create_snapshot_enforces_retention(
        self, app_ctx
    ):
        """Should delete oldest snapshots beyond the max."""
        user = app_ctx
        # Read the actual max for this user (UserSettings
        # may already exist with a different default)
        max_count = (
            PlaylistSnapshotService._get_max_snapshots(
                user.id
            )
        )
        for i in range(max_count + 2):
            PlaylistSnapshotService.create_snapshot(
                user_id=user.id,
                playlist_id="p1",
                playlist_name="Test",
                track_uris=[f"spotify:track:{i}"],
                snapshot_type=SnapshotType.MANUAL,
            )

        snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1", limit=200
        )
        assert len(snaps) == max_count

    def test_create_snapshot_large_track_list(
        self, app_ctx
    ):
        """Should handle large playlists."""
        user = app_ctx
        uris = [
            f"spotify:track:{i}" for i in range(500)
        ]
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Big Playlist",
            track_uris=uris,
            snapshot_type=SnapshotType.MANUAL,
        )
        assert snap.track_count == 500
        assert len(snap.track_uris) == 500

    def test_create_snapshot_has_created_at(
        self, app_ctx
    ):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:a"],
            snapshot_type=SnapshotType.MANUAL,
        )
        assert snap.created_at is not None


class TestPlaylistSnapshotServiceGet:
    """Tests for get_snapshots and get_snapshot."""

    def test_get_snapshots_returns_newest_first(
        self, app_ctx
    ):
        user = app_ctx
        PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "First",
            ["spotify:track:a"],
            SnapshotType.MANUAL,
        )
        PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "Second",
            ["spotify:track:b"],
            SnapshotType.MANUAL,
        )

        snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1"
        )
        assert len(snaps) == 2
        assert snaps[0].playlist_name == "Second"

    def test_get_snapshots_filters_by_playlist(
        self, app_ctx
    ):
        user = app_ctx
        PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "A",
            ["spotify:track:a"],
            SnapshotType.MANUAL,
        )
        PlaylistSnapshotService.create_snapshot(
            user.id,
            "p2",
            "B",
            ["spotify:track:b"],
            SnapshotType.MANUAL,
        )

        snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1"
        )
        assert len(snaps) == 1

    def test_get_snapshots_respects_limit(
        self, app_ctx
    ):
        user = app_ctx
        for i in range(5):
            PlaylistSnapshotService.create_snapshot(
                user.id,
                "p1",
                f"S{i}",
                [f"spotify:track:{i}"],
                SnapshotType.MANUAL,
            )

        snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1", limit=3
        )
        assert len(snaps) == 3

    def test_get_snapshots_empty(self, app_ctx):
        user = app_ctx
        snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "nonexistent"
        )
        assert snaps == []

    def test_get_snapshot_by_id(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "Test",
            ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        result = PlaylistSnapshotService.get_snapshot(
            snap.id, user.id
        )
        assert result.id == snap.id

    def test_get_snapshot_wrong_user_raises(
        self, app_ctx
    ):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "Test",
            ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        other_result = UserService.upsert_from_spotify({
            "id": "other_user",
            "display_name": "Other",
            "images": [],
        })

        with pytest.raises(
            PlaylistSnapshotNotFoundError
        ):
            PlaylistSnapshotService.get_snapshot(
                snap.id, other_result.user.id
            )

    def test_get_snapshot_nonexistent_raises(
        self, app_ctx
    ):
        user = app_ctx
        with pytest.raises(
            PlaylistSnapshotNotFoundError
        ):
            PlaylistSnapshotService.get_snapshot(
                99999, user.id
            )


class TestPlaylistSnapshotServiceRestore:
    """Tests for restore_snapshot."""

    def test_restore_returns_track_uris(self, app_ctx):
        user = app_ctx
        uris = [
            "spotify:track:a",
            "spotify:track:b",
            "spotify:track:c",
        ]
        snap = PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "Test",
            uris,
            SnapshotType.MANUAL,
        )

        result = PlaylistSnapshotService.restore_snapshot(
            snap.id, user.id
        )
        assert result == uris

    def test_restore_wrong_user_raises(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "Test",
            ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        other_result = UserService.upsert_from_spotify({
            "id": "other",
            "display_name": "Other",
            "images": [],
        })

        with pytest.raises(
            PlaylistSnapshotNotFoundError
        ):
            PlaylistSnapshotService.restore_snapshot(
                snap.id, other_result.user.id
            )

    def test_restore_nonexistent_raises(self, app_ctx):
        user = app_ctx
        with pytest.raises(
            PlaylistSnapshotNotFoundError
        ):
            PlaylistSnapshotService.restore_snapshot(
                99999, user.id
            )

    def test_restore_preserves_track_order(
        self, app_ctx
    ):
        """Track URIs should come back in the same order."""
        user = app_ctx
        uris = [
            "spotify:track:z",
            "spotify:track:a",
            "spotify:track:m",
        ]
        snap = PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "Test",
            uris,
            SnapshotType.MANUAL,
        )
        result = PlaylistSnapshotService.restore_snapshot(
            snap.id, user.id
        )
        assert result == uris


class TestPlaylistSnapshotServiceDelete:
    """Tests for delete_snapshot."""

    def test_delete_snapshot(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "Test",
            ["spotify:track:a"],
            SnapshotType.MANUAL,
        )
        snap_id = snap.id

        result = PlaylistSnapshotService.delete_snapshot(
            snap_id, user.id
        )
        assert result is True

        with pytest.raises(
            PlaylistSnapshotNotFoundError
        ):
            PlaylistSnapshotService.get_snapshot(
                snap_id, user.id
            )

    def test_delete_wrong_user_raises(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "Test",
            ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        other_result = UserService.upsert_from_spotify({
            "id": "other",
            "display_name": "Other",
            "images": [],
        })

        with pytest.raises(
            PlaylistSnapshotNotFoundError
        ):
            PlaylistSnapshotService.delete_snapshot(
                snap.id, other_result.user.id
            )

    def test_delete_nonexistent_raises(self, app_ctx):
        user = app_ctx
        with pytest.raises(
            PlaylistSnapshotNotFoundError
        ):
            PlaylistSnapshotService.delete_snapshot(
                99999, user.id
            )

    def test_delete_does_not_affect_other_snapshots(
        self, app_ctx
    ):
        user = app_ctx
        snap1 = PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "A",
            ["spotify:track:a"],
            SnapshotType.MANUAL,
        )
        snap2 = PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "B",
            ["spotify:track:b"],
            SnapshotType.MANUAL,
        )

        PlaylistSnapshotService.delete_snapshot(
            snap1.id, user.id
        )

        # snap2 should still exist
        result = PlaylistSnapshotService.get_snapshot(
            snap2.id, user.id
        )
        assert result.id == snap2.id


class TestPlaylistSnapshotServiceCleanup:
    """Tests for cleanup_old_snapshots."""

    def test_cleanup_deletes_oldest(self, app_ctx):
        user = app_ctx
        for i in range(5):
            PlaylistSnapshotService.create_snapshot(
                user.id,
                "p1",
                f"S{i}",
                [f"spotify:track:{i}"],
                SnapshotType.MANUAL,
            )

        deleted = (
            PlaylistSnapshotService.cleanup_old_snapshots(
                user.id, "p1", max_count=3
            )
        )
        assert deleted == 2

        remaining = (
            PlaylistSnapshotService.get_snapshots(
                user.id, "p1", limit=100
            )
        )
        assert len(remaining) == 3

    def test_cleanup_noop_when_under_limit(
        self, app_ctx
    ):
        user = app_ctx
        PlaylistSnapshotService.create_snapshot(
            user.id,
            "p1",
            "Test",
            ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        deleted = (
            PlaylistSnapshotService.cleanup_old_snapshots(
                user.id, "p1", max_count=10
            )
        )
        assert deleted == 0

    def test_cleanup_is_per_playlist(self, app_ctx):
        user = app_ctx
        for i in range(3):
            PlaylistSnapshotService.create_snapshot(
                user.id,
                "p1",
                f"S{i}",
                [f"spotify:track:{i}"],
                SnapshotType.MANUAL,
            )
            PlaylistSnapshotService.create_snapshot(
                user.id,
                "p2",
                f"S{i}",
                [f"spotify:track:{i}"],
                SnapshotType.MANUAL,
            )

        PlaylistSnapshotService.cleanup_old_snapshots(
            user.id, "p1", max_count=1
        )

        p1_snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1", limit=100
        )
        p2_snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p2", limit=100
        )
        assert len(p1_snaps) == 1
        assert len(p2_snaps) == 3  # Untouched

    def test_cleanup_noop_when_empty(self, app_ctx):
        user = app_ctx
        deleted = (
            PlaylistSnapshotService.cleanup_old_snapshots(
                user.id, "p1", max_count=10
            )
        )
        assert deleted == 0

    def test_cleanup_preserves_newest(self, app_ctx):
        """Newest snapshots should survive cleanup."""
        user = app_ctx
        snaps = []
        for i in range(5):
            s = PlaylistSnapshotService.create_snapshot(
                user.id,
                "p1",
                f"S{i}",
                [f"spotify:track:{i}"],
                SnapshotType.MANUAL,
            )
            snaps.append(s)

        PlaylistSnapshotService.cleanup_old_snapshots(
            user.id, "p1", max_count=2
        )

        remaining = (
            PlaylistSnapshotService.get_snapshots(
                user.id, "p1", limit=100
            )
        )
        remaining_ids = {s.id for s in remaining}
        # The newest two should survive
        assert snaps[-1].id in remaining_ids
        assert snaps[-2].id in remaining_ids


class TestPlaylistSnapshotServiceAutoEnabled:
    """Tests for is_auto_snapshot_enabled."""

    def test_defaults_to_true(self, app_ctx):
        user = app_ctx
        assert (
            PlaylistSnapshotService
            .is_auto_snapshot_enabled(user.id)
            is True
        )

    def test_respects_user_settings_disabled(
        self, app_ctx
    ):
        """When UserSettings has auto_snapshot_enabled=False."""
        user = app_ctx
        from shuffify.models.db import UserSettings

        settings = UserSettings.query.filter_by(
            user_id=user.id
        ).first()
        if settings:
            settings.auto_snapshot_enabled = False
        else:
            settings = UserSettings(
                user_id=user.id,
                auto_snapshot_enabled=False,
            )
            db.session.add(settings)
        db.session.commit()

        assert (
            PlaylistSnapshotService
            .is_auto_snapshot_enabled(user.id)
            is False
        )

    def test_respects_user_settings_enabled(
        self, app_ctx
    ):
        """When UserSettings has auto_snapshot_enabled=True."""
        user = app_ctx
        from shuffify.models.db import UserSettings

        settings = UserSettings.query.filter_by(
            user_id=user.id
        ).first()
        if settings:
            settings.auto_snapshot_enabled = True
        else:
            settings = UserSettings(
                user_id=user.id,
                auto_snapshot_enabled=True,
            )
            db.session.add(settings)
        db.session.commit()

        assert (
            PlaylistSnapshotService
            .is_auto_snapshot_enabled(user.id)
            is True
        )


class TestPlaylistSnapshotServiceMaxSnapshots:
    """Tests for _get_max_snapshots."""

    def test_returns_user_setting_value(self, app_ctx):
        """Should return the value from UserSettings."""
        user = app_ctx
        from shuffify.models.db import UserSettings

        settings = UserSettings.query.filter_by(
            user_id=user.id
        ).first()
        if settings:
            expected = settings.max_snapshots_per_playlist
        else:
            expected = DEFAULT_MAX_SNAPSHOTS_PER_PLAYLIST

        result = (
            PlaylistSnapshotService._get_max_snapshots(
                user.id
            )
        )
        assert result == expected

    def test_respects_user_settings(self, app_ctx):
        user = app_ctx
        from shuffify.models.db import UserSettings

        settings = UserSettings.query.filter_by(
            user_id=user.id
        ).first()
        if settings:
            settings.max_snapshots_per_playlist = 25
        else:
            settings = UserSettings(
                user_id=user.id,
                max_snapshots_per_playlist=25,
            )
            db.session.add(settings)
        db.session.commit()

        result = (
            PlaylistSnapshotService._get_max_snapshots(
                user.id
            )
        )
        assert result == 25


class TestPlaylistSnapshotToDict:
    """Tests for PlaylistSnapshot.to_dict()."""

    def test_to_dict_contains_all_fields(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:a"],
            snapshot_type=SnapshotType.MANUAL,
            trigger_description="test desc",
        )

        d = snap.to_dict()
        assert d["id"] == snap.id
        assert d["user_id"] == user.id
        assert d["playlist_id"] == "p1"
        assert d["playlist_name"] == "Test"
        assert d["track_uris"] == ["spotify:track:a"]
        assert d["track_count"] == 1
        assert d["snapshot_type"] == SnapshotType.MANUAL
        assert d["trigger_description"] == "test desc"
        assert d["created_at"] is not None

    def test_repr(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:a"],
            snapshot_type=SnapshotType.MANUAL,
        )
        r = repr(snap)
        assert "PlaylistSnapshot" in r
        assert "p1" in r
