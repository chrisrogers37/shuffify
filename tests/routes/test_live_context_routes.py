"""Library review context, reviewed mutation bindings and the pure observer over HTTP."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import InternalServerError

from shuffify.models.db import ActivityLog, JobExecution
from shuffify.services.live_review_bindings import scope_fingerprint
from shuffify.spotify.exceptions import SpotifyError
from tests.paths import api_path
from tests.queued_operations import post_and_run
from tests.queued_operations import scoped_history as history
from tests.routes.live_review_helpers import binding_bundle, post_context, seed_history
from tests.services.test_live_executor_outcomes import MutableSpotify, uri
from tests.services.test_live_metadata_service import MetadataSpotify

TARGET = "playlist123"
CONTEXT = api_path(f"/playlist/{TARGET}/live-context")
INSPECT = api_path(f"/playlist/{TARGET}/inspect-live")
DIGEST = "a" * 64
SHUFFLE_FORM = {"algorithm": "BalancedShuffle", "keep_first": "0", "section_count": "4"}


class CountingSpotify(MutableSpotify):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads = 0

    def get_playlist(self, playlist_id, **kwargs):
        self.reads += 1
        return super().get_playlist(playlist_id, **kwargs)


@pytest.fixture
def api():
    return CountingSpotify(SimpleNamespace(spotify_id="user123"), playlist123=[uri(1), uri(2), uri(2)])


@pytest.fixture
def metadata():
    return MetadataSpotify(False)


@pytest.fixture(autouse=True)
def snapshots():
    with patch("shuffify.services.live_playlist_service.PlaylistSnapshotService.auto_snapshot_if_enabled") as snap:
        yield snap


def as_user(api):
    return patch("shuffify.routes.require_auth", return_value=api)


# --- Review context -------------------------------------------------------


def test_each_review_kind_answers_the_frozen_union_and_is_never_cached(auth_client, api, metadata):
    seed_history(auth_client, TARGET, [[uri(2), uri(1), uri(2)], [uri(1), uri(2), uri(2)]])
    with as_user(api):
        shuffle = post_context(auth_client, TARGET, {"kind": "shuffle", "form": SHUFFLE_FORM})
        undo = post_context(auth_client, TARGET, {"kind": "undo"})
    with as_user(metadata):
        privacy = post_context(auth_client, TARGET, {"kind": "privacy", "public": True})
    for response, kind in ((shuffle, "shuffle"), (undo, "undo"), (privacy, "privacy")):
        assert response.status_code == 200
        body = response.get_json()
        assert body.keys() == {"success", "context"} and body["success"] is True
        assert body["context"]["version"] == 1
        assert body["context"]["kind"] == kind
        assert body["context"]["target"]["id"] == TARGET
    assert api.writes == metadata.writes == []
    assert ActivityLog.query.count() == JobExecution.query.count() == 0


@pytest.mark.parametrize("send", [
    {"data": "kind=undo", "content_type": "application/x-www-form-urlencoded"},
    {"data": "undo", "content_type": "text/plain"},
    {"json": ["undo"]},
    {"json": {"kind": "rotate"}},
])
def test_review_body_must_be_one_exact_json_object(auth_client, api, send):
    with as_user(api):
        response = auth_client.post(CONTEXT, **send)
    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "no-store"
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"], operation["retryable"]) == (
        "live_context", "invalid_request", False
    )
    assert api.reads == 0


@pytest.mark.parametrize("status", [401, 503])
def test_review_preflight_failures_change_nothing_and_may_load_again(auth_client, api, status):
    with (
        patch("shuffify.routes.require_auth", return_value=None if status == 401 else api),
        patch("shuffify.is_db_available", return_value=status != 503),
    ):
        response = post_context(auth_client, TARGET, {"kind": "undo"})
    assert response.status_code == status
    body = response.get_json()
    assert body["success"] is False
    assert body["operation"]["kind"] == "live_context"
    assert body["operation"]["aggregate_status"] == "partial"
    assert body["operation"]["retryable"] is True
    assert not {"execution_id", "started_at", "execution_state", "http_status"} & body.keys()
    assert api.reads == 0


def test_undo_review_leaves_session_history_untouched(auth_client, api):
    states = [[uri(9)], [uri(2), uri(1), uri(2)], [uri(1), uri(2), uri(2)]]
    seed_history(auth_client, TARGET, states)
    with as_user(api):
        response = post_context(auth_client, TARGET, {"kind": "undo"})
    assert response.get_json()["context"]["action"]["desired_track_uris"] == states[1]
    assert history(auth_client, TARGET) == {"states": states, "current_index": 2}


# --- Reviewed Shuffle -------------------------------------------------------


def test_reviewed_shuffle_runs_the_reviewed_options_once(auth_client, api):
    with as_user(api):
        scope = post_context(auth_client, TARGET, {"kind": "shuffle", "form": SHUFFLE_FORM}).get_json()[
            "context"]["review_binding"]["scope"]
        with patch(
            "shuffify.services.live_playlist_service.ShuffleService.execute",
            return_value=[uri(2), uri(2), uri(1)],
        ) as execute:
            response = post_and_run(auth_client, api, api_path(f"/shuffle/{TARGET}"), data={
                **SHUFFLE_FORM, "csrf_token": "copied form field", "reviewed_scope": scope,
            })
    assert response.status_code == 200
    assert response.get_json()["operation"]["aggregate_status"] == "complete"
    execute.assert_called_once()
    assert execute.call_args.args[2] == {"keep_first": 0, "section_count": 4}
    assert api.writes == [("update", TARGET, [uri(2), uri(2), uri(1)])]


def test_options_changed_after_review_dispatch_nothing(auth_client, api, snapshots):
    with as_user(api):
        scope = post_context(auth_client, TARGET, {"kind": "shuffle", "form": SHUFFLE_FORM}).get_json()[
            "context"]["review_binding"]["scope"]
        with patch("shuffify.services.live_playlist_service.ShuffleService.execute") as execute:
            response = post_and_run(auth_client, api, api_path(f"/shuffle/{TARGET}"), data={
                **SHUFFLE_FORM, "section_count": "5", "reviewed_scope": scope,
            })
    assert response.status_code == 200
    assert response.operation["state"] == "failed"
    body = response.get_json()
    assert body["success"] is False
    assert (body["operation"]["error_code"], body["operation"]["retryable"]) == ("review_changed", False)
    execute.assert_not_called()
    snapshots.assert_not_called()
    assert api.writes == []
    assert history(auth_client, TARGET) is None


@pytest.mark.parametrize("path,kind,data", [
    ("shuffle", "shuffle", {"reviewed_scope": "A" * 64}),
    ("shuffle", "shuffle", {"reviewed_scope": "a" * 63}),
    ("shuffle", "shuffle", {"reviewed_scope": "a" * 65}),
    ("shuffle", "shuffle", {"reviewed_scope": ""}),
    ("shuffle", "shuffle", MultiDict([("reviewed_scope", DIGEST), ("reviewed_scope", DIGEST)])),
    ("shuffle", "shuffle", {"reviewed_scope": DIGEST, "reviewed_history": DIGEST}),
    ("shuffle", "shuffle", {"reviewed_before_order": DIGEST}),
    ("undo", "undo", {"reviewed_scope": DIGEST}),
    ("undo", "undo", {"reviewed_scope": DIGEST, "reviewed_history": DIGEST}),
    ("undo", "undo", {"reviewed_scope": DIGEST, "reviewed_history": DIGEST, "reviewed_before_order": "x"}),
    ("undo", "undo", MultiDict([
        ("reviewed_scope", DIGEST), ("reviewed_history", DIGEST), ("reviewed_history", DIGEST),
        ("reviewed_before_order", DIGEST),
    ])),
])
def test_malformed_bindings_never_fall_back_or_read_spotify(auth_client, api, path, kind, data):
    # MultiDict keeps duplicate keys; a plain dict copy would collapse them.
    data = MultiDict(list(MultiDict(data).items(multi=True)) + (
        [("algorithm", "BasicShuffle")] if path == "shuffle" else []
    ))
    seed_history(auth_client, TARGET, [[uri(2)], [uri(1)]])
    with as_user(api):
        response = post_and_run(auth_client, api, api_path(f"/{path}/{TARGET}"), data=data)
    assert response.status_code == 400
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"], operation["retryable"]) == (
        kind, "invalid_review_binding", False
    )
    assert api.reads == 0
    assert api.writes == []
    assert history(auth_client, TARGET)["current_index"] == 1


# --- Reviewed Undo ----------------------------------------------------------


def test_reviewed_undo_restores_the_reviewed_version_exactly_once(auth_client, api):
    states = [[uri(3)], [uri(2), uri(1), uri(2)], [uri(1), uri(2), uri(2)]]
    seed_history(auth_client, TARGET, states)
    with as_user(api):
        context = post_context(auth_client, TARGET, {"kind": "undo"}).get_json()["context"]
        bundle = binding_bundle(context)
        # A client-supplied order is not an Undo input; the server selects from its history.
        response = post_and_run(auth_client, api, api_path(f"/undo/{TARGET}"), data={**bundle, "track_uris": uri(7)})
        assert response.status_code == 200
        assert history(auth_client, TARGET)["current_index"] == 1
        again = post_and_run(auth_client, api, api_path(f"/undo/{TARGET}"), data=bundle)
    assert api.writes == [("update", TARGET, context["action"]["desired_track_uris"])]
    assert context["action"]["desired_track_uris"] == states[1]
    assert again.status_code == 200
    assert again.operation["state"] == "failed"
    assert again.get_json()["operation"]["error_code"] == "history_changed"
    assert history(auth_client, TARGET)["current_index"] == 1


@pytest.mark.parametrize("change,code", [
    ("history", "history_changed"),
    ("target", "resource_changed"),
    ("scope", "review_changed"),
])
def test_stale_undo_review_never_writes_or_moves_history(auth_client, api, change, code):
    states = [[uri(2), uri(1), uri(2)], [uri(1), uri(2), uri(2)]]
    seed_history(auth_client, TARGET, states)
    with as_user(api):
        bundle = binding_bundle(post_context(auth_client, TARGET, {"kind": "undo"}).get_json()["context"])
        if change == "history":
            seed_history(auth_client, TARGET, [[uri(5)]] + states)
        elif change == "target":
            api.playlists[TARGET] = [uri(1), uri(2), uri(2), uri(4)]  # Added elsewhere after review.
            api.revisions[TARGET] += 1
        else:
            bundle["reviewed_scope"] = scope_fingerprint("undo", "another-playlist", {})
        expected = history(auth_client, TARGET)
        response = post_and_run(auth_client, api, api_path(f"/undo/{TARGET}"), data=bundle)
    assert response.status_code == 200
    assert response.operation["state"] == "failed"
    assert response.get_json()["operation"]["error_code"] == code
    assert response.get_json()["operation"]["retryable"] is False
    assert api.writes == []
    assert history(auth_client, TARGET) == expected


def test_reviewed_undo_after_history_expiry_is_its_specific_400(auth_client, api):
    seed_history(auth_client, TARGET, [[uri(2), uri(1), uri(2)], [uri(1), uri(2), uri(2)]])
    with as_user(api):
        bundle = binding_bundle(post_context(auth_client, TARGET, {"kind": "undo"}).get_json()["context"])
        with auth_client.session_transaction() as session:
            session.pop("playlist_states")
        response = post_and_run(auth_client, api, api_path(f"/undo/{TARGET}"), data=bundle)
    assert response.status_code == 400
    assert response.get_json()["operation"]["error_code"] == "history_unavailable"
    assert api.writes == []


# --- Reviewed privacy -------------------------------------------------------


def test_reviewed_privacy_runs_and_a_different_boolean_never_writes(auth_client, metadata):
    with as_user(metadata):
        scope = post_context(auth_client, TARGET, {"kind": "privacy", "public": True}).get_json()[
            "context"]["review_binding"]["scope"]
        changed = post_and_run(auth_client, metadata,
            api_path(f"/playlist/{TARGET}/toggle-visibility"), json={"public": False, "reviewed_scope": scope}
        )
        assert changed.status_code == 200
        assert changed.operation["state"] == "failed"
        assert changed.get_json()["operation"]["error_code"] == "review_changed"
        assert metadata.writes == []
        ran = post_and_run(auth_client, metadata,
            api_path(f"/playlist/{TARGET}/toggle-visibility"), json={"public": True, "reviewed_scope": scope},
        )
    assert ran.status_code == 200
    assert ran.get_json()["public"] is True
    assert metadata.writes == [(TARGET, True)]


@pytest.mark.parametrize("body", [
    {"public": True, "reviewed_scope": 7},
    {"public": True, "reviewed_scope": None},
    {"public": True, "reviewed_scope": "A" * 64},
    {"public": True, "reviewed_scope": DIGEST, "reviewed_history": DIGEST},
    {"public": True, "reviewed_before_order": DIGEST},
])
def test_privacy_binding_shape_is_checked_before_any_read(auth_client, metadata, body):
    with as_user(metadata):
        response = post_and_run(auth_client, metadata, api_path(f"/playlist/{TARGET}/toggle-visibility"), json=body)
    assert response.status_code == 400
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"]) == ("privacy", "invalid_review_binding")
    assert metadata.reads == metadata.writes == []


@pytest.mark.parametrize("kind", ["shuffle", "undo", "privacy"])
def test_a_copied_matching_hash_never_authorizes(auth_client, api, metadata, kind):
    seed_history(auth_client, TARGET, [[uri(2), uri(1), uri(2)], [uri(1), uri(2), uri(2)]])
    spotify = metadata if kind == "privacy" else api
    with as_user(spotify):
        if kind == "shuffle":
            body = {"kind": "shuffle", "form": SHUFFLE_FORM}
        else:
            body = {"kind": kind, "public": True} if kind == "privacy" else {"kind": kind}
        context = post_context(auth_client, TARGET, body).get_json()["context"]
        spotify.owner = "someone-else"
        if kind == "shuffle":
            response = post_and_run(auth_client, spotify, api_path(f"/shuffle/{TARGET}"), data={
                **SHUFFLE_FORM, "reviewed_scope": context["review_binding"]["scope"],
            })
        elif kind == "undo":
            response = post_and_run(auth_client, spotify, api_path(f"/undo/{TARGET}"), data=binding_bundle(context))
        else:
            response = post_and_run(auth_client, spotify, api_path(f"/playlist/{TARGET}/toggle-visibility"), json={
                "public": True, "reviewed_scope": context["review_binding"]["scope"],
            })
    assert response.status_code == 200
    assert response.operation["state"] == "failed"
    assert spotify.writes == []


# --- Pure observer ----------------------------------------------------------


def inspect(client, body):
    with (
        patch("shuffify.services.live_playlist_service.playlist_lock") as lock,
        patch("shuffify.services.live_playlist_service.ShuffleService.execute") as execute,
    ):
        response = client.post(INSPECT, json=body)
    lock.assert_not_called()
    execute.assert_not_called()
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["inspection_only"] is True
    return response


def test_track_check_reports_a_match_as_an_observation_only(auth_client, api, snapshots):
    seed_history(auth_client, TARGET, [[uri(9)], [uri(1), uri(2), uri(2)]])
    before = history(auth_client, TARGET)
    with as_user(api):
        response = inspect(auth_client, {"kind": "tracks", "desired_track_uris": [uri(1), uri(2), uri(2)]})
    assert response.status_code == 200
    body = response.get_json()
    assert (body["success"], body["status"]) == (True, "success")
    operation = body["operation"]
    assert (operation["kind"], operation["aggregate_status"], operation["retryable"]) == (
        "track_inspection", "complete", False
    )
    assert operation["bookkeeping"] == []
    entry = operation["resources"][0]
    assert (entry["status"], entry["dispatched"], entry["affected_count"], entry["verification"]) == (
        "not_required", False, 0, "ordered"
    )
    assert entry["current_track_uris"] == entry["desired_track_uris"] == [uri(1), uri(2), uri(2)]
    assert "baseline_track_uris" not in entry
    assert not {"execution_id", "started_at", "execution_state"} & body.keys()
    assert api.writes == []
    snapshots.assert_not_called()
    assert history(auth_client, TARGET) == before
    assert ActivityLog.query.count() == JobExecution.query.count() == 0


@pytest.mark.parametrize("desired,code", [
    ([uri(2), uri(1), uri(2)], "intent_not_observed"),
    ([uri(1), uri(2)], "intent_not_observed"),
    ([], "intent_not_observed"),
    (None, "intent_unavailable"),
])
def test_track_check_without_a_match_stays_unknown(auth_client, api, desired, code):
    body = {"kind": "tracks"} if desired is None else {"kind": "tracks", "desired_track_uris": desired}
    with as_user(api):
        response = inspect(auth_client, body)
    assert response.status_code == 502
    operation = response.get_json()["operation"]
    assert (operation["aggregate_status"], operation["error_code"], operation["retryable"]) == (
        "unknown", code, False
    )
    entry = operation["resources"][0]
    assert entry["current_track_uris"] == [uri(1), uri(2), uri(2)]
    assert ("desired_track_uris" in entry) is (desired is not None)
    assert api.writes == []


@pytest.mark.parametrize("desired,status,code", [
    (False, 200, None),
    (True, 502, "verification_failed"),
    (None, 502, "intent_unavailable"),
])
def test_privacy_check_observes_without_writing(auth_client, metadata, desired, status, code):
    body = {"kind": "privacy"} if desired is None else {"kind": "privacy", "desired_public": desired}
    with as_user(metadata):
        response = inspect(auth_client, body)
    assert response.status_code == status
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"], operation["retryable"]) == ("privacy", code, False)
    assert metadata.writes == []


@pytest.mark.parametrize("body", [
    None, [], {}, {"kind": "playlist"}, {"kind": ["tracks"]},
    {"kind": "tracks", "desired_track_uris": None},
    {"kind": "tracks", "desired_track_uris": uri(1)},
    {"kind": "tracks", "desired_track_uris": ["spotify:track:short"]},
    {"kind": "tracks", "desired_track_uris": [uri(1)], "desired_public": True},
    {"kind": "tracks", "desired_public": True},
    {"kind": "tracks", "execution_id": 4, "operation": {"aggregate_status": "complete"}},
    {"kind": "privacy", "desired_public": None},
    {"kind": "privacy", "desired_public": "false"},
    {"kind": "privacy", "desired_track_uris": []},
    {"kind": "privacy", "desired_public": True, "resource_name": "claimed"},
])
def test_check_requests_must_be_one_exact_variant(auth_client, api, body):
    with as_user(api):
        response = inspect(auth_client, body)
    assert response.status_code == 400
    response_body = response.get_json()
    operation = response_body["operation"]
    expected_kind = "privacy" if isinstance(body, dict) and body.get("kind") == "privacy" else "track_inspection"
    assert (operation["kind"], operation["aggregate_status"], operation["error_code"], operation["retryable"]) == (
        expected_kind, "unknown", "invalid_request", False
    )
    assert operation["resources"] == []
    assert "claimed" not in response.text
    assert api.reads == 0


@pytest.mark.parametrize("body,kind", [({"kind": "tracks"}, "track_inspection"), ({"kind": "privacy"}, "privacy")])
def test_check_preflight_failure_is_unknown_never_no_effect(auth_client, api, body, kind):
    with patch("shuffify.routes.require_auth", return_value=None):
        response = inspect(auth_client, body)
    assert response.status_code == 401
    response_body = response.get_json()
    operation = response_body["operation"]
    assert (operation["kind"], operation["aggregate_status"], operation["retryable"]) == (kind, "unknown", False)
    assert "This check did not run" in response_body["message"]
    assert api.reads == 0


@pytest.mark.parametrize("path,body", [(CONTEXT, {"kind": "undo"}), (INSPECT, {"kind": "tracks"})])
@pytest.mark.parametrize("failure,status", [(InternalServerError(), 500), (SpotifyError("unavailable"), 500)])
def test_failures_outside_the_route_body_are_never_cached(auth_client, path, body, failure, status):
    with patch("shuffify.routes.require_auth", side_effect=failure):
        response = auth_client.post(path, json=body)
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"


def test_api_authentication_errors_are_not_cached(auth_client):
    with auth_client.session_transaction() as session:
        session.pop("spotify_token", None)
    response = auth_client.post(api_path(f"/shuffle/{TARGET}"), data={"algorithm": "BasicShuffle"})
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path,send", [
    ("shuffle", {"query_string": {"reviewed_scope": DIGEST}, "data": {"algorithm": "BasicShuffle"}}),
    ("shuffle", {"json": {"algorithm": "BasicShuffle", "reviewed_scope": DIGEST}}),
    ("undo", {"json": {"reviewed_scope": DIGEST, "reviewed_history": DIGEST, "reviewed_before_order": DIGEST}}),
    ("undo", {"query_string": {"reviewed_history": DIGEST}}),
])
def test_bindings_outside_the_form_are_malformed_never_legacy(auth_client, api, path, send):
    seed_history(auth_client, TARGET, [[uri(2)], [uri(1)]])
    with as_user(api):
        response = post_and_run(auth_client, api, api_path(f"/{path}/{TARGET}"), **send)
    assert response.status_code == 400
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"]) == (path, "invalid_review_binding")
    assert api.reads == 0
    assert api.writes == []
    assert history(auth_client, TARGET)["current_index"] == 1


def test_privacy_binding_in_the_query_is_malformed_never_legacy(auth_client, metadata):
    with as_user(metadata):
        response = post_and_run(auth_client, metadata,
            api_path(f"/playlist/{TARGET}/toggle-visibility"),
            json={"public": True}, query_string={"reviewed_scope": DIGEST},
        )
    assert response.status_code == 400
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"]) == ("privacy", "invalid_review_binding")
    assert metadata.reads == metadata.writes == []


@pytest.mark.parametrize("body,kind", [({"kind": "undo"}, "undo"), ({"kind": "other"}, "live_context")])
def test_unexpected_review_failure_reports_the_validated_kind(auth_client, api, body, kind):
    with as_user(api), patch(
        "shuffify.routes.playlists.LiveActionContextService.resolve", side_effect=RuntimeError("unavailable"),
    ):
        response = post_context(auth_client, TARGET, body)
    assert response.status_code == 502
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"], operation["aggregate_status"]) == (
        kind, "preflight_failed", "partial"
    )
    assert api.writes == []
