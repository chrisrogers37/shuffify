"""Workshop writes require a complete baseline and a confirmed final order."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from flask.sessions import SecureCookieSession

from shuffify.services.playlist_service import PlaylistAccessError
from shuffify.services.workshop_commit_service import WorkshopCommitService
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyPartialBatchError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)

A, B, C = ["spotify:track:" + c * 22 for c in "abc"]
MODULE = "shuffify.services.workshop_commit_service"


class PlaylistAPI:
    """In-memory raw-page fixture; never contacts any external service."""

    def __init__(self, uris):
        self.uris = list(uris)
        self.revision = 1
        self.owner = "alice"
        self.collaborative = False
        self.writes = []
        self.reads = []
        self.page_transform = lambda page: page
        self.metadata_transform = lambda metadata: metadata
        self.write_hook = None
        self.wrapper = "item"

    def get_playlist(self, playlist_id, skip_cache=False):
        assert skip_cache is True
        self.reads.append(("metadata", playlist_id))
        return self.metadata_transform(
            {
                "name": "Fictional mix",
                "owner": {"id": self.owner},
                "collaborative": self.collaborative,
                "snapshot_id": str(self.revision),
                "items": {"total": len(self.uris)},
            }
        )

    def get_playlist_items_raw(self, playlist_id, limit=100, offset=0):
        assert 1 <= limit <= 50  # Read limit differs from write batch size.
        self.reads.append(("items", offset))
        items = [
            {
                self.wrapper: {
                    "uri": uri,
                    "id": uri.rsplit(":", 1)[-1],
                    "type": "track",
                    "name": "Track",
                    "artists": [{"name": "Artist"}],
                }
            }
            for uri in self.uris[offset : offset + limit]
        ]
        next_offset = offset + len(items)
        return self.page_transform(
            {
                "items": items,
                "total": len(self.uris),
                "offset": offset,
                "next": (
                    f"https://fake.invalid/items?offset={next_offset}"
                    if next_offset < len(self.uris)
                    else None
                ),
            }
        )

    def update_playlist_tracks(self, playlist_id, uris):
        self.writes.append(list(uris))
        if self.write_hook:
            return self.write_hook(uris)
        self.uris = list(uris)
        self.revision += 1
        return True


@pytest.fixture
def context():
    api = PlaylistAPI([A, B])
    user = SimpleNamespace(id=7, spotify_id="alice")
    session = SecureCookieSession({"retained": "draft"})
    with (
        patch(f"{MODULE}.PlaylistSnapshotService") as snapshots,
        patch(f"{MODULE}.TrackLockService") as locks,
        patch(f"{MODULE}.ActivityLogService") as activity,
    ):
        snapshots.is_auto_snapshot_enabled.return_value = True
        yield SimpleNamespace(
            api=api,
            user=user,
            session=session,
            snapshots=snapshots,
            locks=locks,
            activity=activity,
            service=WorkshopCommitService(api),
        )


def commit(ctx, baseline=None, desired=None):
    return ctx.service.commit(
        "playlist",
        ctx.user,
        ctx.session,
        [A, B] if baseline is None else baseline,
        [B, A] if desired is None else desired,
    ).to_dict()


def assert_no_effects(ctx):
    assert ctx.api.writes == []
    assert dict(ctx.session) == {"retained": "draft"}
    ctx.snapshots.create_snapshot.assert_not_called()
    ctx.locks.safe_reconcile_positions.assert_not_called()


def test_saved_order_is_read_back_before_history_and_locks(context):
    body = commit(context)
    assert body["target_status"] == "saved"
    assert body["saved_track_uris"] == [B, A]
    assert body["current_track_uris"] == [B, A]
    assert body["confirmed_batches"] == body["total_batches"] == 1
    assert context.api.writes == [[B, A]]
    assert context.session["playlist_states"]["playlist"]["states"] == [[A, B], [B, A]]
    assert len(context.api.reads) == 6
    assert context.snapshots.create_snapshot.call_args.kwargs["track_uris"] == [A, B]
    context.locks.safe_reconcile_positions.assert_called_once_with(
        7, "playlist", [B, A]
    )


@pytest.mark.parametrize(
    "current,baseline,desired",
    [
        ([B, A], [A, B], [A]),
        ([A, A, B], [A, B], [B, A]),
        ([A, B], [A, A, B], [B, A]),
    ],
)
def test_order_and_duplicate_drift_never_write(context, current, baseline, desired):
    context.api.uris = current
    body = commit(context, baseline, desired)
    assert body["error_code"] == "stale_draft"
    assert body["current_track_uris"] == current
    assert "saved_track_uris" not in body
    assert body["retryable"] is False
    assert_no_effects(context)


@pytest.mark.parametrize(
    "baseline,desired", [([], [A]), ([A, B], []), ([A, B], [A, A, B])]
)
def test_empty_and_duplicate_orders_are_preserved(context, baseline, desired):
    context.api.uris = baseline
    body = commit(context, baseline, desired)
    assert body["target_status"] == "saved"
    assert body["saved_track_uris"] == desired


@pytest.mark.parametrize("current", [[], [A, B], [A, A, B]])
def test_already_applied_precedes_stale_comparison(context, current):
    context.api.uris = current
    body = commit(context, [C], current)
    assert body["target_status"] == "already_applied"
    assert body["saved_track_uris"] == current
    assert context.api.writes == []
    context.snapshots.create_snapshot.assert_not_called()


@pytest.mark.parametrize(
    "entry",
    [
        {"item": None},
        {"track": None},
        {},
        {"item": {"uri": A, "type": "episode"}},
        {"item": {"uri": "spotify:local:artist:album:title"}},
        {"item": {"uri": A, "is_local": True}},
        {"item": {"uri": A}, "is_local": True},
    ],
)
def test_unsupported_items_are_never_silently_dropped(context, entry):
    context.api.page_transform = lambda page: {
        **page,
        "items": [entry, page["items"][1]],
    }
    body = commit(context)
    assert body["error_code"] == "unsupported_playlist_items"
    assert body["representable"] is False
    assert "current_track_uris" not in body
    assert_no_effects(context)


@pytest.mark.parametrize("wrapper", ["item", "track"])
def test_complete_raw_pagination_both_wrappers(context, wrapper):
    context.api.wrapper = wrapper
    context.api.uris = [A, B] * 75 + [A]
    body = commit(context, context.api.uris, [C])
    assert body["target_status"] == "saved"
    assert ("items", 100) in context.api.reads


@pytest.mark.parametrize(
    "changes",
    [
        {"total": 3},
        {"offset": 1},
        {"total": True},
        {"items": []},
        {"next": "https://fake.invalid/items?offset=2"},
    ],
)
def test_incomplete_pages_cannot_establish_a_baseline(context, changes):
    context.api.page_transform = lambda page: {**page, **changes}
    body = commit(context)
    assert body["error_code"] == "upstream_read_failed"
    assert body["retryable"] is True
    assert_no_effects(context)


def test_invalid_page_continuation_and_changing_total_fail(context):
    context.api.uris = [A] * 101
    context.api.page_transform = lambda page: {
        **page,
        "next": "https://fake.invalid/items?offset=0",
    }
    assert commit(context)["error_code"] == "upstream_read_failed"
    assert_no_effects(context)


def test_snapshot_changes_during_read_fail_without_write(context):
    calls = 0

    def metadata(value):
        nonlocal calls
        calls += 1
        return {**value, "snapshot_id": str(calls)}

    context.api.metadata_transform = metadata
    assert commit(context)["error_code"] == "upstream_read_failed"
    assert_no_effects(context)


@pytest.mark.parametrize("snapshot", [None, "", 123])
def test_missing_snapshot_cannot_prove_consistent_read(context, snapshot):
    context.api.metadata_transform = lambda metadata: {
        **metadata,
        "snapshot_id": snapshot,
    }
    assert commit(context)["error_code"] == "upstream_read_failed"
    assert_no_effects(context)


def test_page_total_cannot_change_mid_read(context):
    context.api.uris = [A] * 101
    context.api.page_transform = lambda page: (
        {**page, "total": 102} if page["offset"] else page
    )
    assert commit(context)["error_code"] == "upstream_read_failed"
    assert_no_effects(context)


def test_coordination_failure_is_known_not_written(context):
    with patch(f"{MODULE}.playlist_lock", side_effect=RuntimeError("db unavailable")):
        body = commit(context)
    assert body["error_code"] == "prewrite_failed"
    assert body["retryable"] is True
    assert_no_effects(context)


def test_permission_is_fresh_and_precedes_raw_reads(context):
    context.api.owner = "bob"
    with pytest.raises(PlaylistAccessError):
        commit(context)
    assert context.api.reads == [("metadata", "playlist")]
    assert_no_effects(context)


def test_collaborator_may_commit(context):
    context.api.owner = "bob"
    context.api.collaborative = True
    assert commit(context)["target_status"] == "saved"


@pytest.mark.parametrize("operation", ["commit", "read"])
def test_busy_lock_does_not_read_or_mutate(context, operation):
    @contextmanager
    def busy(*args, **kwargs):
        assert kwargs["timeout_s"] == 2.0
        yield False

    with patch(f"{MODULE}.playlist_lock", busy):
        result = (
            commit(context)
            if operation == "commit"
            else context.service.read_current("playlist", context.user).to_dict()
        )
    assert result["error_code"] == "operation_in_progress"
    assert context.api.reads == []
    assert_no_effects(context)


def test_snapshot_failure_is_nonblocking(context):
    context.snapshots.is_auto_snapshot_enabled.side_effect = RuntimeError("unavailable")
    assert commit(context)["target_status"] == "saved"


def batch_error(cause, completed=0, total=1):
    return SpotifyPartialBatchError(
        "playlist", "update", completed, total, [], [], cause
    )


@pytest.mark.parametrize(
    "error,expected,retryable",
    [
        (batch_error(SpotifyWriteUnverifiedError("secret")), "unknown", False),
        (batch_error(SpotifyAPIError("secret")), "unknown", False),
        (SpotifyAPIError("secret"), "unknown", False),
        (batch_error(SpotifyWriteRejectedError("secret")), "not_written", True),
        (batch_error(SpotifyWriteUnverifiedError("secret"), 1, 2), "partial", False),
        (batch_error(SpotifyAuthError("secret"), 1, 2), "partial", False),
    ],
)
def test_batch_failures_preserve_certainty_without_history(
    context, error, expected, retryable
):
    context.api.write_hook = Mock(side_effect=error)
    body = commit(context)
    assert body["target_status"] == expected
    assert body["retryable"] is retryable
    assert "secret" not in str(body)
    assert "saved_track_uris" not in body
    assert "current_track_uris" not in body
    assert dict(context.session) == {"retained": "draft"}
    context.locks.safe_reconcile_positions.assert_not_called()
    assert len(context.api.writes) == 1


def test_first_auth_failure_preserves_auth_handling(context):
    context.api.write_hook = Mock(side_effect=batch_error(SpotifyAuthError("expired")))
    with pytest.raises(SpotifyAuthError):
        commit(context)


@pytest.mark.parametrize("operation", ["read", "commit"])
def test_raw_item_permission_error_remains_forbidden(context, operation):
    context.api.get_playlist_items_raw = Mock(
        side_effect=SpotifyForbiddenError("secret")
    )
    with pytest.raises(PlaylistAccessError, match="Spotify refused access"):
        if operation == "commit":
            commit(context)
        else:
            context.service.read_current("playlist", context.user)
    assert_no_effects(context)


def test_first_batch_permission_error_remains_forbidden(context):
    context.api.write_hook = Mock(
        side_effect=batch_error(SpotifyForbiddenError("secret"))
    )
    with pytest.raises(PlaylistAccessError, match="Spotify refused access"):
        commit(context)
    assert dict(context.session) == {"retained": "draft"}


def test_verification_mismatch_reports_complete_observed_order(context):
    context.api.write_hook = lambda uris: True  # Response alone is not confirmation.
    body = commit(context)
    assert body["target_status"] == "unknown"
    assert body["current_track_uris"] == [A, B]
    assert "saved_track_uris" not in body
    assert dict(context.session) == {"retained": "draft"}


def test_verification_failure_is_unknown_even_if_auth_expires(context):
    def write(uris):
        context.api.get_playlist = Mock(side_effect=SpotifyAuthError("secret"))
        return True

    context.api.write_hook = write
    body = commit(context)
    assert body["target_status"] == "unknown"
    assert body["confirmed_batches"] == 1
    assert dict(context.session) == {"retained": "draft"}


def test_current_state_is_read_only_and_preserves_order_metadata(context):
    context.api.uris = [A, A, B]
    body = context.service.read_current("playlist", context.user).to_dict()
    assert body["success"] is True
    assert body["current_track_uris"] == [A, A, B]
    assert [track["uri"] for track in body["current_tracks"]] == [A, A, B]
    assert body["loaded_at"].endswith("+00:00")
    assert "saved_track_uris" not in body
    assert_no_effects(context)


def test_revision_is_frozen_before_dispatch(context):
    desired = [B, A]

    def write(uris):
        desired.append(C)
        context.api.uris = list(uris)
        return True

    context.api.write_hook = write
    body = commit(context, desired=desired)
    assert desired == [B, A, C]
    assert body["saved_track_uris"] == [B, A]


def test_failed_bookkeeping_does_not_reclassify_confirmed_save(context):
    with patch(
        f"{MODULE}.StateService.ensure_playlist_initialized",
        side_effect=RuntimeError("db"),
    ):
        body = commit(context)
    assert body["target_status"] == "saved"
    assert body["saved_track_uris"] == [B, A]


def test_newly_adopted_baseline_is_the_next_undo_target(context):
    from shuffify.services.state_service import StateService

    StateService.initialize_playlist_state(context.session, "playlist", [A])
    context.api.uris = [A, B]
    # Read-only reconciliation itself must leave session history untouched.
    context.service.read_current("playlist", context.user)
    assert StateService.get_current_uris(context.session, "playlist") == [A]
    body = commit(context, [A, B], [B, C, A])
    assert body["target_status"] == "saved"
    assert StateService.undo(context.session, "playlist") == [A, B]
