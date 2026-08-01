"""
Tests for raid link and drip routes.

Tests cover authentication, validation, and basic success paths
for raid playlist link CRUD and drip endpoints.
"""

from unittest.mock import MagicMock, patch

# =============================================================
# Authentication Tests
# =============================================================


class TestRaidLinkAuthRequired:
    """Raid link endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_create_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/raid-link",
                json={"create_new": True},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_update_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.put(
                "/playlist/p1/raid-link",
                json={"drip_count": 5},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_delete_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete(
                "/playlist/p1/raid-link"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_drip_now_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/drip-now",
                json={},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_drip_toggle_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/drip-schedule-toggle"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_source_count_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.put(
                "/playlist/p1/raid-source-count",
                json={
                    "source_id": 1,
                    "raid_count": 5,
                },
            )
            assert resp.status_code == 401


# =============================================================
# Validation Tests
# =============================================================


class TestRaidLinkValidation:
    """Request validation tests."""

    @patch("shuffify.routes.require_auth")
    def test_create_empty_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-link",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_update_no_fields(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.put(
            "/playlist/p1/raid-link",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_update_drip_count_too_low(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.put(
            "/playlist/p1/raid-link",
            json={"drip_count": 0},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_source_count_too_low(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.put(
            "/playlist/p1/raid-source-count",
            json={"source_id": 1, "raid_count": 0},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_create_existing_without_id(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-link",
            json={
                "create_new": False,
                # Missing raid_playlist_id
            },
        )
        assert resp.status_code == 400


# =============================================================
# Success Path Tests
# =============================================================


class TestRaidLinkSuccess:
    """Basic success path tests."""

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.raid_panel"
        ".RaidLinkService"
    )
    def test_create_new_raid_link(
        self, mock_svc, mock_auth, auth_client,
    ):
        mock_auth.return_value = MagicMock()
        # No existing link — allows creation to proceed
        mock_svc.get_link_for_playlist.return_value = None
        mock_link = MagicMock()
        mock_link.to_dict.return_value = {
            "id": 1,
            "raid_playlist_id": "new_raid",
        }
        mock_svc.create_raid_playlist.return_value = (
            "new_raid", "My Playlist [Raids]"
        )
        mock_svc.create_link.return_value = mock_link

        resp = auth_client.post(
            "/playlist/p1/raid-link",
            json={
                "create_new": True,
                "target_playlist_name": "My Playlist",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.raid_panel"
        ".RaidSyncService"
    )
    def test_drip_now_success(
        self, mock_svc, mock_auth, auth_client,
    ):
        mock_auth.return_value = MagicMock()
        mock_svc.drip_now.return_value = {
            "tracks_added": 3,
            "tracks_total": 50,
            "status": "success",
        }

        resp = auth_client.post(
            "/playlist/p1/drip-now",
            json={},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["tracks_added"] == 3


def _seed_source(db_app, spotify_id, target_playlist_id, raid_count=5):
    """Create an upstream source owned by `spotify_id` on `target_playlist_id`."""
    from shuffify.models.db import UpstreamSource, db
    from shuffify.services.user_service import UserService

    with db_app.app_context():
        user = UserService.upsert_from_spotify(
            {
                "id": spotify_id,
                "display_name": spotify_id,
                "images": [],
            }
        ).user
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id=target_playlist_id,
            source_playlist_id=f"src-{target_playlist_id}",
            source_type="external",
            raid_count=raid_count,
        )
        db.session.add(source)
        db.session.commit()
        return source.id


def _source_raid_count(db_app, source_id):
    from shuffify.models.db import UpstreamSource, db

    with db_app.app_context():
        db.session.expire_all()
        return UpstreamSource.query.get(source_id).raid_count


def _source_exists(db_app, source_id):
    from shuffify.models.db import UpstreamSource, db

    with db_app.app_context():
        db.session.expire_all()
        return UpstreamSource.query.get(source_id) is not None


class TestRaidSourceCountPlaylistScope:
    """A source belongs to one target playlist (SR-038).

    The route took source_id from the body and the playlist from the path, but
    only ever scoped the lookup by owner, so the playlist in the URL was
    decorative: any of the caller's sources could be updated through any
    playlist URL, including a playlist the caller does not own. Ownership was
    always enforced, so this never reached another user's data -- but the
    request was still being applied outside the scope it named.
    """

    @patch("shuffify.routes.require_auth")
    def test_correct_playlist_still_updates(
        self, mock_auth, db_app, auth_client
    ):
        """The feature must keep working: right playlist, right source."""
        mock_auth.return_value = MagicMock()
        source_id = _seed_source(db_app, "user123", "playlist-A")

        resp = auth_client.put(
            "/playlist/playlist-A/raid-source-count",
            json={"source_id": source_id, "raid_count": 9},
        )

        assert resp.status_code == 200
        assert _source_raid_count(db_app, source_id) == 9

    @patch("shuffify.routes.require_auth")
    def test_other_playlist_cannot_update_the_source(
        self, mock_auth, db_app, auth_client
    ):
        """Playlist B's URL must not reach a source configured on playlist A."""
        mock_auth.return_value = MagicMock()
        source_id = _seed_source(db_app, "user123", "playlist-A")

        resp = auth_client.put(
            "/playlist/playlist-B/raid-source-count",
            json={"source_id": source_id, "raid_count": 99},
        )

        assert resp.status_code == 404
        assert _source_raid_count(db_app, source_id) == 5

    @patch("shuffify.routes.require_auth")
    def test_unowned_playlist_in_url_cannot_update_the_source(
        self, mock_auth, db_app, auth_client
    ):
        """The playlist in the path is not decorative."""
        mock_auth.return_value = MagicMock()
        source_id = _seed_source(db_app, "user123", "playlist-A")

        resp = auth_client.put(
            "/playlist/not-my-playlist/raid-source-count",
            json={"source_id": source_id, "raid_count": 77},
        )

        assert resp.status_code == 404
        assert _source_raid_count(db_app, source_id) == 5

    @patch("shuffify.routes.require_auth")
    def test_another_users_source_is_unreachable(
        self, mock_auth, db_app, auth_client
    ):
        """Owner scoping predates this fix; pin it so it cannot regress."""
        mock_auth.return_value = MagicMock()
        victim_source_id = _seed_source(db_app, "victim456", "playlist-A")

        resp = auth_client.put(
            "/playlist/playlist-A/raid-source-count",
            json={"source_id": victim_source_id, "raid_count": 99},
        )

        assert resp.status_code == 404
        assert _source_raid_count(db_app, victim_source_id) == 5


class TestRaidUnwatchPlaylistScope:
    """Same defect class as the raid-source-count scope, on unwatch.

    unwatch resolved the source by owner alone, so the playlist in the URL was
    decorative here too -- and this path DELETES. It also then updates the URL
    playlist's raid schedule, so a mismatched call removed a source from one
    playlist while editing another playlist's schedule.
    """

    @patch("shuffify.routes.require_auth")
    def test_correct_playlist_still_unwatches(
        self, mock_auth, db_app, auth_client
    ):
        """The feature must keep working."""
        mock_auth.return_value = MagicMock()
        source_id = _seed_source(db_app, "user123", "playlist-A")

        resp = auth_client.post(
            "/playlist/playlist-A/raid-unwatch",
            json={"source_id": source_id},
        )

        assert resp.status_code == 200
        assert _source_exists(db_app, source_id) is False

    @patch("shuffify.routes.require_auth")
    def test_other_playlist_cannot_delete_the_source(
        self, mock_auth, db_app, auth_client
    ):
        """Playlist B's URL must not delete a source configured on A."""
        mock_auth.return_value = MagicMock()
        source_id = _seed_source(db_app, "user123", "playlist-A")

        resp = auth_client.post(
            "/playlist/playlist-B/raid-unwatch",
            json={"source_id": source_id},
        )

        assert resp.status_code == 404
        assert _source_exists(db_app, source_id) is True

    @patch("shuffify.routes.require_auth")
    def test_another_users_source_cannot_be_deleted(
        self, mock_auth, db_app, auth_client
    ):
        """Owner scoping predates this fix; pin it so it cannot regress."""
        mock_auth.return_value = MagicMock()
        victim_source_id = _seed_source(db_app, "victim456", "playlist-A")

        resp = auth_client.post(
            "/playlist/playlist-A/raid-unwatch",
            json={"source_id": victim_source_id},
        )

        assert resp.status_code == 404
        assert _source_exists(db_app, victim_source_id) is True
