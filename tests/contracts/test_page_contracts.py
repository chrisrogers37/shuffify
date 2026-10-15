"""Actual Flask contexts and shared examples obey schemas generated from TypeScript."""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from shuffify.models.db import ActivityLog, JobExecution, PlaylistPreference, Schedule, User, db
from shuffify.models.playlist import Playlist
from shuffify.services import PlaylistNotFoundError, PlaylistService
from tests.paths import api_path

WEB = Path(__file__).resolve().parents[2] / "web"
CASES = json.loads((WEB / "fixtures" / "contract-cases.json").read_text())


def validator(name):
    schema = json.loads((WEB / "schemas" / f"{name}.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def check_response(response, schema, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.get_json()
    problems = list(validator(schema).iter_errors(payload))
    assert not problems, "\n".join(f"{list(error.absolute_path)}: {error.message}" for error in problems)
    return payload


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_typescript_and_python_share_valid_and_invalid_examples(case):
    payload = (
        json.loads((WEB / "fixtures" / case["fixture"]).read_text())
        if "fixture" in case else case["data"]
    )
    assert validator(case["schema"]).is_valid(payload) is case["valid"]


@pytest.fixture
def page_reads(monkeypatch):
    playlists = [
        {"id": key, "name": name, "images": [], "tracks": {"total": 2},
         "owner": {"id": "user123"}, "public": False,
         "external_urls": {"spotify": f"https://open.spotify.com/playlist/{key}"}}
        for key, name in [("first", "Morning Mix"), ("second", "Evening Mix"), ("hidden", "Hidden Mix")]
    ]
    tracks = [{
        "id": "example-track", "name": "Example Song", "uri": "spotify:track:example-track",
        "duration_ms": 123000, "is_local": False, "added_at": None,
        "artists": ["Example Artist"], "artist_urls": [None], "album_name": "Example Album",
        "album_image_url": None, "track_url": "https://open.spotify.com/track/example-track",
    }] * 2
    calls = []

    def list_playlists(_service, skip_cache=False):
        calls.append(skip_cache)
        return copy.deepcopy(playlists)

    def load_playlist(_service, playlist_id, include_features=False):
        if playlist_id not in {row["id"] for row in playlists}:
            raise PlaylistNotFoundError("Playlist not found.")
        return Playlist(
            playlist_id, "Morning Mix", "user123", total_tracks=2,
            public=False, tracks=copy.deepcopy(tracks),
        )

    def editable_playlist(_service, playlist_id, user_spotify_id, *, skip_cache=False, lean=False):
        for playlist in playlists:
            if playlist["id"] == playlist_id and playlist["owner"]["id"] == user_spotify_id:
                return copy.deepcopy(playlist)
        raise PlaylistNotFoundError("Playlist not found.")

    monkeypatch.setattr("shuffify.routes.require_auth", lambda: object())
    monkeypatch.setattr(PlaylistService, "get_user_playlists", list_playlists)
    monkeypatch.setattr(PlaylistService, "get_playlist", load_playlist)
    monkeypatch.setattr(PlaylistService, "validate_user_can_edit", editable_playlist)
    return calls


def test_signed_out_session_never_requires_auth_and_omits_csrf(client):
    payload = check_response(client.get(api_path("/session")), "session-context")
    assert payload["signed_in"] is False
    assert "csrf_token" not in payload


def test_signed_in_session_projects_only_public_profile_fields(auth_client):
    payload = check_response(auth_client.get(api_path("/session")), "session-context")
    assert payload["signed_in"] is True
    assert set(payload["user"]) == {"id", "display_name", "image_url"}
    assert payload["csrf_token"]


def test_signed_out_callback_notice_is_consumed_once(client):
    response = client.get("/callback?error=access_denied&error_description=untrusted-query-text")
    assert response.status_code == 302
    first = check_response(client.get(api_path("/session")), "session-context")
    assert first["notice"]["code"] == "access_denied"
    assert "untrusted-query-text" not in first["notice"]["message"]
    second = check_response(client.get(api_path("/session")), "session-context")
    assert second["notice"] is None


@pytest.mark.parametrize("path,schema", [
    ("/settings", "settings-context"), ("/activity", "activity-context"),
    ("/library", "library-context"), ("/routines", "routines-context"),
    ("/workshop/first", "workshop-context"), ("/user-playlists", "user-playlists-context"),
])
def test_page_contexts_match_generated_contracts(auth_client, page_reads, path, schema):
    check_response(auth_client.get(api_path(path)), schema)


def test_settings_save_returns_the_same_context_as_read(auth_client, page_reads):
    payload = check_response(auth_client.post(api_path("/settings"), json={
        "max_snapshots_per_playlist": 17,
    }), "settings-context")
    assert payload["settings"]["max_snapshots_per_playlist"] == 17
    assert payload["algorithms"]
    assert payload["algorithm_options"]
    check_response(auth_client.post(api_path("/settings"), json={
        "max_snapshots_per_playlist": 51,
    }), "api-error", 400)


def test_library_sections_keep_preference_order_and_undo(auth_client, db_app, page_reads):
    with db_app.app_context():
        owner = User.query.filter_by(spotify_id="user123").one()
        db.session.add_all([
            PlaylistPreference(user_id=owner.id, spotify_playlist_id="second", is_pinned=True, sort_order=0),
            PlaylistPreference(user_id=owner.id, spotify_playlist_id="hidden", is_hidden=True),
        ])
        db.session.commit()
    with auth_client.session_transaction() as session:
        session["playlist_states"] = {"second": {"states": [["a", "b"], ["b", "a"]], "current_index": 1}}
    payload = check_response(auth_client.get(api_path("/library?refresh=1")), "library-context")
    sections = payload["sections"]
    assert [row["id"] for row in sections["favorites"]] == ["second"]
    assert [row["id"] for row in sections["playlists"]] == ["first"]
    assert [row["id"] for row in sections["hidden"]] == ["hidden"]
    assert sections["favorites"][0]["can_undo"] is True
    assert page_reads == [True]
    picker = check_response(auth_client.get(api_path("/user-playlists")), "user-playlists-context")
    assert [row["id"] for row in picker["playlists"]] == ["second", "first"]
    for row in picker["playlists"]:
        library_row = next(item for group in sections.values() for item in group if item["id"] == row["id"])
        assert row == {key: library_row[key] for key in row}


def test_workshop_keeps_duplicate_occurrences_and_returns_missing_as_404(auth_client, page_reads):
    payload = check_response(auth_client.get(api_path("/workshop/first")), "workshop-context")
    assert len(payload["tracks"]) == 2
    assert payload["tracks"][0]["uri"] == payload["tracks"][1]["uri"]
    check_response(auth_client.get(api_path("/workshop/missing")), "api-error", 404)
    with auth_client.session_transaction() as session:
        assert session["user_data"]["id"] == "user123"


def test_populated_activity_and_routines_preserve_full_contract(auth_client, db_app, page_reads):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with db_app.app_context():
        owner = User.query.filter_by(spotify_id="user123").one()
        schedule = Schedule(
            user_id=owner.id, job_type="shuffle", target_playlist_id="first", target_playlist_name="Morning Mix",
            algorithm_name="BasicShuffle", schedule_type="interval", schedule_value="daily", is_enabled=False,
        )
        db.session.add(schedule)
        db.session.flush()
        for index in range(105):
            db.session.add(ActivityLog(
                user_id=owner.id, activity_type="shuffle", description="Shuffled Morning Mix",
                playlist_id="first", playlist_name="Morning Mix", created_at=now + timedelta(seconds=index),
                metadata_json={"triggered_by": "manual", "algorithm": "BasicShuffle"},
            ))
        for index in range(25):
            db.session.add(JobExecution(
                schedule_id=schedule.id, status="success", started_at=now + timedelta(seconds=index),
                result_json={"version": 1, "operation": "shuffle", "execution_id": index + 1, "outcome": "complete",
                             "steps": [{"step": "shuffle", "status": "complete", "counts": {"tracks_reordered": 2},
                                        "resources": [{"resource": "first", "status": "complete", "kind": "target",
                                                       "mutation_type": "track_order", "dispatched": True}],
                                        "bookkeeping": []}]},
            ))
        db.session.commit()
    activity = check_response(auth_client.get(api_path("/activity")), "activity-context")
    assert len(activity["activities"]) == 100
    assert len(activity["executions"]) == 20
    assert activity["activities"][0]["triggered_by"] == "manual"
    assert activity["activities"][0]["metadata"]["algorithm"] == "BasicShuffle"
    assert activity["executions"][0]["result_detail"]["steps"][0]["counts"] == {"tracks_reordered": 2}
    check_response(auth_client.get(api_path("/routines")), "routines-context")
    check_response(auth_client.get(api_path("/library")), "library-context")


def test_stored_routine_parameters_remain_lossless_in_read_contexts(auth_client, db_app, page_reads):
    params = {"keep_first": "2", "retired_option": {"mode": "custom"}}
    with db_app.app_context():
        owner = User.query.filter_by(spotify_id="user123").one()
        db.session.add(Schedule(
            user_id=owner.id, job_type="shuffle", target_playlist_id="first", target_playlist_name="Morning Mix",
            algorithm_name="BasicShuffle", algorithm_params=params,
            schedule_type="interval", schedule_value="daily", is_enabled=True,
        ))
        db.session.commit()
    routines = check_response(auth_client.get(api_path("/routines")), "routines-context")
    assert routines["routines"][0]["schedule"]["algorithm_params"] == params
    assert routines["routines"][0]["definition"]["algorithm_params"] == params
    library = check_response(auth_client.get(api_path("/library")), "library-context")
    assert library["dashboard"]["active_schedules"][0]["algorithm_params"] == params
