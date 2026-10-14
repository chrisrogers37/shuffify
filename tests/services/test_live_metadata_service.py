"""Visibility outcomes against mutable metadata, with no track API available."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shuffify.services.live_metadata_service import LiveMetadataService
from shuffify.services.live_review_bindings import scope_fingerprint
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
)

OWNER = SimpleNamespace(spotify_id="user123")
MISSING = object()


class MetadataSpotify:
    """Only metadata methods exist, so track-pipeline calls cannot succeed."""

    def __init__(self, public=False):
        self.public = public
        self.owner = OWNER.spotify_id
        self.reads = []
        self.writes = []
        self.read_error = None
        self.after_write = None
        self.write_error = None

    def get_playlist(self, playlist_id, *, skip_cache=False):
        self.reads.append((playlist_id, skip_cache))
        if self.read_error:
            raise self.read_error
        metadata = {
            "id": playlist_id, "name": "Fresh playlist name",
            "owner": {"id": self.owner}, "collaborative": False,
        }
        if self.public is not MISSING:
            metadata["public"] = self.public
        return metadata

    def update_playlist_details(self, playlist_id, *, public):
        self.writes.append((playlist_id, public))
        if self.write_error:
            raise self.write_error
        self.public = public
        if self.after_write:
            self.after_write(self)


def resource(result):
    assert not {"execution_id", "started_at", "execution_state", "tracks_added", "tracks_total"} & result.keys()
    assert result["operation"]["kind"] == "privacy"
    assert result["operation"]["bookkeeping"] == []
    entry = result["operation"]["resources"][0]
    assert entry["mutation_type"] == entry["verification"] == "metadata"
    assert not any("track" in key for key in entry)
    return entry


@pytest.mark.parametrize("public", [None, "false", "true", 0, 1, [], {}])
def test_invalid_boolean_never_reads_or_dispatches(public):
    api = MetadataSpotify()
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=public)
    assert result["http_status"] == 400
    assert result["operation"]["error_code"] == "invalid_public"
    assert result["operation"]["resources"] == []
    assert api.reads == api.writes == []


@pytest.mark.parametrize("public", [True, False])
def test_verified_visibility_uses_actual_fresh_reads(public):
    api = MetadataSpotify(not public)
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=public)
    entry = resource(result)
    assert result["http_status"] == 200
    assert result["status"] == "success"
    assert result["public"] is api.public is public
    assert entry["status"] == "complete"
    assert entry["affected_count"] == 1
    assert entry["resource_name"] == "Fresh playlist name"
    assert entry["baseline_metadata"] == {"public": not public}
    assert entry["desired_metadata"] == entry["current_metadata"] == {"public": public}
    assert api.reads == [("p1", True), ("p1", True)]
    assert api.writes == [("p1", public)]


@pytest.mark.parametrize("public", [True, False])
def test_noop_is_a_pure_metadata_observation(public):
    api = MetadataSpotify(public)
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=public)
    entry = resource(result)
    assert result["http_status"] == 200
    assert entry["status"] == "not_required"
    assert entry["affected_count"] == 0
    assert entry["dispatched"] is False
    assert api.reads == [("p1", True)]
    assert api.writes == []


@pytest.mark.parametrize("public", [MISSING, None, "false", 0, 1])
def test_unreadable_baseline_never_dispatches(public):
    api = MetadataSpotify(public)
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=True)
    entry = resource(result)
    assert result["http_status"] == 502
    assert result["operation"]["aggregate_status"] == "partial"
    assert entry["status"] == "failed"
    assert entry["dispatched"] is False
    assert entry["desired_metadata"] == {"public": True}
    assert "baseline_metadata" not in entry
    assert "current_metadata" not in entry
    assert api.writes == []


def test_access_denial_is_known_no_dispatch_without_trusted_names():
    api = MetadataSpotify()
    api.owner = "someone-else"
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=True)
    assert result["http_status"] == 403
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["resources"] == []
    assert api.writes == []


@pytest.mark.parametrize(
    "error,status,code,retryable", [
        (SpotifyAuthError("private detail"), 401, "authentication_required", True),
        (SpotifyForbiddenError("private detail"), 403, "access_denied", False),
        (SpotifyNotFoundError("private detail"), 404, "playlist_not_found", False),
        (RuntimeError("private detail"), 502, "metadata_unavailable", True),
    ]
)
def test_preflight_read_error_has_safe_no_effect_result(error, status, code, retryable):
    api = MetadataSpotify()
    api.read_error = error
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=True)
    assert result["http_status"] == status
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["error_code"] == code
    assert result["operation"]["retryable"] is retryable
    assert result["operation"]["resources"] == result["operation"]["bookkeeping"] == []
    assert "private detail" not in str(result)
    assert api.reads == [("p1", True)]
    assert api.writes == []


def test_unwrapped_missing_playlist_is_also_a_terminal_no_effect_failure():
    api = MetadataSpotify()
    service = LiveMetadataService(api)
    with patch.object(
        service.playlists, "validate_user_can_edit", side_effect=SpotifyNotFoundError("private detail")
    ):
        result = service.set_visibility("p1", OWNER, public=True)
    assert result["http_status"] == 404
    assert result["message"] == "Playlist not found."
    assert result["operation"]["error_code"] == "playlist_not_found"
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["retryable"] is False
    assert result["operation"]["resources"] == []
    assert api.writes == []


@pytest.mark.parametrize("error,status", [
    (SpotifyWriteRejectedError("private detail"), 502),
    (SpotifyAuthError("private detail"), 401),
    (SpotifyForbiddenError("private detail"), 403),
    (SpotifyNotFoundError("private detail"), 404),
    (SpotifyRateLimitError("private detail"), 429),
    (SpotifyQuotaExceededError("private detail"), 429),
])
def test_explicit_rejection_is_failed_with_dispatch_provenance(error, status):
    api = MetadataSpotify()
    api.write_error = error
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=True)
    entry = resource(result)
    assert result["http_status"] == status
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["error_code"] == entry["error_code"] == "write_rejected"
    assert "No changes were made" in result["message"]
    assert entry["status"] == "failed"
    assert entry["dispatched"] is True
    assert entry["affected_count"] == 0
    assert result["operation"]["retryable"] is entry["retryable"] is True
    assert api.reads == [("p1", True)]
    assert api.writes == [("p1", True)]
    assert api.public is False
    assert "current_metadata" not in entry
    assert "private detail" not in str(result)


def test_lost_acknowledgement_keeps_intent_after_actual_change():
    api = MetadataSpotify()

    def lose_reply(_api):
        raise TimeoutError("private transport detail")

    api.after_write = lose_reply
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=True)
    entry = resource(result)
    assert api.public is True
    assert api.writes == [("p1", True)]
    assert result["http_status"] == 502
    assert result["operation"]["aggregate_status"] == "unknown"
    assert result["operation"]["retryable"] is False
    assert entry["dispatched"] is True
    assert entry["affected_count"] is None
    assert entry["desired_metadata"] == {"public": True}
    assert entry["baseline_metadata"] == {"public": False}
    assert "current_metadata" not in entry
    assert "loaded_at" not in entry
    assert "public" not in result
    assert "private transport detail" not in str(result)


@pytest.mark.parametrize("after", [MISSING, None, "true", 1, False])
def test_invalid_or_wrong_readback_is_unknown_not_acknowledged_success(after):
    api = MetadataSpotify()
    api.after_write = lambda fake: setattr(fake, "public", after)
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=True)
    entry = resource(result)
    assert result["http_status"] == 502
    assert entry["status"] == "unknown"
    assert entry["error_code"] == "verification_failed"
    assert entry["affected_count"] is None
    assert result["operation"]["retryable"] is False
    assert "public" not in result
    if type(after) is bool:
        assert entry["current_metadata"] == {"public": after}
    else:
        assert "current_metadata" not in entry
    assert len(api.writes) == 1


@pytest.mark.parametrize("fault", ["read_failure", "permission_lost"])
def test_readback_is_fresh_and_authorized(fault):
    api = MetadataSpotify()

    def fail_after_write(fake):
        if fault == "read_failure":
            fake.read_error = RuntimeError("private detail")
        else:
            fake.owner = "someone-else"

    api.after_write = fail_after_write
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=True)
    entry = resource(result)
    assert api.public is True
    assert entry["status"] == "unknown"
    assert "current_metadata" not in entry
    assert "private detail" not in str(result)
    assert api.reads == [("p1", True), ("p1", True)]


@pytest.mark.parametrize("desired,actual,status", [
    (True, True, "complete"), (False, False, "complete"), (None, True, "unknown"),
    (False, True, "unknown"), (True, None, "unknown"),
])
def test_inspection_only_observes_retained_boolean(desired, actual, status):
    api = MetadataSpotify(actual)
    result = LiveMetadataService(api).inspect_visibility("p1", OWNER, desired_public=desired)
    entry = resource(result)
    assert result["inspection_only"] is True
    assert result["operation"]["aggregate_status"] == status
    assert result["operation"]["retryable"] is False
    assert entry["dispatched"] is False
    assert "baseline_metadata" not in entry
    assert api.reads == [("p1", True)]
    assert api.writes == []
    if desired is None:
        assert entry["error_code"] == "intent_unavailable"
        assert entry["current_metadata"] == {"public": True}


def test_inspection_denied_is_unknown_not_failed_no_effect():
    api = MetadataSpotify()
    api.owner = "someone-else"
    result = LiveMetadataService(api).inspect_visibility("p1", OWNER, desired_public=True)
    assert result["http_status"] == 403
    assert result["inspection_only"] is True
    assert result["operation"]["aggregate_status"] == "unknown"
    assert result["operation"]["retryable"] is False
    assert api.writes == []


def test_inspection_invalid_retained_intent_never_reads():
    api = MetadataSpotify()
    result = LiveMetadataService(api).inspect_visibility("p1", OWNER, desired_public="false")
    assert result["http_status"] == 400
    assert result["inspection_only"] is True
    assert result["operation"]["aggregate_status"] == "unknown"
    assert api.reads == api.writes == []


@pytest.mark.parametrize("error,status,code", [
    (SpotifyAuthError("private detail"), 401, "authentication_required"),
    (SpotifyNotFoundError("private detail"), 404, "playlist_not_found"),
    (SpotifyForbiddenError("private detail"), 403, "access_denied"),
    (RuntimeError("private detail"), 502, "metadata_unavailable"),
])
def test_inspection_read_rejection_never_permits_replay(error, status, code):
    api = MetadataSpotify()
    api.read_error = error
    result = LiveMetadataService(api).inspect_visibility("p1", OWNER, desired_public=True)
    assert result["http_status"] == status
    assert result["inspection_only"] is True
    assert result["operation"]["aggregate_status"] == "unknown"
    assert result["operation"]["error_code"] == code
    assert result["operation"]["retryable"] is False
    assert result["operation"]["resources"] == []
    assert "private detail" not in str(result)
    assert api.reads == [("p1", True)]
    assert api.writes == []


@pytest.mark.parametrize("reviewed_public", [True, False])
def test_reviewed_visibility_compares_after_fresh_authorization(reviewed_public):
    api = MetadataSpotify(False)
    scope = scope_fingerprint("privacy", "p1", {"public": reviewed_public})
    result = LiveMetadataService(api).set_visibility("p1", OWNER, public=True, reviewed_scope=scope)
    assert api.reads[0] == ("p1", True)
    if reviewed_public:
        assert result["http_status"] == 200
        assert api.writes == [("p1", True)]
        return
    assert result["http_status"] == 409
    assert result["operation"]["error_code"] == "review_changed"
    assert result["operation"]["retryable"] is False
    entry = resource(result)
    assert (entry["status"], entry["dispatched"], entry["affected_count"]) == ("failed", False, 0)
    assert api.reads == [("p1", True)]
    assert api.writes == []
    assert "public" not in result


def test_a_matching_review_hash_never_authorizes():
    api = MetadataSpotify(False)
    api.owner = "someone-else"
    result = LiveMetadataService(api).set_visibility(
        "p1", OWNER, public=True, reviewed_scope=scope_fingerprint("privacy", "p1", {"public": True})
    )
    assert result["http_status"] == 403
    assert api.writes == []
