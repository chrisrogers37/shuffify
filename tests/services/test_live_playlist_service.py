"""Direct track effects against real session history and mutable Spotify state."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask.sessions import SecureCookieSession

from shuffify.models.db import ActivityLog, JobExecution
from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.services.live_review_bindings import (
    before_order_fingerprint,
    history_fingerprint,
    scope_fingerprint,
    shuffle_scope,
)
from shuffify.services.state_service import StateService
from shuffify.services.user_service import UserService
from shuffify.services.workshop_commit_service import WorkshopCommitService
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyTokenExpiredError,
    SpotifyWriteRejectedError,
)
from tests.services.test_live_executor_outcomes import MutableSpotify, uri


@pytest.fixture
def owner(db_app):
    return UserService.upsert_from_spotify({"id": "direct-owner", "display_name": "Owner", "images": []}).user


@pytest.fixture(autouse=True)
def snapshots():
    with patch("shuffify.services.live_playlist_service.PlaylistSnapshotService.auto_snapshot_if_enabled") as snapshot:
        yield snapshot


def saved_snapshot(owner, desired, target="target"):
    return SimpleNamespace(
        id=17,
        user_id=owner.id,
        playlist_id=target,
        track_uris=list(desired),
        to_dict=lambda: {"id": 17, "playlist_id": target, "track_uris": list(desired)},
    )


def seed_history(session, states, index=None):
    session["playlist_states"] = {
        "target": {"states": deepcopy(states), "current_index": len(states) - 1 if index is None else index}
    }
    session.modified = False


def run_action(kind, owner, api, session):
    service = LivePlaylistService(api)
    if kind == "undo":
        return service.undo("target", owner, session)
    if kind == "snapshot_restore":
        return service.restore_snapshot(saved_snapshot(owner, [uri(2), uri(1)]), owner, session)
    with patch(
        "shuffify.services.live_playlist_service.ShuffleService.execute", return_value=[uri(2), uri(1)]
    ) as execute:
        result = service.shuffle("target", owner, session, algorithm="BasicShuffle", params={})
    execute.assert_called_once()
    return result


def test_shuffle_uses_fresh_complete_membership_and_actual_before_history(owner, snapshots):
    actual = [uri(i) for i in range(101)] + [uri(1)]
    api, session = MutableSpotify(owner, target=actual), SecureCookieSession()
    seed_history(session, [[uri(99)]])
    desired = list(reversed(actual))
    with patch("shuffify.services.live_playlist_service.ShuffleService.execute", return_value=desired) as execute:
        result = LivePlaylistService(api).shuffle("target", owner, session, algorithm="BasicShuffle", params={})
    assert [t["uri"] for t in execute.call_args.args[1]] == actual
    assert all("added_at" in t for t in execute.call_args.args[1])
    execute.assert_called_once()
    assert result["status"] == "success"
    assert result["playlist"]["total_tracks"] == 102
    assert session["playlist_states"]["target"] == {"states": [[uri(99)], actual, desired], "current_index": 2}
    assert snapshots.call_args.kwargs["track_uris"] == actual
    assert api.writes == [("update", "target", desired)]
    assert ActivityLog.query.count() == 1
    assert JobExecution.query.count() == 0
    assert not {"execution_id", "started_at", "execution_state"} & result.keys()


def test_real_newest_first_preserves_dates_of_duplicate_occurrences(owner):
    api = MutableSpotify(owner, target=[uri(1), uri(1), uri(2)])
    real_page = api.get_playlist_items_raw

    def dated_page(*args, **kwargs):
        page = real_page(*args, **kwargs)
        for entry, date in zip(page["items"], ["2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z", "2026-02-01T00:00:00Z"]):
            entry["added_at"] = date
        return page

    api.get_playlist_items_raw = dated_page
    result = LivePlaylistService(api).shuffle(
        "target", owner, SecureCookieSession(), algorithm="NewestFirstShuffle", params={"jitter": 1}
    )
    assert result["status"] == "success"
    assert result["operation"]["resources"][0]["desired_track_uris"] == [uri(1), uri(2), uri(1)]
    assert api.writes == [("update", "target", [uri(1), uri(2), uri(1)])]


@pytest.mark.parametrize("kind", ["shuffle", "snapshot_restore"])
def test_noop_has_no_write_snapshot_activity_or_session_initialization(owner, kind, snapshots):
    api, session = MutableSpotify(owner, target=[uri(2), uri(1)]), SecureCookieSession()
    result = run_action(kind, owner, api, session)
    assert result["status"] == "success"
    assert result["operation"]["resources"][0]["status"] == "not_required"
    assert result["operation"]["resources"][0]["affected_count"] == 0
    assert result["operation"]["bookkeeping"][0]["status"] == "not_required"
    assert api.writes == []
    assert session == {}
    assert session.modified is False
    snapshots.assert_not_called()
    assert ActivityLog.query.count() == JobExecution.query.count() == 0


@pytest.mark.parametrize("kind", ["shuffle", "undo", "snapshot_restore"])
@pytest.mark.parametrize("fault", ["rejected", "partial", "lost", "readback", "wrong_order"])
def test_failed_or_unknown_write_never_changes_history_or_replays(owner, kind, fault):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    before = deepcopy(dict(session))

    def fail(fake, playlist_id, uris):
        if fault == "rejected":
            raise SpotifyPartialBatchError(playlist_id, "update", 0, 1, [], uris, SpotifyWriteRejectedError("private"))
        if fault == "partial":
            fake.playlists[playlist_id] = uris[:1]
            raise SpotifyPartialBatchError(playlist_id, "update", 1, 2, uris[:1], uris[1:], SpotifyAPIError("private"))
        if fault == "wrong_order":
            return True
        fake.playlists[playlist_id] = list(uris)
        if fault == "lost":
            raise TimeoutError("private transport detail")
        fake.read_failure = RuntimeError("private readback detail")
        return True

    api.faults[("update", "target")] = fail
    result = run_action(kind, owner, api, session)
    resource = result["operation"]["resources"][0]
    assert result["http_status"] == 502
    assert resource["status"] == ("failed" if fault == "rejected" else "unknown")
    assert resource["desired_track_uris"] == [uri(2), uri(1)]
    assert resource["baseline_track_uris"] == [uri(1), uri(2)]
    assert result["operation"]["retryable"] == (fault == "rejected")
    assert session == before
    assert len(api.writes) == 1
    assert "private" not in str(result)
    if fault in ("lost", "partial", "readback"):
        assert "current_track_uris" not in resource
    assert ActivityLog.query.count() == 0


def test_verified_undo_confirms_exactly_one_previous_saved_version(owner, snapshots):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(9)], [uri(2), uri(1)], [uri(1), uri(2)]])
    result = run_action("undo", owner, api, session)
    assert result["status"] == "success"
    assert result["playlist_state"]["current_index"] == 1
    assert StateService.get_current_uris(session, "target") == [uri(2), uri(1)]
    assert ActivityLog.query.count() == 0
    snapshots.assert_not_called()


def test_undo_already_matching_selected_order_confirms_pointer_without_write(owner, snapshots):
    api, session = MutableSpotify(owner, target=[uri(2), uri(1)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    result = run_action("undo", owner, api, session)
    assert result["status"] == "success"
    assert result["playlist_state"]["current_index"] == 0
    assert result["operation"]["resources"][0]["status"] == "not_required"
    assert result["operation"]["bookkeeping"][0]["status"] == "complete"
    assert api.writes == []
    snapshots.assert_not_called()
    assert ActivityLog.query.count() == 0


@pytest.mark.parametrize("kind", ["shuffle", "undo", "snapshot_restore"])
def test_verified_spotify_retained_when_history_confirmation_fails(owner, kind):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    method = "confirm_undo" if kind == "undo" else "record_verified_change"
    with patch.object(StateService, method, side_effect=RuntimeError("private session detail")):
        result = run_action(kind, owner, api, session)
    assert result["http_status"] == 502
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert result["operation"]["bookkeeping"][0]["status"] == "failed"
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["retryable"] is False
    assert "playlist_state" not in result
    assert result["playlist"]["tracks"][0]["uri"] == uri(2)
    assert len(api.writes) == 1
    assert ActivityLog.query.count() == (0 if kind == "undo" else 1)


def test_undo_history_changed_before_dispatch_is_rejected(owner):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    original = StateService.peek_undo

    def changed(*args):
        selection = original(*args)
        session["playlist_states"]["target"]["states"][0] = [uri(9)]
        return selection

    with patch.object(StateService, "peek_undo", side_effect=changed):
        result = run_action("undo", owner, api, session)
    assert result["http_status"] == 409
    assert result["operation"]["error_code"] == "history_changed"
    assert result["operation"]["retryable"] is False
    assert api.writes == []


@pytest.mark.parametrize("kind", ["shuffle", "undo", "snapshot_restore"])
def test_resource_drift_refuses_dispatch_and_history_but_allows_fresh_attempt(owner, kind, snapshots):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    history = deepcopy(dict(session))
    real_read = WorkshopCommitService.read_complete
    reads = []

    def external_edit_after_first_read(reader, *args):
        observed = real_read(reader, *args)
        reads.append(list(observed.track_uris))
        if len(reads) == 1:
            api.playlists["target"] = [uri(3), uri(1), uri(2)]
            api.revisions["target"] += 1
        return observed

    with patch.object(WorkshopCommitService, "read_complete", external_edit_after_first_read):
        result = run_action(kind, owner, api, session)
    operation = result["operation"]
    resource = operation["resources"][0]
    assert reads == [[uri(1), uri(2)], [uri(3), uri(1), uri(2)]]
    assert result["http_status"] == 409
    assert operation["error_code"] == "resource_changed"
    assert operation["aggregate_status"] == "partial"
    assert operation["retryable"] is True
    assert resource["dispatched"] is False
    assert resource["status"] == "failed"
    assert resource["affected_count"] == 0
    assert resource["retryable"] is True
    assert api.writes == []
    assert session == history
    assert ActivityLog.query.count() == 0
    if kind == "undo":
        snapshots.assert_not_called()
    else:
        # This is a best-effort pre-attempt backup, not a receipt of a write.
        snapshots.assert_called_once()
        assert snapshots.call_args.kwargs["track_uris"] == [uri(1), uri(2)]


@pytest.mark.parametrize("kind", ["shuffle", "undo", "snapshot_restore"])
@pytest.mark.parametrize(
    ("error_type", "status"),
    [
        (SpotifyAuthError, 401),
        (SpotifyTokenExpiredError, 401),
        (SpotifyForbiddenError, 403),
        (SpotifyNotFoundError, 404),
        (SpotifyRateLimitError, 429),
        (SpotifyQuotaExceededError, 429),
        (SpotifyWriteRejectedError, 502),
    ],
)
def test_first_batch_typed_rejection_preserves_zero_effects_and_reports_status(owner, kind, error_type, status):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    history = deepcopy(dict(session))

    def rejected(fake, playlist_id, uris):
        raise SpotifyPartialBatchError(playlist_id, "update", 0, 1, [], uris, error_type("private provider detail"))

    api.faults[("update", "target")] = rejected
    result = run_action(kind, owner, api, session)
    operation = result["operation"]
    resource = operation["resources"][0]
    assert result["http_status"] == status
    assert result["status"] == "failed"
    assert operation["aggregate_status"] == "partial"
    assert operation["error_code"] == "write_rejected"
    assert operation["retryable"] is True
    assert resource["dispatched"] is True
    assert resource["status"] == "failed"
    assert resource["affected_count"] == 0
    assert resource["baseline_track_uris"] == [uri(1), uri(2)]
    assert resource["desired_track_uris"] == [uri(2), uri(1)]
    assert "current_track_uris" not in resource
    assert "private" not in str(result)
    assert "try" in result["message"].lower()
    assert api.playlists["target"] == [uri(1), uri(2)]
    assert len(api.writes) == 1
    assert session == history
    assert ActivityLog.query.count() == 0


@pytest.mark.parametrize("kind", ["shuffle", "undo", "snapshot_restore"])
@pytest.mark.parametrize("point", ["later_batch", "readback"])
def test_typed_rejection_after_possible_effects_remains_unknown(owner, kind, point):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    history = deepcopy(dict(session))

    def uncertain(fake, playlist_id, uris):
        if point == "later_batch":
            fake.playlists[playlist_id] = uris[:1]
            raise SpotifyPartialBatchError(
                playlist_id, "update", 1, 2, uris[:1], uris[1:], SpotifyRateLimitError("private later rejection")
            )
        fake.playlists[playlist_id] = list(uris)
        fake.read_failure = SpotifyTokenExpiredError("private readback rejection")
        return True

    api.faults[("update", "target")] = uncertain
    result = run_action(kind, owner, api, session)
    operation = result["operation"]
    resource = operation["resources"][0]
    assert result["http_status"] == 502
    assert operation["aggregate_status"] == "unknown"
    assert operation["retryable"] is False
    assert resource["status"] == "unknown"
    assert resource["affected_count"] is None
    assert resource["dispatched"] is True
    assert "private" not in str(result)
    assert len(api.writes) == 1
    assert session == history
    assert ActivityLog.query.count() == 0


@pytest.mark.parametrize("kind", ["shuffle", "undo", "snapshot_restore"])
@pytest.mark.parametrize("point", ["authorization", "track_read"])
@pytest.mark.parametrize(
    ("error_type", "status", "code"),
    [(SpotifyNotFoundError, 404, "playlist_not_found"), (SpotifyForbiddenError, 403, "playlist_access_denied")],
)
def test_direct_and_wrapped_access_errors_keep_typed_status(owner, kind, point, error_type, status, code):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    history = deepcopy(dict(session))
    method = "get_playlist" if point == "authorization" else "get_playlist_items_raw"
    with patch.object(api, method, side_effect=error_type("private access detail")):
        service = LivePlaylistService(api)
        result = (
            service.restore_snapshot(saved_snapshot(owner, [uri(2), uri(1)]), owner, session)
            if kind == "snapshot_restore"
            else getattr(service, kind)("target", owner, session, **(
                {"algorithm": "BasicShuffle", "params": {}} if kind == "shuffle" else {}
            ))
        )
    assert result["http_status"] == status
    assert result["operation"]["error_code"] == code
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["retryable"] is False
    assert "private" not in str(result)
    assert api.writes == []
    assert session == history


def test_undo_history_changed_after_write_keeps_newer_history(owner):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    api.on_write = lambda: seed_history(session, [[uri(9)], [uri(8)]])
    result = run_action("undo", owner, api, session)
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert result["operation"]["bookkeeping"][0]["status"] == "failed"
    assert StateService.get_current_uris(session, "target") == [uri(8)]


def test_snapshot_missing_history_starts_with_actual_before(owner, snapshots):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    result = run_action("snapshot_restore", owner, api, session)
    assert result["status"] == "success"
    assert result["playlist_id"] == "target"
    assert result["snapshot"]["id"] == 17
    assert result["playlist_state"] == {"states": [[uri(1), uri(2)], [uri(2), uri(1)]], "current_index": 1}
    assert snapshots.call_args.kwargs["track_uris"] == [uri(1), uri(2)]


@pytest.mark.parametrize("case", ["missing_history", "original", "denied", "empty_snapshot"])
def test_preflight_rejections_do_not_initialize_or_advance_history(owner, case):
    api, session = MutableSpotify(owner, target=[uri(1)]), SecureCookieSession()
    if case == "original":
        seed_history(session, [[uri(1)]])
    if case == "denied":
        api.owner = "another-user"
    before = deepcopy(dict(session))
    service = LivePlaylistService(api)
    result = (
        service.restore_snapshot(saved_snapshot(owner, []), owner, session)
        if case == "empty_snapshot"
        else service.undo("target", owner, session)
    )
    assert result["http_status"] == (403 if case == "denied" else 400)
    assert result["operation"]["aggregate_status"] == "partial"
    assert api.writes == []
    assert session == before


def test_all_track_work_occurs_inside_one_target_lock(owner):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    held, entries = [], []

    @contextmanager
    def lock(playlist_id, timeout_s):
        assert not held
        assert timeout_s == 2.0
        held.append(True)
        entries.append(playlist_id)
        yield True
        held.pop()

    original = api.get_playlist
    api.get_playlist = lambda *args, **kwargs: original(*args, **kwargs) if held else pytest.fail("unlocked read")
    api.on_write = lambda: held or pytest.fail("unlocked write")
    with patch("shuffify.services.live_playlist_service.playlist_lock", lock):
        result = run_action("shuffle", owner, api, session)
    assert result["status"] == "success"
    assert entries == ["target"]


@pytest.mark.parametrize("intent", [None, [], [uri(1)], [uri(2)]])
def test_inspection_reads_only_and_cannot_infer_lost_random_intent(owner, intent, snapshots):
    api = MutableSpotify(owner, target=[uri(1)])
    with (
        patch("shuffify.services.live_playlist_service.playlist_lock") as lock,
        patch("shuffify.services.live_playlist_service.ShuffleService.execute") as shuffle,
    ):
        result = LivePlaylistService(api).inspect_tracks("target", owner, desired_track_uris=intent)
    assert result["inspection_only"] is True
    assert result["operation"]["aggregate_status"] == ("complete" if intent == [uri(1)] else "unknown")
    assert result["operation"]["retryable"] is False
    assert result["operation"]["resources"][0]["current_track_uris"] == [uri(1)]
    assert api.writes == []
    assert ActivityLog.query.count() == JobExecution.query.count() == 0
    shuffle.assert_not_called()
    snapshots.assert_not_called()
    lock.assert_not_called()


@pytest.mark.parametrize("failure_point", ["acquire", "release"])
def test_lock_failure_preserves_known_dispatch_boundary(owner, failure_point):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()

    @contextmanager
    def failing_lock(*args, **kwargs):
        if failure_point == "acquire":
            raise RuntimeError("private lock detail")
        yield True
        raise RuntimeError("private lock detail")

    with (
        patch("shuffify.services.live_playlist_service.playlist_lock", failing_lock),
        patch("shuffify.services.live_playlist_service.ShuffleService.execute", return_value=[uri(2), uri(1)]),
    ):
        result = LivePlaylistService(api).shuffle(
            "target", owner, session, algorithm="NewestFirstShuffle", params={"jitter": 1}
        )
    operation = result["operation"]
    assert result["http_status"] == 502
    assert operation["error_code"] == "coordination_failed"
    assert "private" not in str(result)
    if failure_point == "acquire":
        assert operation["aggregate_status"] == "partial"
        assert operation["retryable"] is True
        assert operation["resources"] == operation["bookkeeping"] == []
        assert session == {}
        assert api.writes == []
    else:
        assert operation["aggregate_status"] == "unknown"
        assert operation["retryable"] is False
        assert operation["resources"][0]["status"] == "complete"
        assert operation["bookkeeping"][0]["status"] == "complete"
        assert len(api.writes) == 1
        assert session["playlist_states"]["target"]["current_index"] == 1
        assert operation["bookkeeping"][-1]["kind"] == "coordination"
        assert operation["bookkeeping"][-1]["status"] == "unknown"
        assert result["playlist"]["total_tracks"] == 2


def undo_review(session, current, target="target"):
    selection = StateService.peek_undo(session, target)
    return {
        "reviewed_scope": scope_fingerprint("undo", target, {}),
        "reviewed_history": history_fingerprint(target, selection),
        "reviewed_before_order": before_order_fingerprint(target, current),
    }


def test_reviewed_shuffle_compares_inside_the_lock_before_any_algorithm(owner, snapshots):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    held, reads = [], []

    @contextmanager
    def lock(playlist_id, timeout_s):
        held.append(True)
        yield True
        held.pop()

    original = api.get_playlist
    api.get_playlist = lambda *args, **kwargs: reads.append(bool(held)) or original(*args, **kwargs)
    with (
        patch("shuffify.services.live_playlist_service.playlist_lock", lock),
        patch("shuffify.services.live_playlist_service.ShuffleService.execute") as execute,
    ):
        result = LivePlaylistService(api).shuffle(
            "target", owner, session, algorithm="BasicShuffle", params={"keep_first": 0},
            reviewed={"reviewed_scope": shuffle_scope("target", "BasicShuffle", {"keep_first": 1})},
        )
    assert result["http_status"] == 409
    operation = result["operation"]
    assert (operation["aggregate_status"], operation["error_code"], operation["retryable"]) == (
        "partial", "review_changed", False
    )
    assert operation["resources"][0]["dispatched"] is False
    assert reads and all(reads)  # Fresh authorization and read happened inside the target lock.
    execute.assert_not_called()
    snapshots.assert_not_called()
    assert api.writes == []
    assert session == {} and session.modified is False
    assert ActivityLog.query.count() == 0


def test_matching_reviewed_shuffle_runs_once(owner):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    reviewed = {"reviewed_scope": shuffle_scope("target", "BasicShuffle", {"keep_first": 0})}
    with patch(
        "shuffify.services.live_playlist_service.ShuffleService.execute", return_value=[uri(2), uri(1)]
    ) as execute:
        result = LivePlaylistService(api).shuffle(
            "target", owner, session, algorithm="BasicShuffle", params={"keep_first": 0}, reviewed=reviewed
        )
    assert result["status"] == "success"
    execute.assert_called_once()
    assert api.writes == [("update", "target", [uri(2), uri(1)])]


@pytest.mark.parametrize("field,code", [
    ("reviewed_scope", "review_changed"),
    ("reviewed_history", "history_changed"),
    ("reviewed_before_order", "resource_changed"),
])
def test_reviewed_undo_rejects_each_changed_binding_without_dispatch(owner, field, code):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    reviewed = undo_review(session, [uri(1), uri(2)])
    reviewed[field] = "f" * 64
    before = deepcopy(dict(session))
    result = LivePlaylistService(api).undo("target", owner, session, reviewed=reviewed)
    assert result["http_status"] == 409
    assert result["operation"]["error_code"] == code
    assert result["operation"]["retryable"] is False
    assert api.writes == []
    assert dict(session) == before


@pytest.mark.parametrize("change,code", [("history", "history_changed"), ("target", "resource_changed")])
def test_reviewed_undo_requires_review_again_after_a_later_change(owner, change, code):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    reviewed = undo_review(session, [uri(1), uri(2)])
    if change == "history":
        StateService.record_verified_change(session, "target", before_uris=[uri(1), uri(2)], after_uris=[uri(3)])
    else:
        api.playlists["target"] = [uri(1), uri(2), uri(3)]  # Added elsewhere after the review.
        api.revisions["target"] += 1
    before = deepcopy(dict(session))
    result = LivePlaylistService(api).undo("target", owner, session, reviewed=reviewed)
    assert result["http_status"] == 409
    assert result["operation"]["error_code"] == code
    assert api.writes == []
    assert dict(session) == before


def test_matching_reviewed_undo_advances_once_and_cannot_be_reused(owner):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2), uri(2)]), SecureCookieSession()
    states = [[uri(3)], [uri(2), uri(1), uri(2)], [uri(1), uri(2), uri(2)]]
    seed_history(session, states)
    reviewed = undo_review(session, [uri(1), uri(2), uri(2)])
    result = LivePlaylistService(api).undo("target", owner, session, reviewed=reviewed)
    assert result["status"] == "success"
    assert session["playlist_states"]["target"]["current_index"] == 1
    assert api.writes == [("update", "target", states[1])]
    # The same review never selects the next older version.
    again = LivePlaylistService(api).undo("target", owner, session, reviewed=reviewed)
    assert again["http_status"] == 409
    assert again["operation"]["error_code"] == "history_changed"
    assert session["playlist_states"]["target"]["current_index"] == 1
    assert len(api.writes) == 1


def test_reviewed_undo_with_expired_history_keeps_its_specific_400(owner):
    api, session = MutableSpotify(owner, target=[uri(1), uri(2)]), SecureCookieSession()
    seed_history(session, [[uri(2), uri(1)], [uri(1), uri(2)]])
    reviewed = undo_review(session, [uri(1), uri(2)])
    session.clear()
    result = LivePlaylistService(api).undo("target", owner, session, reviewed=reviewed)
    assert result["http_status"] == 400
    assert result["operation"]["error_code"] == "history_unavailable"
    assert api.writes == []
    assert "playlist_states" not in session


def test_track_inspection_reports_no_prewrite_baseline(owner):
    api = MutableSpotify(owner, target=[uri(1), uri(1)])
    result = LivePlaylistService(api).inspect_tracks("target", owner, desired_track_uris=[uri(1), uri(1)])
    assert result["operation"]["aggregate_status"] == "complete"
    entry = result["operation"]["resources"][0]
    assert "baseline_track_uris" not in entry
    assert entry["current_track_uris"] == entry["desired_track_uris"] == [uri(1), uri(1)]
    assert (entry["status"], entry["dispatched"], entry["affected_count"]) == ("not_required", False, 0)
