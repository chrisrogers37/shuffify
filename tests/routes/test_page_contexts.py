"""Read endpoints expose complete page contexts without losing private session state."""

from unittest.mock import create_autospec, patch

import pytest

from shuffify.models.db import ActivityLog, PlaylistPreference, Schedule, User, db
from shuffify.models.playlist import Playlist
from shuffify.services import PlaylistAccessError, PlaylistNotFoundError
from shuffify.services.playlist_service import PlaylistService
from shuffify.spotify.api import SpotifyAPI


@pytest.fixture
def context_api(auth_client):
    api = create_autospec(SpotifyAPI, instance=True)
    with patch("shuffify.routes.require_auth", autospec=True, return_value=api):
        yield api


def raw_playlist(playlist_id, *, name=None, count=3):
    return {
        "id": playlist_id, "name": name or playlist_id, "owner": {"id": "user123"},
        "images": [{"url": "https://example.com/art.png"}], "items": {"total": count}, "public": False,
        "external_urls": {"spotify": f"https://open.spotify.com/playlist/{playlist_id}"},
    }


def test_session_is_public_and_consumes_only_authored_notices(client):
    with client.session_transaction() as session:
        session["auth_notice"] = "access_denied"
    response = client.get("/api/session")
    assert response.status_code == 200
    assert response.json["signed_in"] is False
    assert "csrf_token" not in response.json
    assert response.json["notice"]["code"] == "access_denied"
    assert client.get("/api/session").json["notice"] is None
    with client.session_transaction() as session:
        session["auth_notice"] = "<script>untrusted</script>"
    assert client.get("/api/session").json["notice"] is None


def test_signed_in_session_projects_profile_and_issues_csrf(auth_client):
    with auth_client.session_transaction() as session:
        session["user_data"].update(email="private@example.invalid", images=[{"url": "https://example.com/user.png"}])
    response = auth_client.get("/api/session")
    assert response.status_code == 200
    assert response.json["signed_in"] is True
    assert response.json["user"] == {"id": "user123", "display_name": "Test User",
                                     "image_url": "https://example.com/user.png"}
    assert response.json["csrf_token"]
    assert "private@example.invalid" not in response.get_data(as_text=True)


def test_oauth_callback_exposes_safe_notice_to_signed_out_session(client):
    client.get("/callback?error=access_denied&error_description=untrusted-description")
    response = client.get("/api/session")
    assert response.json["notice"]["code"] == "access_denied"
    assert "untrusted-description" not in response.get_data(as_text=True)
    assert client.get("/api/session").json["notice"] is None


def test_settings_read_and_update_return_the_same_complete_context(auth_client, context_api):
    before = auth_client.get("/api/settings").json
    assert before["algorithms"]
    response = auth_client.post("/api/settings", json={"extra": {"example": "value"},
                                                      "default_algorithm_params": {"keep_first": 2}})
    assert response.status_code == 200
    assert set(response.json) == set(before)
    assert response.json["settings"]["extra"] == {"example": "value"}
    assert response.json["settings"]["default_algorithm_params"] == {"keep_first": 2}


def test_library_orders_sections_projects_rows_and_preserves_undo(auth_client, context_api, db_app):
    context_api.get_user_playlists.return_value = [raw_playlist("regular"), raw_playlist("favorite"),
                                                  raw_playlist("hidden")]
    with db_app.app_context():
        user = User.query.filter_by(spotify_id="user123").one()
        db.session.add_all([
            PlaylistPreference(user_id=user.id, spotify_playlist_id="favorite", is_pinned=True, sort_order=0),
            PlaylistPreference(user_id=user.id, spotify_playlist_id="hidden", is_hidden=True, sort_order=1),
        ])
        db.session.commit()
    history = {"regular": {"states": [["spotify:track:a"], ["spotify:track:b"]], "current_index": 1}}
    with auth_client.session_transaction() as session:
        session["playlist_states"] = history
    response = auth_client.get("/api/library?refresh=1")
    assert response.status_code == 200
    sections = response.json["sections"]
    assert [p["id"] for p in sections["favorites"]] == ["favorite"]
    assert [p["id"] for p in sections["playlists"]] == ["regular"]
    assert [p["id"] for p in sections["hidden"]] == ["hidden"]
    assert sections["playlists"][0]["can_undo"] is True
    assert sections["playlists"][0]["track_count"] == 3
    context_api.get_user_playlists.assert_called_with(skip_cache=True)
    with auth_client.session_transaction() as session:
        assert session["playlist_states"] == history
    summary = auth_client.get("/api/user-playlists").json["playlists"][0]
    assert set(summary) == {"id", "name", "image_url", "track_count", "spotify_url"}
    assert summary["spotify_url"] == sections["favorites"][0]["spotify_url"]


def test_activity_preserves_metadata_and_limits_each_feed(auth_client, context_api, db_app):
    with db_app.app_context():
        user = User.query.filter_by(spotify_id="user123").one()
        db.session.add(ActivityLog(user_id=user.id, activity_type="shuffle", description="Example action",
                                  metadata_json={"triggered_by": "manual", "counts": {"tracks": 4}}))
        db.session.commit()
    response = auth_client.get("/api/activity")
    assert response.status_code == 200
    assert response.json["activities"][0]["metadata"]["counts"] == {"tracks": 4}
    assert response.json["activities"][0]["triggered_by"] == "manual"
    assert len(response.json["activities"]) <= 100
    assert len(response.json["executions"]) <= 20


def test_routines_include_machine_state_and_playlist_refs(auth_client, context_api, db_app):
    context_api.get_user_playlists.return_value = [raw_playlist("target")]
    with db_app.app_context():
        user = User.query.filter_by(spotify_id="user123").one()
        db.session.add(Schedule(user_id=user.id, job_type="shuffle", target_playlist_id="target",
                                target_playlist_name="Example", algorithm_name="BasicShuffle",
                                schedule_type="interval", schedule_value="daily", is_enabled=False))
        db.session.commit()
    response = auth_client.get("/api/routines")
    assert response.status_code == 200
    assert response.json["routines"][0]["state"] in {"active", "paused", "needs_attention"}
    assert response.json["routines"][0]["state_label"]
    assert response.json["playlists"] == [{"id": "target", "name": "target"}]


@pytest.mark.parametrize("failure", [PlaylistNotFoundError("Missing"), PlaylistAccessError("Inaccessible")])
def test_unavailable_workshop_returns_404_without_clearing_session(auth_client, context_api, failure):
    with patch("shuffify.routes.contexts.PlaylistService", autospec=PlaylistService) as playlists:
        playlists.return_value.validate_user_can_edit.side_effect = failure
        response = auth_client.get("/api/workshop/missing")
    assert response.status_code == 404
    assert response.json["success"] is False
    with auth_client.session_transaction() as session:
        assert session["spotify_token"]["access_token"] == "test_token"


def test_workshop_context_contains_tracks_navigation_and_history(auth_client, context_api):
    with auth_client.session_transaction() as session:
        session["external_playlist_history"] = [{"id": "source", "name": "Source", "owner_id": "example",
                                                 "track_count": 5}]
    with patch("shuffify.routes.contexts.PlaylistService", autospec=PlaylistService) as playlists:
        playlists.return_value.get_playlist.return_value = Playlist(id="target", name="Target", owner_id="user123")
        playlists.return_value.get_user_playlists.return_value = [raw_playlist("before"), raw_playlist("target"),
                                                                 raw_playlist("after")]
        response = auth_client.get("/api/workshop/target")
    assert response.status_code == 200
    assert "tracks" not in response.json["playlist"]
    assert response.json["tracks"] == []
    assert response.json["previous"] == {"id": "before", "name": "before"}
    assert response.json["next"] == {"id": "after", "name": "after"}
    assert response.json["recently_loaded"][0]["id"] == "source"


@pytest.mark.parametrize("query", ["schedule_id=0", "schedule_id=-1", "schedule_id=１２", "schedule_id=" + "1" * 19,
                                    "from=https://example.com", "origin=https://example.com"])
def test_routine_context_rejects_invalid_navigation_before_spotify(auth_client, context_api, query):
    response = auth_client.get("/api/playlist/target/routines/context?" + query)
    assert response.status_code == 404
    context_api.get_playlist_summary.assert_not_called()


def test_setup_context_rejects_url_return_targets_before_spotify(auth_client, context_api):
    response = auth_client.get("/api/playlist/target/setup/context?return=https://example.com")
    assert response.status_code == 404
    context_api.get_playlist_summary.assert_not_called()


def test_refresh_write_route_is_absent(client):
    assert client.post("/api/refresh-playlists").status_code == 404


def test_settings_preserve_extensible_parameters_and_preferences(auth_client, context_api):
    params = {"retired_option": {"tracks": [1, None, {"enabled": True}]}, "keep_first": 2}
    extra = {"example": ["value", {"nested": False}]}
    response = auth_client.post("/api/settings", json={"default_algorithm_params": params, "extra": extra})
    assert response.status_code == 200
    assert response.json["settings"]["default_algorithm_params"] == params
    assert response.json["settings"]["extra"] == extra
    response = auth_client.get("/api/settings")
    assert response.json["settings"]["default_algorithm_params"] == params
    assert response.json["settings"]["extra"] == extra
