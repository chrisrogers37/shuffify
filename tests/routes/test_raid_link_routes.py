"""
Tests for raid link and drip routes.

Tests cover authentication, validation, and basic success paths
for raid playlist link CRUD and drip endpoints.
"""

from unittest.mock import MagicMock, patch

from shuffify.models.db import db
from tests.paths import api_path
from tests.playlist_setup_fixtures import SetupSpotify
from tests.queued_operations import post_and_run
from tests.routes.live_review_helpers import serving
from tests.routes.test_live_raid_outcomes import MutableSpotify, uri

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
                api_path("/playlist/p1/raid-link"),
                json={"create_new": True},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_update_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.put(
                api_path("/playlist/p1/raid-link"),
                json={"drip_count": 5},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_delete_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete(
                api_path("/playlist/p1/raid-link")
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_drip_now_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/drip-now"),
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
                api_path("/playlist/p1/drip-schedule-toggle")
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_source_count_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.put(
                api_path("/playlist/p1/raid-source-count"),
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
            api_path("/playlist/p1/raid-link"),
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
            api_path("/playlist/p1/raid-link"),
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_update_drip_count_too_low(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.put(
            api_path("/playlist/p1/raid-link"),
            json={"drip_count": 0},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_source_count_too_low(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.put(
            api_path("/playlist/p1/raid-source-count"),
            json={"source_id": 1, "raid_count": 0},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_create_existing_without_id(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/raid-link"),
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
    def test_create_new_raid_link(
        self, mock_auth, auth_client,
    ):
        from shuffify.enums import ActivityType
        from shuffify.models.db import ActivityLog, RaidPlaylistLink

        api = SetupSpotify(playlists={"p1": {"name": "My Playlist"}})
        mock_auth.return_value = api
        resp = post_and_run(auth_client, api,
            api_path("/playlist/p1/raid-link"),
            json={
                "create_new": True,
                "target_playlist_name": "My Playlist",
            },
        )
        assert resp.status_code == 200 and resp.accepted.status_code == 202
        assert resp.operation["state"] == "succeeded"
        data = resp.get_json()
        assert data["success"] is True
        link = RaidPlaylistLink.query.one()
        assert link.raid_playlist_id == "Created1"
        assert link.raid_playlist_name == "My Playlist [Raids]"
        assert api.playlists["Created1"]["public"] is False
        assert len(api.writes) == 1
        assert ActivityLog.query.one().activity_type == ActivityType.RAID_LINK_CREATE

    def test_drip_now_success(self, raid_target):
        api = MutableSpotify()
        api.playlists["target"] = [uri(number) for number in range(1, 48)]
        api.playlists["collection"] = [uri(number) for number in range(48, 51)]
        raid_target.link.drip_count = 3
        db.session.commit()
        with serving(api):
            resp = post_and_run(raid_target.client, api, api_path("/playlist/target/drip-now"), json={})
        assert resp.status_code == 200, resp.get_json()
        assert resp.accepted.status_code == 202
        assert resp.operation["state"] == "succeeded"
        data = resp.get_json()
        assert data["tracks_added"] == 3
        assert data["tracks_total"] == 50
        assert len(api.playlists["target"]) == 50
        assert api.playlists["collection"] == []


class TestRaidLinkCreationUnknown:
    """A lost answer from Spotify leaves the raid playlist's existence unknown."""

    @patch("shuffify.routes.require_auth")
    def test_unverified_creation_is_reported_unknown_and_links_nothing(
        self, mock_auth, auth_client
    ):
        from shuffify.models.db import RaidPlaylistLink
        from shuffify.services.playlist_setup_service import CREATION_UNKNOWN
        from shuffify.spotify.exceptions import SpotifyWriteUnverifiedError

        api = SetupSpotify(playlists={"p1": {"name": "Mix"}})
        api.create_failure = SpotifyWriteUnverifiedError("timed out")
        mock_auth.return_value = api

        resp = post_and_run(auth_client, api,
            api_path("/playlist/p1/raid-link"),
            json={"create_new": True, "target_playlist_name": "Mix"},
        )

        assert resp.status_code == 200 and resp.accepted.status_code == 202
        assert resp.operation["state"] == "needs_attention"
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "creation_unknown"
        assert data["outcome"] == "unknown"
        assert data["message"] == CREATION_UNKNOWN
        assert len([call for call in api.calls if call[0] == "create_user_playlist"]) == 1
        assert RaidPlaylistLink.query.count() == 0


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
    """A source belongs to one target playlist.

    The route takes source_id from the body and the playlist from the path; a
    request that names another playlist gets 404 and changes nothing.
    """

    @patch("shuffify.routes.require_auth")
    def test_correct_playlist_still_updates(
        self, mock_auth, db_app, auth_client
    ):
        """The source's own playlist can update it."""
        mock_auth.return_value = MagicMock()
        source_id = _seed_source(db_app, "user123", "playlist-A")

        resp = auth_client.put(
            api_path("/playlist/playlist-A/raid-source-count"),
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
            api_path("/playlist/playlist-B/raid-source-count"),
            json={"source_id": source_id, "raid_count": 99},
        )

        assert resp.status_code == 404
        assert _source_raid_count(db_app, source_id) == 5

    @patch("shuffify.routes.require_auth")
    def test_unowned_playlist_in_url_cannot_update_the_source(
        self, mock_auth, db_app, auth_client
    ):
        """The playlist in the path scopes the request."""
        mock_auth.return_value = MagicMock()
        source_id = _seed_source(db_app, "user123", "playlist-A")

        resp = auth_client.put(
            api_path("/playlist/not-my-playlist/raid-source-count"),
            json={"source_id": source_id, "raid_count": 77},
        )

        assert resp.status_code == 404
        assert _source_raid_count(db_app, source_id) == 5

    @patch("shuffify.routes.require_auth")
    def test_another_users_source_is_unreachable(
        self, mock_auth, db_app, auth_client
    ):
        """Another user's source cannot be reached."""
        mock_auth.return_value = MagicMock()
        victim_source_id = _seed_source(db_app, "victim456", "playlist-A")

        resp = auth_client.put(
            api_path("/playlist/playlist-A/raid-source-count"),
            json={"source_id": victim_source_id, "raid_count": 99},
        )

        assert resp.status_code == 404
        assert _source_raid_count(db_app, victim_source_id) == 5


class TestRaidUnwatchPlaylistScope:
    """Unwatch is scoped to the playlist in the URL.

    This path deletes the source and then updates the URL playlist's raid
    schedule, so a request that names another playlist gets 404 and changes
    nothing.
    """

    @patch("shuffify.routes.require_auth")
    def test_correct_playlist_still_unwatches(
        self, mock_auth, db_app, auth_client
    ):
        """The source's own playlist can unwatch it."""
        mock_auth.return_value = MagicMock()
        source_id = _seed_source(db_app, "user123", "playlist-A")

        resp = auth_client.post(
            api_path("/playlist/playlist-A/raid-unwatch"),
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
            api_path("/playlist/playlist-B/raid-unwatch"),
            json={"source_id": source_id},
        )

        assert resp.status_code == 404
        assert _source_exists(db_app, source_id) is True

    @patch("shuffify.routes.require_auth")
    def test_another_users_source_cannot_be_deleted(
        self, mock_auth, db_app, auth_client
    ):
        """Another user's source cannot be deleted."""
        mock_auth.return_value = MagicMock()
        victim_source_id = _seed_source(db_app, "victim456", "playlist-A")

        resp = auth_client.post(
            api_path("/playlist/playlist-A/raid-unwatch"),
            json={"source_id": victim_source_id},
        )

        assert resp.status_code == 404
        assert _source_exists(db_app, victim_source_id) is True
