"""Playlist setup routes: auth, envelopes, conflicts, creation outcomes and the page's return link."""

from unittest.mock import patch

import pytest

from shuffify.enums import ActivityType
from shuffify.models.db import ActivityLog, PlaylistPair, RaidPlaylistLink, Schedule, UpstreamSource, User
from shuffify.routes import json_reply
from shuffify.services import playlist_setup_service as setup
from shuffify.services.playlist_lock import playlist_lock
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyForbiddenError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyTokenExpiredError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)
from tests.paths import api_path
from tests.playlist_setup_fixtures import (
    TARGET,
    SetupSpotify,
    add_link,
    add_pair,
    add_routine,
    add_source,
    assert_never_scheduled,
)
from tests.queued_operations import post_and_run

pytestmark = pytest.mark.usefixtures("no_scraping")

BASE = f"/playlist/{TARGET}/setup"
CONTEXT, IMPACT, SETTINGS = api_path(f"{BASE}/context"), api_path(f"{BASE}/impact"), api_path(f"{BASE}/settings")
CREATE, PLAYLISTS = api_path(f"{BASE}/spotify-playlists"), api_path(f"{BASE}/playlists")


@pytest.fixture
def api():
    return SetupSpotify()


@pytest.fixture
def user(auth_client):
    return User.query.filter_by(spotify_id="user123").one()


def as_user(api):
    return patch("shuffify.routes.require_auth", return_value=api)


def get_context(client, api):
    with as_user(api):
        response = client.get(CONTEXT)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["context"]


def post_impact(client, api, changes, revision=None, **extra):
    revision = revision or get_context(client, api)["configuration_revision"]
    with as_user(api):
        return client.post(IMPACT, json={"configuration_revision": revision, "changes": changes, **extra})


def put_settings(client, api, body):
    with as_user(api):
        return client.put(SETTINGS, json=body)


def reviewed_body(client, api, changes, **extra):
    response = post_impact(client, api, changes)
    assert response.status_code == 200, response.get_json()
    reviewed = response.get_json()
    body = {"configuration_revision": reviewed["configuration_revision"], "changes": changes,
            "acknowledgment": reviewed["acknowledgment"], **extra}
    return reviewed, body


# --- Auth, envelopes and caching --------------------------------------------------------


@pytest.mark.parametrize("method, path", [
    ("get", CONTEXT), ("post", IMPACT), ("put", SETTINGS), ("post", CREATE), ("get", PLAYLISTS),
])
def test_every_endpoint_refuses_a_signed_out_request_with_an_error_code(db_app, method, path):
    with db_app.test_client() as client:
        response = getattr(client, method)(path, json={})
    assert response.status_code == 401
    assert response.get_json() == {
        "success": False, "error_code": "authentication_required", "message": "Please log in first.",
        "category": "error",
    }


@pytest.mark.parametrize("path", [CONTEXT, PLAYLISTS])
def test_reads_are_never_cached_even_when_refused(auth_client, api, path):
    with as_user(api):
        assert auth_client.get(path).headers["Cache-Control"] == "no-store"
    with patch("shuffify.routes.require_auth", return_value=None):
        assert auth_client.get(path).headers["Cache-Control"] == "no-store"


def test_a_review_is_never_cached(auth_client, api):
    assert post_impact(auth_client, api, [{"op": "add_search_source", "search_query": "lofi"}]).headers[
        "Cache-Control"] == "no-store"
    with as_user(api):
        assert auth_client.post(IMPACT, json=[]).headers["Cache-Control"] == "no-store"


def test_the_context_answers_the_service_context(auth_client, api, user, scheduler_spies):
    add_source(user, "ext1", name="Indie Finds")
    with as_user(api):
        response = auth_client.get(CONTEXT)
    body = response.get_json()
    assert response.status_code == 200 and set(body) == {"success", "context", "return_target"}
    assert body["success"] is True
    assert body["return_target"] == "playlist"
    assert body["context"]["target"] == {"id": TARGET, "name": "Morning Mix"}
    assert body["context"]["sources"][0]["name"] == "Indie Finds"
    assert api.writes == [] and ActivityLog.query.count() == 0
    assert_never_scheduled(scheduler_spies)


@pytest.mark.parametrize("target, status, code, message", [
    ("missing", 404, "playlist_not_found", "That playlist doesn't exist or was deleted."),
    ("foreign", 403, "access_denied", "You can only set up your own or collaborative playlists."),
])
def test_a_target_the_user_cannot_edit_is_refused_everywhere(auth_client, api, target, status, code, message):
    expected = {"success": False, "error_code": code, "message": message, "category": "error"}
    revision = "a" * 64
    with as_user(api):
        responses = [
            auth_client.get(api_path(f"/playlist/{target}/setup/context")),
            auth_client.post(api_path(f"/playlist/{target}/setup/impact"), json={
                "configuration_revision": revision, "changes": [{"op": "unlink_collection"}],
            }),
            auth_client.put(api_path(f"/playlist/{target}/setup/settings"), json={
                "configuration_revision": revision, "changes": [{"op": "unlink_collection"}], "acknowledgment": {},
            }),
        ]
        created = post_and_run(auth_client, api, api_path(f"/playlist/{target}/setup/spotify-playlists"),
                               json={"purpose": "archive", "name": "A"})
    for response in responses:
        assert (response.status_code, response.get_json()) == (status, expected)
    assert created.status_code == 200 and created.accepted.status_code == 202
    assert {key: created.get_json()[key] for key in ("success", "error_code", "message")} == {
        key: expected[key] for key in ("success", "error_code", "message")
    }
    assert created.get_json()["http_status"] == status
    assert api.writes == []


def test_json_reply_passes_a_replys_headers_through(db_app):
    with db_app.test_request_context():
        response, status = json_reply(({"success": True}, 200))
        assert (status, response.get_json()) == (200, {"success": True})
        response, status, headers = json_reply(setup.refusal("rate_limited", 429, "Busy", {"Retry-After": "7"}))
        assert (status, headers, response.get_json()) == (429, {"Retry-After": "7"}, {
            "success": False, "error_code": "rate_limited", "message": "Busy",
        })


def test_spotify_failures_reading_the_target_are_refused_as_retryable(auth_client, api):
    api.read_failures[TARGET] = SpotifyRateLimitError("slow", retry_after=9)
    with as_user(api):
        response = auth_client.get(CONTEXT)
    assert response.status_code == 429 and response.headers["Retry-After"] == "9"
    assert response.get_json()["error_code"] == "rate_limited"
    api.read_failures[TARGET] = SpotifyAPIError("down")
    with as_user(api):
        response = auth_client.get(CONTEXT)
    assert (response.status_code, response.get_json()["error_code"]) == (502, "spotify_unavailable")
    api.read_failures[TARGET] = SpotifyTokenExpiredError("expired")
    with as_user(api):
        response = auth_client.get(CONTEXT)
    assert (response.status_code, response.get_json()["error_code"]) == (401, "authentication_required")


# --- Request bodies ---------------------------------------------------------------------


@pytest.mark.parametrize("path, method", [(IMPACT, "post"), (SETTINGS, "put"), (CREATE, "post")])
@pytest.mark.parametrize("send", [
    {"json": []}, {"json": "changes"}, {"json": None}, {"json": 3},
    {"data": "{not json", "content_type": "application/json"},
    {"data": "purpose=archive", "content_type": "application/x-www-form-urlencoded"},
])
def test_a_body_that_is_not_a_json_object_is_an_invalid_request(auth_client, api, path, method, send):
    with as_user(api):
        response = getattr(auth_client, method)(path, **send)
    assert response.status_code == 400
    assert response.get_json() == {
        "success": False, "error_code": "invalid_request", "message": "Request body must be a JSON object.",
        "category": "error",
    }
    assert api.writes == []


@pytest.mark.parametrize("path, method", [(IMPACT, "post"), (SETTINGS, "put"), (CREATE, "post")])
def test_an_empty_or_loose_object_is_a_validation_error(auth_client, api, path, method):
    with as_user(api):
        empty = getattr(auth_client, method)(path, json={})
        loose = getattr(auth_client, method)(path, json={"surprise": True})
    for response in (empty, loose):
        body = response.get_json()
        assert response.status_code == 400 and body["error_code"] == "invalid_request"
        assert body["message"].startswith("Validation error: ")
    assert "Extra inputs are not permitted" in loose.get_json()["message"]


def test_duplicate_subjects_are_an_invalid_request(auth_client, api):
    change = {"op": "add_search_source", "search_query": "lofi"}
    response = post_impact(auth_client, api, [change, {**change, "search_query": " lofi "}])
    assert response.status_code == 400 and "both change search:lofi" in response.get_json()["message"]


def test_field_errors_answer_invalid_changes(auth_client, api, user):
    add_source(user, "ext1")
    response = post_impact(auth_client, api, [
        {"op": "add_playlist_source", "playlist_id": "ext1"}, {"op": "link_collection", "playlist_id": "foreign"},
    ])
    assert response.status_code == 400
    assert response.get_json() == {
        "success": False, "error_code": "invalid_changes", "message": "Some changes need attention. Nothing was saved.",
        "category": "error",
        "errors": [
            {"index": 0, "field": "playlist_id", "message": "Already a source"},
            {"index": 1, "field": "playlist_id", "message": "You can only use your own or collaborative playlists"},
        ],
    }


def test_another_users_source_is_not_found(auth_client, api, user):
    from shuffify.services.user_service import UserService

    other = UserService.upsert_from_spotify({"id": "user456", "display_name": "Other", "images": []}).user
    theirs = add_source(other, "ext1")
    response = post_impact(auth_client, api, [{"op": "remove_source", "source_id": theirs.id}])
    assert response.status_code == 400
    assert response.get_json()["errors"] == [{"index": 0, "field": "source_id", "message": "Source not found"}]
    assert UpstreamSource.query.count() == 1


# --- Review and Save over HTTP --------------------------------------------------------------


def test_review_then_save_applies_the_reviewed_changes_and_nothing_else(auth_client, api, user, scheduler_spies):
    routine = add_routine(user, "raid", ["ext1"])
    source = add_source(user, "ext1", name="Indie Finds")
    changes = [{"op": "remove_source", "source_id": source.id}, {"op": "add_search_source", "search_query": "lofi"}]
    reviewed, body = reviewed_body(auth_client, api, changes)
    assert reviewed["success"] is True and [r["id"] for r in reviewed["affected_routines"]] == [routine.id]
    assert UpstreamSource.query.count() == 1
    response = put_settings(auth_client, api, body)
    reply = response.get_json()
    assert response.status_code == 200
    assert set(reply) == {"success", "saved", "configuration_revision", "affected_schedule_ids", "context"}
    assert (reply["success"], reply["saved"], reply["affected_schedule_ids"]) == (True, True, [routine.id])
    assert reply["context"]["state_digest"] == reviewed["expected_state_digest"]
    assert reply["context"]["configuration_revision"] == reply["configuration_revision"]
    assert get_context(auth_client, api)["state_digest"] == reviewed["expected_state_digest"]
    assert [s.search_query for s in UpstreamSource.query.all()] == ["lofi"]
    assert Schedule.query.one().source_playlist_ids == []
    assert ActivityLog.query.one().activity_type == ActivityType.PLAYLIST_SETUP_SAVE
    assert api.writes == []
    assert_never_scheduled(scheduler_spies)


def test_a_stale_save_answers_configuration_changed_with_its_changed_sections(auth_client, api, user):
    source = add_source(user, "ext1")
    context = get_context(auth_client, api)
    changes = [{"op": "set_source_count", "source_id": source.id, "raid_count": 2}]
    _, body = reviewed_body(auth_client, api, changes)
    add_link(user)
    add_pair(user)
    response = put_settings(auth_client, api, {**body, "section_digests": context["section_digests"]})
    reply = response.get_json()
    assert (response.status_code, reply["error_code"]) == (409, "configuration_changed")
    assert reply["changed_sections"] == ["collection", "archive"]
    assert reply["context"]["collection"]["playlist_id"] == "col1"
    assert reply["message"] == "These settings changed since your review. Review the changes again."
    assert UpstreamSource.query.one().raid_count == 5
    response = put_settings(auth_client, api, body)
    assert response.status_code == 409 and response.get_json()["changed_sections"] is None


def _earlier(token):
    return {**token, "issued_at": token["issued_at"] - 1}


@pytest.mark.parametrize("tamper, reason", [
    (lambda body: {**body, "acknowledgment": _earlier(body["acknowledgment"])}, "mismatch"),
    (lambda body: {**body, "acknowledgment": {**body["acknowledgment"], "signature": "f" * 64}}, "mismatch"),
    (lambda body: {**body, "changes": [{"op": "add_search_source", "search_query": "jazz"}]}, "mismatch"),
    (lambda body: {**body, "acknowledgment": {**body["acknowledgment"], "extra": 1}}, "malformed"),
    (lambda body: {**body, "acknowledgment": "signed"}, "malformed"),
    (lambda body: {**body, "acknowledgment": None}, "malformed"),
])
def test_a_tampered_or_mismatched_acknowledgment_is_refused(auth_client, api, tamper, reason):
    _, body = reviewed_body(auth_client, api, [{"op": "add_search_source", "search_query": "lofi"}])
    response = put_settings(auth_client, api, tamper(body))
    reply = response.get_json()
    assert (response.status_code, reply["error_code"], reply["reason"]) == (409, "acknowledgment_invalid", reason)
    assert UpstreamSource.query.count() == 0 and ActivityLog.query.count() == 0


def test_an_expired_acknowledgment_is_refused(auth_client, api):
    _, body = reviewed_body(auth_client, api, [{"op": "add_search_source", "search_query": "lofi"}])
    later = body["acknowledgment"]["issued_at"] + setup.ACK_MAX_AGE_S + 1
    with patch("shuffify.services.signed_digest.time.time", return_value=later):
        response = put_settings(auth_client, api, body)
    reply = response.get_json()
    assert (response.status_code, reply["reason"]) == (409, "expired")
    assert reply["message"] == "This review expired. Review the changes again."
    assert UpstreamSource.query.count() == 0


def test_a_save_while_the_target_is_locked_is_busy(auth_client, api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    monkeypatch.setattr(setup, "LOCK_WAIT_S", 0.01)
    _, body = reviewed_body(auth_client, api, [{"op": "add_search_source", "search_query": "lofi"}])
    held, release = Event(), Event()

    def another_owner():
        with playlist_lock(TARGET) as acquired:
            assert acquired
            held.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(another_owner)
        assert held.wait(5)
        try:
            response = put_settings(auth_client, api, body)
        finally:
            release.set()
        owner.result()
    assert response.status_code == 409
    assert response.get_json() == {
        "success": False, "error_code": "playlist_busy", "retryable": True, "category": "error",
        "message": "A routine or save is running on this playlist. Try again when it finishes.",
    }
    assert UpstreamSource.query.count() == 0


def test_a_failed_save_answers_save_failed(auth_client, api):
    _, body = reviewed_body(auth_client, api, [{"op": "add_search_source", "search_query": "lofi"}])
    with patch.object(setup, "safe_commit", side_effect=RuntimeError("lost")):
        response = put_settings(auth_client, api, body)
    assert (response.status_code, response.get_json()["error_code"]) == (500, "save_failed")
    assert UpstreamSource.query.count() == 0


@pytest.mark.parametrize("path, method, body", [
    (IMPACT, "post", {"configuration_revision": "a" * 64, "changes": [{"op": "unlink_collection"}]}),
    (SETTINGS, "put", {"configuration_revision": "a" * 64, "changes": [{"op": "unlink_collection"}],
                       "acknowledgment": {}}),
    (CREATE, "post", {"purpose": "archive", "name": "Old Mix"}),
])
@pytest.mark.parametrize("token_state", ["missing", "expired"])
def test_csrf_refuses_writes_and_reviews_before_anything_runs(
    csrf_client, csrf_headers, path, method, body, token_state
):
    with patch("shuffify.routes.require_auth") as auth, \
            patch.object(setup.PlaylistSetupService, "impact") as impact, \
            patch.object(setup.PlaylistSetupService, "save") as save, \
            patch.object(setup.PlaylistSetupService, "create_playlist") as create:
        response = getattr(csrf_client, method)(path, json=body, headers=csrf_headers(token_state))
    assert response.status_code == 400 and response.get_json()["success"] is False
    for spy in (auth, impact, save, create):
        spy.assert_not_called()


def test_a_valid_csrf_token_reaches_the_view(csrf_client, csrf_headers, db_app, api, sample_token):
    from shuffify.services.user_service import UserService

    user = UserService.upsert_from_spotify({"id": "user123", "display_name": "Test User", "images": []}).user
    with csrf_client.session_transaction() as session:
        session["spotify_token"] = sample_token
        session["user_data"] = {"id": "user123"}
    with as_user(api), patch("shuffify.routes.get_db_user", return_value=user):
        response = post_and_run(csrf_client, api,
            CREATE, json={"purpose": "archive", "name": "Old Mix"}, headers=csrf_headers("valid")
        )
    assert response.status_code == 200 and response.accepted.status_code == 202
    assert response.get_json()["playlist"]["name"] == "Old Mix"


# --- Spotify creation ---------------------------------------------------------------------


def create(client, api, purpose="collection", name="  New Finds  "):
    with as_user(api):
        return post_and_run(client, api, CREATE, json={"purpose": purpose, "name": name})


def creations(api):
    return [call for call in api.calls if call[0] == "create_user_playlist"]


@pytest.mark.parametrize("purpose", ["collection", "archive"])
def test_creation_makes_one_private_playlist_with_the_exact_name_and_never_links(
    auth_client, api, user, purpose, scheduler_spies
):
    response = create(auth_client, api, purpose)
    assert response.status_code == 200 and response.accepted.status_code == 202
    reply = response.get_json()
    assert {key: reply[key] for key in ("success", "outcome", "playlist", "privacy_confirmed")} == {
        "success": True, "outcome": "created", "playlist": {"id": "Created1", "name": "New Finds"},
        "privacy_confirmed": True,
    }
    assert api.writes == [
        ("create_user_playlist", "New Finds", False, setup.DESCRIPTIONS[purpose]),
    ]
    activity = ActivityLog.query.one()
    assert (activity.activity_type, activity.playlist_id) == (ActivityType.PLAYLIST_SETUP_CREATE, TARGET)
    assert activity.metadata_json == {"purpose": purpose, "playlist_id": "Created1", "privacy_confirmed": True,
                                      "operation_id": response.operation["id"]}
    assert RaidPlaylistLink.query.count() == PlaylistPair.query.count() == Schedule.query.count() == 0
    assert_never_scheduled(scheduler_spies)


def test_an_optional_activity_failure_retains_verified_creation(auth_client, api):
    with patch("shuffify.services.activity_log_service.ActivityLog", side_effect=RuntimeError("log down")):
        response = create(auth_client, api)
    assert response.status_code == 200 and response.accepted.status_code == 202
    assert response.operation["state"] == "succeeded"
    reply = response.get_json()
    assert {key: reply[key] for key in ("success", "outcome", "playlist", "privacy_confirmed")} == {
        "success": True, "outcome": "created", "playlist": {"id": "Created1", "name": "New Finds"},
        "privacy_confirmed": True,
    }


def test_a_privacy_failure_retains_created_identity_without_claiming_private(auth_client, api):
    original = api.create_user_playlist

    def public(name, **kwargs):
        return {**original(name, **kwargs), "public": True}

    api.create_user_playlist = public
    api.privacy_failure = SpotifyWriteUnverifiedError("lost")
    response = create(auth_client, api)
    assert response.status_code == 200 and response.accepted.status_code == 202
    assert response.operation["state"] == "needs_attention"
    reply = response.get_json()
    assert reply["outcome"] == "created" and reply["playlist"]["id"] == "Created1"
    assert reply["privacy_confirmed"] is False and reply["creation_unknown"] is False
    assert len(creations(api)) == 1
    assert RaidPlaylistLink.query.count() == PlaylistPair.query.count() == 0


@pytest.mark.parametrize("purpose, seed, code", [
    ("collection", lambda user: add_link(user), "already_linked"),
    ("archive", lambda user: add_pair(user), "already_paired"),
])
def test_an_existing_link_or_pair_is_reported_before_any_spotify_write(auth_client, api, user, purpose, seed, code):
    row = seed(user)
    response = create(auth_client, api, purpose)
    reply = response.get_json()
    assert (response.status_code, reply["error_code"]) == (409, code)
    expected_id = row.raid_playlist_id if purpose == "collection" else row.archive_playlist_id
    assert reply["playlist"]["id"] == expected_id
    assert creations(api) == [] and api.writes == []
    other = "archive" if purpose == "collection" else "collection"
    assert create(auth_client, api, other).status_code == 200


UNKNOWN = ("We could not confirm whether the playlist was created. "
           "Check your Spotify playlists before creating another.")


@pytest.mark.parametrize("failure, status, code, outcome", [
    (SpotifyWriteUnverifiedError("timed out"), 502, "creation_unknown", "unknown"),
    (SpotifyForbiddenError("API error 403: Insufficient client scope"), 422, "creation_refused", "refused"),
    (SpotifyWriteRejectedError("API error 400: Bad name"), 422, "creation_refused", "refused"),
    (SpotifyTokenExpiredError("expired"), 401, "authentication_required", "refused"),
    (SpotifyAPIError("Unexpected error: boom"), 502, "creation_unknown", "unknown"),
    (RuntimeError("boom"), 502, "creation_unknown", "unknown"),
])
def test_each_creation_failure_maps_to_its_outcome_and_is_never_retried(
    auth_client, api, failure, status, code, outcome
):
    api.create_failure = failure
    response = create(auth_client, api)
    reply = response.get_json()
    assert response.status_code == 200 and response.accepted.status_code == 202
    assert (reply["http_status"], reply["success"], reply["error_code"], reply["outcome"]) == (
        status, False, code, outcome)
    assert len(creations(api)) == 1 and ActivityLog.query.count() == 0
    if outcome == "unknown":
        assert reply["message"] == UNKNOWN
    if outcome == "refused":
        assert str(failure) not in reply["message"]
        assert "reason" not in reply


@pytest.mark.parametrize("failure", [
    SpotifyRateLimitError("slow", retry_after=12),
    SpotifyQuotaExceededError("quota", retry_after=3600),
])
def test_creation_defers_the_original_operation_until_the_retry_deadline(auth_client, api, failure):
    from datetime import datetime, timezone

    from shuffify.services.operations.repository import OperationRepository

    api.create_failure = failure
    before = datetime.now(timezone.utc)
    response = create(auth_client, api)
    assert response.status_code == 200 and response.accepted.status_code == 202
    assert response.operation["state"] == "running" and response.get_json() is None
    repository = OperationRepository()
    stored = repository.get(response.operation["id"])
    document = stored.journal
    deferred = next(item for plan in document["plans"] for item in plan["batches"]
                    if item.get("next_attempt_at"))
    assert datetime.fromisoformat(deferred["next_attempt_at"]) > before
    assert stored.lease_owner is None and stored.lease_expires_at > before
    assert len(creations(api)) == 1 and ActivityLog.query.count() == 0


@pytest.mark.parametrize("answer", [{}, {"id": ""}, {"id": 5}, [], None, "Created"])
def test_a_creation_answer_without_an_id_is_unknown(auth_client, api, answer):
    def answered(name, public=False, description=""):
        api.calls.append(("create_user_playlist", name, public, description))
        return answer

    api.create_user_playlist = answered
    response = create(auth_client, api)
    reply = response.get_json()
    assert response.status_code == 200 and response.accepted.status_code == 202
    assert (reply["http_status"], reply["error_code"], reply["outcome"], reply["message"]) == (
        502, "creation_unknown", "unknown", UNKNOWN)
    assert ("update_playlist_details",) not in [call[:1] for call in api.calls]


def test_creation_is_limited_to_ten_a_minute(auth_client, api):
    statuses = [create(auth_client, api, name=f"Mix {index}").status_code for index in range(11)]
    assert statuses == [200] * 10 + [429]
    assert len(creations(api)) == 10


# --- Owned playlists ------------------------------------------------------------------------


def test_owned_playlists_include_hidden_ones_and_refresh_skips_the_cache(auth_client, api, user):
    from shuffify.models.db import PlaylistPreference

    with as_user(api):
        response = auth_client.get(PLAYLISTS)
    reply = response.get_json()
    assert response.status_code == 200 and reply["success"] is True
    ids = [playlist["id"] for playlist in reply["playlists"]]
    assert "foreign" not in ids and {"target", "collab", "col1"} <= set(ids)
    collab = next(playlist for playlist in reply["playlists"] if playlist["id"] == "collab")
    assert collab == {"id": "collab", "name": "Shared Collab", "track_count": 3,
                      "image_url": "https://img.invalid/collab.jpg", "owned": False}
    assert next(p for p in reply["playlists"] if p["id"] == "col1")["owned"] is True
    assert PlaylistPreference.query.count() == 0
    with as_user(api):
        auth_client.get(f"{PLAYLISTS}?refresh=1")
    assert [call for call in api.calls if call[0] == "get_user_playlists"] == [
        ("get_user_playlists", False), ("get_user_playlists", True),
    ]
    api.list_failure = SpotifyAPIError("down")
    with as_user(api):
        response = auth_client.get(PLAYLISTS)
    assert (response.status_code, response.get_json()["error_code"]) == (502, "spotify_unavailable")


# --- The page -----------------------------------------------------------------------------------
