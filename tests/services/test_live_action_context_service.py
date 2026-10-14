"""Library review context: fresh read-only facts and canonical comparison bindings."""

import hashlib
import json
from copy import deepcopy
from unittest.mock import patch

import pytest
from flask.sessions import SecureCookieSession

from shuffify.models.db import ActivityLog, JobExecution
from shuffify.services.live_action_context_service import (
    ContextRequestError,
    LiveActionContextService,
    parse_context_request,
    summarize,
)
from shuffify.services.live_review_bindings import (
    before_order_fingerprint,
    fingerprint,
    history_fingerprint,
    scope_fingerprint,
    shuffle_scope,
)
from shuffify.services.state_service import StateService
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
    SpotifyTokenExpiredError,
)
from tests.node_runtime import run_scenario
from tests.services.test_live_executor_outcomes import MutableSpotify, uri
from tests.services.test_live_metadata_service import MISSING, OWNER, MetadataSpotify
from tests.services.test_live_playlist_service import seed_history
from tests.test_workshop_save_runtime import CLIENT as WORKSHOP_CLIENT
from tests.test_workshop_save_runtime import ROOT
from tests.test_workshop_save_runtime import node as node


@pytest.fixture(autouse=True)
def nothing_mutates(db_app):
    """No review may shuffle, snapshot, lock or record anything."""
    with (
        patch("shuffify.services.live_playlist_service.ShuffleService.execute") as execute,
        patch("shuffify.services.playlist_snapshot_service.PlaylistSnapshotService.auto_snapshot_if_enabled") as snap,
        patch("shuffify.services.live_playlist_service.playlist_lock") as lock,
    ):
        yield
        execute.assert_not_called()
        snap.assert_not_called()
        lock.assert_not_called()
        assert ActivityLog.query.count() == JobExecution.query.count() == 0


def resolve(api, body, session=None, playlist_id="target"):
    return LiveActionContextService(api).resolve(playlist_id, OWNER, session or SecureCookieSession(), body)


def seeded(states, index=None):
    session = SecureCookieSession()
    seed_history(session, states, index)
    return session


def shuffle_body(**form):
    return {"kind": "shuffle", "form": {"algorithm": "BalancedShuffle", **form}}


def test_fingerprint_is_canonical_compact_sorted_ascii_json_sha256():
    value = {"b": [1, "é"], "a": {"z": True, "y": None}}
    expected = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    assert fingerprint(value) == fingerprint({"a": {"y": None, "z": True}, "b": [1, "é"]}) == expected
    assert len(expected) == 64 and expected == expected.lower()
    with pytest.raises(ValueError):
        fingerprint({"value": float("nan")})


def test_each_binding_purpose_is_a_separate_domain():
    selection = StateService.peek_undo(seeded([[uri(1)], [uri(2)]]), "target")
    assert len({
        scope_fingerprint("undo", "target", {}),
        before_order_fingerprint("target", []),
        history_fingerprint("target", selection),
    }) == 3
    assert history_fingerprint("target", selection) == fingerprint({
        "purpose": "shuffify.live.undo-selection.v1", "target_id": "target",
        "states": [[uri(1)], [uri(2)]], "current_index": 1,
    })


def test_scope_binds_normalized_semantics_only():
    def scope(form, target="target"):
        _, (algorithm, params) = parse_context_request({"kind": "shuffle", "form": form})
        return shuffle_scope(target, algorithm, params)

    base = scope({"algorithm": "BalancedShuffle", "keep_first": "2", "section_count": "4"})
    # Equivalent normalized values and parameters the method ignores share one scope.
    assert base == scope({"algorithm": " BalancedShuffle ", "keep_first": " 2", "section_count": "4",
                          "shuffle_percentage": "10", "jitter": "3"})
    assert base == scope({"algorithm": "BalancedShuffle", "keep_first": "2"} | {"section_count": "04"})
    assert base != scope({"algorithm": "BalancedShuffle", "keep_first": "2", "section_count": "5"})
    assert base != scope({"algorithm": "StratifiedShuffle", "keep_first": "2", "section_count": "4"})
    assert base != scope({"algorithm": "BalancedShuffle", "keep_first": "2", "section_count": "4"}, "other")
    assert scope_fingerprint("privacy", "target", {"public": True}) != scope_fingerprint(
        "privacy", "target", {"public": False}
    )


@pytest.mark.parametrize("body,kind,code", [
    (None, "live_context", "invalid_request"),
    ([], "live_context", "invalid_request"),
    ({}, "live_context", "invalid_request"),
    ({"kind": "rotate"}, "live_context", "invalid_request"),
    ({"kind": ["undo"]}, "live_context", "invalid_request"),
    ({"kind": "undo", "public": True}, "undo", "invalid_request"),
    ({"kind": "privacy"}, "privacy", "invalid_request"),
    ({"kind": "privacy", "public": None}, "privacy", "invalid_request"),
    ({"kind": "privacy", "public": "true"}, "privacy", "invalid_request"),
    ({"kind": "privacy", "public": 1}, "privacy", "invalid_request"),
    ({"kind": "privacy", "public": True, "form": {}}, "privacy", "invalid_request"),
    ({"kind": "shuffle"}, "shuffle", "invalid_request"),
    ({"kind": "shuffle", "form": None}, "shuffle", "invalid_request"),
    ({"kind": "shuffle", "form": [["algorithm", "BasicShuffle"]]}, "shuffle", "invalid_request"),
    ({"kind": "shuffle", "form": {"keep_first": "1"}}, "shuffle", "invalid_request"),
    ({"kind": "shuffle", "form": {"algorithm": "BasicShuffle", "keep_first": 1}}, "shuffle", "invalid_request"),
    ({"kind": "shuffle", "form": {"algorithm": "BasicShuffle", "keep_first": None}}, "shuffle", "invalid_request"),
    ({"kind": "shuffle", "form": {"algorithm": "BasicShuffle", "keep_first": ["1"]}}, "shuffle", "invalid_request"),
    ({"kind": "shuffle", "form": {"algorithm": "BasicShuffle", "csrf_token": "t"}}, "shuffle", "invalid_request"),
    ({"kind": "shuffle", "form": {"algorithm": "BasicShuffle", "locked_positions": "{}"}}, "shuffle",
     "invalid_request"),
    ({"kind": "shuffle", "form": {"algorithm": "BasicShuffle", "keep_first": "-1"}}, "shuffle", "invalid_request"),
    ({"kind": "shuffle", "form": {"algorithm": "PercentageShuffle", "shuffle_percentage": "nan"}}, "shuffle",
     "invalid_request"),
    ({"kind": "shuffle", "form": {"algorithm": "NotReal"}}, "shuffle", "invalid_algorithm"),
    ({"kind": "shuffle", "form": {"algorithm": ""}}, "shuffle", "invalid_algorithm"),
])
def test_only_exact_request_variants_are_accepted(body, kind, code):
    with pytest.raises(ContextRequestError) as raised:
        parse_context_request(body)
    assert (raised.value.kind, raised.value.code) == (kind, code)
    api = MutableSpotify(OWNER, target=[uri(1)])
    payload, status = resolve(api, body)
    assert status == 400
    assert payload["success"] is False
    assert payload["operation"] == {
        "kind": kind, "aggregate_status": "partial", "resources": [], "bookkeeping": [],
        "error_code": code, "retryable": False,
    }
    assert not {"execution_id", "started_at", "execution_state", "http_status", "context"} & payload.keys()
    assert api.writes == []


def test_shuffle_context_reads_fresh_complete_tracks_and_changes_nothing():
    tracks = [uri(i) for i in range(101)] + [uri(3)]
    api = MutableSpotify(OWNER, target=tracks)
    session = seeded([[uri(9)], [uri(8)]])
    before = deepcopy(dict(session))
    payload, status = resolve(api, shuffle_body(keep_first="1", section_count="3", jitter="9"), session)
    assert status == 200
    assert payload == {"success": True, "context": {
        "version": 1,
        "kind": "shuffle",
        "target": {"id": "target", "name": "Fresh target"},
        "label": "Shuffle now",
        "resources": [{
            "resource": "target", "resource_name": "Fresh target", "role": "target", "domain": "spotify",
            "access": "write", "mutation_type": "track_order", "field": None,
        }],
        "required_bookkeeping": ["session_history"],
        "review_binding": {
            "version": 1,
            "scope": shuffle_scope("target", "BalancedShuffle", {"keep_first": 1, "section_count": 3}),
            "before_order": None,
            "history": None,
        },
        "current": {"track_count": 102, "loaded_at": payload["context"]["current"]["loaded_at"]},
        "action": {
            "algorithm": {"id": "BalancedShuffle", "name": ShuffleRegistry.get_algorithm("BalancedShuffle")().name},
            "params": {"keep_first": 1, "section_count": 3},
        },
    }}
    assert "track_uris" not in payload["context"]["current"]
    assert api.writes == []
    assert dict(session) == before and session.modified is False


def test_shuffle_context_rejects_an_empty_playlist():
    payload, status = resolve(MutableSpotify(OWNER, target=[]), shuffle_body())
    assert status == 400
    assert payload["operation"]["kind"] == "shuffle"
    assert payload["operation"]["error_code"] == "empty_playlist"
    assert payload["operation"]["retryable"] is False


def test_undo_context_reviews_the_actual_session_selection_with_duplicates():
    states = [[uri(1), uri(2), uri(2), uri(3)], [uri(3), uri(2), uri(1), uri(2)], [uri(2), uri(1)]]
    current = [uri(2), uri(1), uri(4)]  # changed outside this session since the last save
    api = MutableSpotify(OWNER, target=current)
    session = seeded(states)
    before = deepcopy(dict(session))
    payload, status = resolve(api, {"kind": "undo"}, session)
    assert status == 200
    context = payload["context"]
    assert context["label"] == "Restore previous saved version now"
    assert context["resources"][0]["mutation_type"] == "track_membership"
    assert context["required_bookkeeping"] == ["session_history"]
    assert context["current"]["track_uris"] == current
    assert context["current"]["track_count"] == 3
    assert context["action"] == {
        "current_index": 2,
        "selected_index": 1,
        "desired_track_uris": states[1],
        "summary": {"added_count": 2, "removed_count": 1, "order_changed": False},
    }
    selection = StateService.peek_undo(session, "target")
    assert context["review_binding"] == {
        "version": 1,
        "scope": scope_fingerprint("undo", "target", {}),
        "before_order": before_order_fingerprint("target", current),
        "history": history_fingerprint("target", selection),
    }
    assert api.writes == []
    assert dict(session) == before and session.modified is False


def test_undo_context_is_track_order_when_occurrences_agree():
    api = MutableSpotify(OWNER, target=[uri(1), uri(1), uri(2)])
    payload, _ = resolve(api, {"kind": "undo"}, seeded([[uri(1), uri(2), uri(1)], [uri(1), uri(1), uri(2)]]))
    assert payload["context"]["resources"][0]["mutation_type"] == "track_order"
    assert payload["context"]["action"]["summary"] == {"added_count": 0, "removed_count": 0, "order_changed": True}


@pytest.mark.parametrize("states,index,code", [
    (None, None, "history_unavailable"),
    ([[uri(1)]], 0, "already_at_original"),
    ([[uri(1)], "corrupt"], 1, "history_unavailable"),
    ([[uri(1)], [uri(2)]], 7, "history_unavailable"),
    ([["spotify:track:short"], [uri(2)]], 1, "invalid_track_order"),
])
def test_undo_context_without_a_usable_selection_is_a_safe_400(states, index, code):
    api = MutableSpotify(OWNER, target=[uri(2)])
    session = SecureCookieSession() if states is None else seeded(states, index)
    before = deepcopy(dict(session))
    payload, status = resolve(api, {"kind": "undo"}, session)
    assert status == 400
    assert payload["operation"]["kind"] == "undo"
    assert payload["operation"]["error_code"] == code
    assert payload["operation"]["retryable"] is False
    assert dict(session) == before and session.modified is False
    assert api.writes == []


@pytest.mark.parametrize("current,requested", [(False, True), (True, False), (True, True)])
def test_privacy_context_reads_fresh_metadata_only(current, requested):
    api = MetadataSpotify(current)  # Track methods are absent: any track call would fail.
    payload, status = resolve(api, {"kind": "privacy", "public": requested}, playlist_id="p1")
    assert status == 200
    state = "public" if requested else "private"
    assert payload["context"] == {
        "version": 1,
        "kind": "privacy",
        "target": {"id": "p1", "name": "Fresh playlist name"},
        "label": f"Make playlist {state} now",
        "resources": [{
            "resource": "p1", "resource_name": "Fresh playlist name", "role": "target", "domain": "spotify",
            "access": "write", "mutation_type": "metadata", "field": "public",
        }],
        "required_bookkeeping": [],
        "review_binding": {
            "version": 1,
            "scope": scope_fingerprint("privacy", "p1", {"public": requested}),
            "before_order": None,
            "history": None,
        },
        "current": {"public": current, "loaded_at": payload["context"]["current"]["loaded_at"]},
        "action": {"public": requested},
    }
    assert api.reads == [("p1", True)]
    assert api.writes == []


@pytest.mark.parametrize("current", [MISSING, None, "true", 1])
def test_privacy_context_requires_a_strict_current_boolean(current):
    api = MetadataSpotify(current)
    payload, status = resolve(api, {"kind": "privacy", "public": True}, playlist_id="p1")
    assert status == 502
    assert payload["operation"]["error_code"] == "metadata_unavailable"
    assert payload["operation"]["retryable"] is True
    assert api.writes == []


@pytest.mark.parametrize("body", [shuffle_body(), {"kind": "undo"}, {"kind": "privacy", "public": True}])
@pytest.mark.parametrize("failure,status,retryable", [
    ("not_editable", 403, False),
    (SpotifyNotFoundError("private detail"), 404, False),
    (SpotifyTokenExpiredError("private detail"), 401, True),
    (SpotifyAPIError("private detail"), 502, True),
])
def test_review_requires_fresh_authorization_and_reports_read_failures(body, failure, status, retryable):
    privacy = body["kind"] == "privacy"
    api = MetadataSpotify(False) if privacy else MutableSpotify(OWNER, target=[uri(1), uri(2)])
    session = seeded([[uri(2), uri(1)], [uri(1), uri(2)]])
    if failure == "not_editable":
        api.owner = "someone-else"
    elif privacy:
        api.read_error = failure
    else:
        api.read_failure = failure
    payload, actual = resolve(api, body, session, playlist_id="p1" if privacy else "target")
    assert actual == status
    assert payload["success"] is False
    assert payload["operation"]["kind"] == body["kind"]
    assert payload["operation"]["retryable"] is retryable
    assert "context" not in payload
    assert "private detail" not in json.dumps(payload)
    assert api.writes == []


SUMMARY_CASES = [
    ([], []),
    ([uri(1)], []),
    ([], [uri(1), uri(1)]),
    ([uri(1), uri(2)], [uri(2), uri(1)]),
    ([uri(1), uri(1), uri(2)], [uri(1), uri(2), uri(1)]),
    ([uri(1), uri(2), uri(1)], [uri(1), uri(1)]),
    ([uri(3), uri(1), uri(2)], [uri(1), uri(4), uri(2)]),
    ([uri(2), uri(1), uri(2), uri(3)], [uri(1), uri(2), uri(2), uri(2)]),
]


def test_summary_matches_the_shipped_workshop_review_summary(node):
    """Reviews describe changes exactly as the shipped WorkshopSave.summarize does."""
    script = WORKSHOP_CLIENT + (
        f"const cases = {json.dumps(SUMMARY_CASES)};\n"
        "console.log(JSON.stringify(cases.map(([baseline, desired]) => client.summarize(baseline, desired))));\n"
    )
    shipped = json.loads(run_scenario(node, script, ROOT).stdout.splitlines()[0])
    assert [summarize(*case) for case in SUMMARY_CASES] == [
        {"added_count": item["added"], "removed_count": item["removed"], "order_changed": item["orderChanged"]}
        for item in shipped
    ]
