"""Source recovery uses real scoped DB records and an in-memory Spotify fake."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shuffify.enums import PendingRaidStatus
from shuffify.models.db import ActivityLog, db
from shuffify.services.pending_raid_service import PendingRaidService
from shuffify.services.playlist_pair_service import PlaylistPairService
from shuffify.services.raid_link_service import RaidLinkService
from shuffify.services.user_service import UserService
from shuffify.services.workshop_finalize_service import (
    WorkshopFinalizeResult,
    WorkshopFinalizeService,
)
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyPartialBatchError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)
from tests.paths import api_path
from tests.queued_operations import post_and_run

A, B, C = ["spotify:track:" + char * 22 for char in "abc"]
MODULE = "shuffify.services.workshop_finalize_service"


class SourceAPI:
    def __init__(self):
        self.playlists = {"target": [A, B], "raid": [A, A, C], "archive": [C, C]}
        self.names = {key: key.title() + " playlist" for key in self.playlists}
        self.owners = {key: "alice" for key in self.playlists}
        self.reads = []
        self.writes = []
        self.read_hook = None
        self.write_hook = None

    def get_playlist(self, playlist_id, skip_cache=False):
        assert skip_cache
        self.reads.append(playlist_id)
        if self.read_hook:
            self.read_hook(playlist_id)
        return {
            "name": self.names[playlist_id],
            "owner": {"id": self.owners[playlist_id]},
            "snapshot_id": repr(self.playlists[playlist_id]),
            "items": {"total": len(self.playlists[playlist_id])},
        }

    def get_playlist_items_raw(self, playlist_id, limit=50, offset=0):
        uris = self.playlists[playlist_id]
        page = uris[offset : offset + limit]
        end = offset + len(page)
        return {
            "items": [{"item": {"uri": uri}} for uri in page],
            "total": len(uris),
            "offset": offset,
            "next": f"https://fake.invalid/?offset={end}" if end < len(uris) else None,
        }

    def playlist_remove_items(self, playlist_id, uris):
        self._write("remove", playlist_id, uris)

    def playlist_add_items(self, playlist_id, uris):
        self._write("append", playlist_id, uris)

    def _write(self, action, playlist_id, uris, position=None):
        self.writes.append((action, playlist_id, list(uris)))
        if self.write_hook:
            return self.write_hook(action, playlist_id, uris)
        self.apply(action, playlist_id, uris)

    def apply(self, action, playlist_id, uris):
        if action in ("append", "add"):
            self.playlists[playlist_id].extend(uris)
        else:
            self.playlists[playlist_id] = [
                uri for uri in self.playlists[playlist_id] if uri not in uris
            ]


@pytest.fixture
def ctx(db_app):
    with db_app.app_context():
        user = UserService.upsert_from_spotify({"id": "alice", "images": []}).user
        api = SourceAPI()
        RaidLinkService.create_link(user.id, "target", "raid")
        PlaylistPairService.create_pair(user.id, "target", "archive")
        PendingRaidService.stage_tracks(
            user.id,
            "target",
            [{"uri": A, "name": "Track", "source_playlist_id": "discovery"}],
        )
        record = PendingRaidService.list_pending(user.id, "target")[0]
        yield SimpleNamespace(
            api=api, user=user, record=record, service=WorkshopFinalizeService(api)
        )


def raid(ctx, **overrides):
    params = dict(
        source_playlist_id="raid", target_track_uris=[A, B], track_ids=[ctx.record.id]
    )
    params.update(overrides)
    return ctx.service.finalize_raids("target", ctx.user, **params)


def restore(ctx, **overrides):
    params = dict(
        source_playlist_id="archive", target_track_uris=[A, B], track_uris=[A]
    )
    params.update(overrides)
    return ctx.service.finalize_restore("target", ctx.user, **params)


def append(ctx, **overrides):
    params = dict(
        source_playlist_id="archive",
        target_track_uris=[A, B],
        baseline_track_uris=[C, C],
        track_uris=[A, A, C],
    )
    params.update(overrides)
    return ctx.service.append_archive("target", ctx.user, **params)


def test_context_reads_requested_resources_with_authorized_server_names(ctx):
    with patch(f"{MODULE}.PlaylistPairService.get_pair_for_playlist") as pair:
        body, code = ctx.service.source_context("target", ctx.user, archive=False)
    assert code == 200
    assert body == {
        "success": True,
        "target": {"id": "target", "name": "Target playlist"},
        "raid": {"id": "raid", "name": "Raid playlist"},
        "archive": None,
    }
    pair.assert_not_called()
    assert set(ctx.api.reads) == {"target", "raid"}
    assert ctx.api.writes == []


def test_context_captures_duplicate_archive_baseline_without_locks(ctx):
    with patch("shuffify.services.workshop_commit_service.playlist_lock") as lock:
        body, code = ctx.service.source_context("target", ctx.user)
    assert code == 200
    assert body["archive"]["baseline_track_uris"] == [C, C]
    lock.assert_not_called()


@pytest.mark.parametrize("resource", ["target", "raid", "archive"])
def test_context_rejects_uneditable_resources(ctx, resource):
    ctx.api.owners[resource] = "other"
    body, code = ctx.service.source_context("target", ctx.user)
    assert code == 409
    assert body["error_code"] == "playlist_access_denied"
    assert ctx.api.writes == []


def test_context_reports_database_read_provenance(ctx):
    with patch(
        f"{MODULE}.RaidLinkService.get_link_for_playlist",
        side_effect=RuntimeError("secret"),
    ):
        body, code = ctx.service.source_context("target", ctx.user)
    assert code == 502
    assert body["error_code"] == "database_read_failed"
    assert "secret" not in str(body)


def test_raid_removes_all_collection_occurrences_before_db_transition(ctx):
    def write(action, playlist_id, uris):
        assert ctx.record.status == PendingRaidStatus.PENDING
        assert playlist_id == "raid"  # Never discovery provenance or target.
        ctx.api.apply(action, playlist_id, uris)

    ctx.api.write_hook = write
    result = raid(ctx)
    assert result.status == "complete"
    assert result.to_dict()["promoted_count"] == 1
    assert ctx.api.playlists["raid"] == [C]
    assert ctx.record.status == PendingRaidStatus.PROMOTED
    assert ctx.api.writes == [("remove", "raid", [A])]


def test_raid_retry_accepts_promoted_records_and_skips_absent_removal(ctx):
    assert raid(ctx).status == "complete"
    verified = raid(ctx)
    assert verified.status == "not_required"
    assert verified.affected_count == 0
    assert verified.to_dict()["promoted_count"] == 1
    assert len(ctx.api.writes) == 1


def test_raid_no_link_transitions_db_only(ctx):
    RaidLinkService.delete_link(ctx.user.id, "target")
    result = raid(ctx, source_playlist_id=None)
    assert result.status == "complete"
    assert result.to_dict()["source_status"] == "not_required"
    assert ctx.record.status == PendingRaidStatus.PROMOTED
    assert ctx.api.writes == []


@pytest.mark.parametrize("invalid", ["unowned", "dismissed", "wrong_target", "missing"])
def test_raid_rejects_entire_invalid_captured_selection(ctx, invalid):
    if invalid == "unowned":
        other = UserService.upsert_from_spotify({"id": "bob", "images": []}).user
        ctx.record.user_id = other.id
    elif invalid == "dismissed":
        ctx.record.status = PendingRaidStatus.DISMISSED
    elif invalid == "wrong_target":
        ctx.record.target_playlist_id = "other"
    db.session.commit()
    ids = [ctx.record.id, 999999] if invalid == "missing" else [ctx.record.id]
    result = raid(ctx, track_ids=ids)
    assert result.http_status == 409
    assert result.error_code == "invalid_track_selection"
    assert ctx.api.writes == []


@pytest.mark.parametrize("finalize", [raid, restore, append])
def test_relinked_source_never_follows_new_link(ctx, finalize):
    if finalize is raid:
        RaidLinkService.update_link(ctx.user.id, "target", raid_playlist_id="new")
    else:
        PlaylistPairService.update_pair(
            ctx.user.id, "target", archive_playlist_id="new"
        )
    result = finalize(ctx)
    assert result.http_status == 409
    assert result.error_code == "source_changed"
    assert not result.retryable
    assert ctx.api.writes == []
    assert "new" not in ctx.api.reads


@pytest.mark.parametrize("finalize", [raid, restore, append])
@pytest.mark.parametrize("actual", [[B, A], [A, A, B], [A]])
def test_target_must_match_exact_captured_order_and_duplicates(ctx, finalize, actual):
    ctx.api.playlists["target"] = actual
    result = finalize(ctx)
    assert result.http_status == 409
    assert result.error_code == "target_changed"
    assert ctx.api.writes == []


@pytest.mark.parametrize("finalize", [raid, restore])
def test_removal_requires_target_membership_even_when_order_matches(ctx, finalize):
    ctx.api.playlists["target"] = [B]
    result = finalize(ctx, target_track_uris=[B])
    assert result.error_code == "target_tracks_missing"
    assert result.http_status == 409
    assert ctx.api.writes == []


@pytest.mark.parametrize("finalize", [raid, restore, append])
def test_prewrite_read_failure_is_safe_to_retry(ctx, finalize):
    def fail(_):
        raise SpotifyAPIError("secret upstream body")

    ctx.api.read_hook = fail
    result = finalize(ctx)
    assert result.status == "failed"
    assert result.http_status == 502
    assert result.retryable
    assert result.error_code == "upstream_read_failed"
    assert "secret" not in str(result.to_dict())
    assert ctx.api.writes == []


def test_successful_delete_ack_without_removal_does_not_promote(ctx):
    ctx.api.write_hook = lambda *_: True
    result = raid(ctx)
    assert result.status == "unknown"
    assert not result.retryable
    assert ctx.record.status == PendingRaidStatus.PENDING


def test_source_verification_failure_never_promotes(ctx):
    def write(action, playlist_id, uris):
        ctx.api.apply(action, playlist_id, uris)
        ctx.api.read_hook = lambda _: (_ for _ in ()).throw(
            RuntimeError("verify failed")
        )

    ctx.api.write_hook = write
    result = raid(ctx)
    assert result.status == "unknown"
    assert result.error_code == "cleanup_unverified"
    assert ctx.record.status == PendingRaidStatus.PENDING


def test_db_failure_after_removal_has_safe_db_only_recovery(ctx):
    with patch("shuffify.services.pending_raid_service.safe_commit") as commit:

        def fail(_):
            db.session.rollback()
            raise RuntimeError("secret database detail")

        commit.side_effect = fail
        result = raid(ctx)
    assert result.status == "unknown"
    assert result.retryable
    assert result.to_dict()["source_status"] == "complete"
    assert result.to_dict()["database_status"] == "failed"
    assert result.to_dict()["aggregate_status"] == "partial"
    assert result.error_code == "database_update_failed"
    assert ctx.api.playlists["raid"] == [C]
    assert ctx.record.status == PendingRaidStatus.PENDING
    inspected = raid(ctx, inspect_only=True)
    assert inspected.to_dict()["database_status"] == "pending"
    assert ctx.record.status == PendingRaidStatus.PENDING
    assert raid(ctx).status == "complete"
    assert ctx.record.status == PendingRaidStatus.PROMOTED
    assert len(ctx.api.writes) == 1


def test_raid_inspect_never_removes_or_transitions(ctx):
    result = raid(ctx, inspect_only=True)
    assert result.status == "unknown"
    assert not result.retryable
    assert ctx.api.writes == []
    assert ctx.record.status == PendingRaidStatus.PENDING


def test_restore_removes_all_occurrences_and_replay_verifies_absence(ctx):
    ctx.api.playlists["archive"] = [A, C, A]
    first = restore(ctx)
    assert first.status == "complete"
    assert first.affected_count == 2
    assert first.to_dict()["restored_count"] == 1
    verified = restore(ctx)
    assert verified.status == "not_required"
    assert verified.affected_count == 0
    assert verified.to_dict()["restored_count"] == 1
    assert len(ctx.api.writes) == 1
    assert ctx.api.playlists["archive"] == [C]


def test_restore_inspect_only_checks_absence(ctx):
    ctx.api.playlists["archive"] = [A, C]
    assert restore(ctx, inspect_only=True).status == "unknown"
    assert ctx.api.writes == []
    ctx.api.playlists["archive"] = [C]
    assert restore(ctx, inspect_only=True).status == "not_required"
    assert ctx.api.writes == []


def test_append_preserves_order_and_all_repeated_occurrences(ctx):
    result = append(ctx)
    assert result.status == "complete"
    assert result.affected_count == 3
    assert ctx.api.playlists["archive"] == [C, C, A, A, C]
    verified = append(ctx)
    assert verified.status == "not_required"
    assert verified.affected_count == 0
    assert verified.to_dict()["archived_count"] == 3
    assert len(ctx.api.writes) == 1


def test_append_requires_full_baseline_match(ctx):
    ctx.api.playlists["archive"] = [C]
    result = append(ctx)
    assert result.http_status == 409
    assert result.error_code == "source_changed"
    assert ctx.api.writes == []


def test_lost_append_ack_is_unknown_then_inspection_verifies_without_replay(ctx):
    def write(action, playlist_id, uris):
        ctx.api.apply(action, playlist_id, uris)
        raise SpotifyWriteUnverifiedError("lost response")

    ctx.api.write_hook = write
    result = append(ctx)
    assert result.status == "unknown"
    assert result.affected_count is None
    assert not result.retryable
    assert append(ctx, inspect_only=True).status == "not_required"
    assert len(ctx.api.writes) == 1


@pytest.mark.parametrize("actual", [[C, C], [C, C, A], [C, C, A, A, C, C]])
def test_append_inspect_uncertain_or_external_duplicates_never_replays(ctx, actual):
    ctx.api.playlists["archive"] = actual
    result = append(ctx, inspect_only=True)
    assert result.status == "unknown"
    assert not result.retryable
    assert ctx.api.writes == []


@pytest.mark.parametrize("completed", [0, 1])
def test_append_batch_failure_uses_dispatch_provenance(ctx, completed):
    def write(*_):
        raise SpotifyPartialBatchError(
            "archive",
            "add",
            completed,
            2,
            [A] if completed else [],
            [A, C],
            SpotifyWriteRejectedError("rejected"),
        )

    ctx.api.write_hook = write
    result = append(ctx)
    assert result.status == ("unknown" if completed else "failed")
    assert result.retryable is (completed == 0)


def test_append_2xx_with_wrong_order_is_unknown(ctx):
    def write(action, playlist_id, uris):
        ctx.api.apply(action, playlist_id, list(reversed(uris)))

    ctx.api.write_hook = write
    result = append(ctx)
    assert result.status == "unknown"
    assert not result.retryable


@pytest.mark.parametrize("finalize", [raid, restore, append])
def test_failed_inspection_never_authorizes_replay_of_prior_unknown_effect(
    ctx, finalize
):
    def fail(_):
        raise SpotifyAPIError("inspection unavailable")

    ctx.api.read_hook = fail
    result = finalize(ctx, inspect_only=True)
    assert result.http_status == 502
    assert result.status == "unknown"
    assert result.error_code == "upstream_read_failed"
    assert not result.retryable
    assert ctx.api.writes == []


@pytest.mark.parametrize("finalize", [raid, restore, append])
def test_finalizers_authorize_source_before_writing(ctx, finalize):
    ctx.api.owners["raid" if finalize is raid else "archive"] = "someoneelse"
    result = finalize(ctx)
    assert result.http_status == 409
    assert result.error_code == "playlist_access_denied"
    assert ctx.api.writes == []


def test_complete_reader_checks_every_source_page_before_removal(ctx):
    ctx.api.playlists["archive"] = [C] * 60 + [A] * 45
    result = restore(ctx)
    assert result.status == "complete"
    assert result.affected_count == 45
    assert ctx.api.playlists["archive"] == [C] * 60


@pytest.mark.parametrize("endpoint", ["finalize"])
def test_real_finalizer_route_handles_201_captured_records_without_target_rewrite(
    ctx, db_app, sample_token, endpoint
):
    uris = [f"spotify:track:{index:022d}" for index in range(200)] + [A]
    PendingRaidService.stage_tracks(
        ctx.user.id, "target", [{"uri": uri, "name": "Captured track"} for uri in uris]
    )
    records = PendingRaidService.list_pending(ctx.user.id, "target")
    assert len(records) == 201
    ctx.api.playlists["target"] = list(uris)
    ctx.api.playlists["raid"] = list(uris)
    with (
        patch("shuffify.routes.require_auth", return_value=ctx.api),
        patch("shuffify.routes.get_db_user", return_value=ctx.user),
        db_app.test_client() as client,
    ):
        with client.session_transaction() as session:
            session.update(spotify_token=sample_token, user_data={"id": ctx.user.spotify_id})
        response = post_and_run(client, ctx.api,
            api_path(f"/playlist/target/pending-raids/{endpoint}"),
            json={
                "source_playlist_id": "raid",
                "target_track_uris": uris,
                "track_ids": [record.id for record in records],
            },
        )
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert response.get_json()["promoted_count"] == 201
    db.session.expire_all()
    assert all(record.status == PendingRaidStatus.PROMOTED for record in records)
    assert ctx.api.playlists["target"] == uris
    assert ctx.api.playlists["raid"] == []
    assert len(ctx.api.writes) == 3
    assert ctx.api.writes[0][:2] == ("remove", "raid")


@pytest.mark.parametrize(
    "http_status,retryable", [(400, False), (401, True), (403, True), (503, True)]
)
@pytest.mark.parametrize("inspect_only", [False, True])
def test_request_rejection_preserves_known_prewrite_or_unknown_inspection(
    http_status, retryable, inspect_only
):
    body = {"source_playlist_id": "archive", "inspect_only": inspect_only}
    result = WorkshopFinalizeResult.request_rejected(
        body, "Rejected", http_status, "archived_count"
    )
    assert result.status == ("unknown" if inspect_only else "failed")
    assert result.retryable is (retryable and not inspect_only)
    assert result.affected_count == (None if inspect_only else 0)
    assert result.to_dict()["archived_count"] == (None if inspect_only else 0)


@pytest.mark.parametrize("finalize", [raid, restore, append])
@pytest.mark.parametrize(
    "scenario,http_status,error_code",
    [
        ("drift", 409, "target_changed"),
        ("relink", 409, "source_changed"),
        ("auth", 401, "authentication_required"),
    ],
)
def test_rejected_inspection_scope_never_claims_no_prior_effect(
    ctx,
    finalize,
    scenario,
    http_status,
    error_code,
):
    if scenario == "drift":
        ctx.api.playlists["target"] = [B, A]
    elif scenario == "relink":
        if finalize is raid:
            RaidLinkService.update_link(ctx.user.id, "target", raid_playlist_id="new")
        else:
            PlaylistPairService.update_pair(
                ctx.user.id, "target", archive_playlist_id="new"
            )
    else:

        def auth_failed(_):
            raise SpotifyAuthError("expired test authentication")

        ctx.api.read_hook = auth_failed
    result = finalize(ctx, inspect_only=True)
    assert result.http_status == http_status
    assert result.status == "unknown"
    assert result.error_code == error_code
    assert not result.retryable
    assert result.affected_count is None
    assert ctx.api.writes == []
    assert ctx.record.status == PendingRaidStatus.PENDING


@pytest.mark.parametrize(
    "path,count_key",
    [
        ("pending-raids/finalize", "promoted_count"),
        ("pair/archive", "archived_count"),
        ("pair/finalize-restore", "restored_count"),
    ],
)
@pytest.mark.parametrize(
    "gate,http_status", [("schema", 400), ("auth", 401), ("database", 503)]
)
@pytest.mark.parametrize("inspect_only", [False, True])
def test_real_route_rejection_respects_inspection_and_immutable_payload(
    ctx,
    db_app,
    path,
    count_key,
    gate,
    http_status,
    inspect_only,
):
    with (
        patch(
            "shuffify.routes.require_auth",
            return_value=None if gate == "auth" else ctx.api,
        ),
        patch("shuffify.is_db_available", return_value=gate != "database"),
        patch("shuffify.routes.is_authenticated", return_value=gate != "auth"),
        patch("shuffify.routes.get_db_user", return_value=ctx.user),
        db_app.test_client() as client,
    ):
        response = client.post(
            api_path(f"/playlist/target/{path}"),
            json={
                "source_playlist_id": (
                    "raid" if path.startswith("pending") else "archive"
                ),
                "inspect_only": inspect_only,
            },
        )
    assert response.status_code == http_status
    assert response.json["status"] == ("unknown" if inspect_only else "failed")
    assert response.json["retryable"] is (not inspect_only and gate != "schema")
    assert response.json[count_key] == (None if inspect_only else 0)
    assert ctx.api.writes == []
    assert ctx.record.status == PendingRaidStatus.PENDING


@pytest.mark.parametrize(
    "kind,path,count_key",
    [
        ("raid", "pending-raids/finalize", "promoted_count"),
        ("restore", "pair/finalize-restore", "restored_count"),
        ("append", "pair/archive", "archived_count"),
    ],
)
def test_verified_noop_retry_does_not_duplicate_actual_activity(
    ctx, db_app, sample_token, kind, path, count_key
):
    payload = {
        "source_playlist_id": "raid" if kind == "raid" else "archive",
        "target_track_uris": [A, B],
    }
    if kind == "raid":
        payload["track_ids"] = [ctx.record.id]
    else:
        payload["track_uris"] = [A]
    if kind == "restore":
        ctx.api.playlists["archive"] = [A, C, A]
    if kind == "append":
        payload["baseline_track_uris"] = [C, C]
    activities = ActivityLog.query.filter_by(user_id=ctx.user.id, playlist_id="target")
    before = activities.count()
    with (
        patch("shuffify.routes.require_auth", return_value=ctx.api),
        patch("shuffify.routes.get_db_user", return_value=ctx.user),
        db_app.test_client() as client,
    ):
        with client.session_transaction() as session:
            session.update(spotify_token=sample_token, user_data={"id": ctx.user.spotify_id})
        initial = post_and_run(client, ctx.api, api_path(f"/playlist/target/{path}"), json=payload)
        assert initial.status_code == 200
        assert initial.get_json()["status"] == "complete"
        assert initial.get_json()["affected_count"] > 0
        assert activities.count() == before + 1
        replay = post_and_run(client, ctx.api, api_path(f"/playlist/target/{path}"), json=payload)
    assert replay.status_code == 200
    assert replay.get_json()["success"] is True
    assert replay.get_json()["status"] == "not_required"
    assert replay.get_json()["affected_count"] == 0
    assert replay.get_json()[count_key] == 1
    assert activities.count() == before + 1
    assert (
        str(initial.get_json()["affected_count"])
        in activities.order_by(ActivityLog.id.desc()).first().description
    )
    assert len(ctx.api.writes) == 1
    assert ctx.api.playlists["target"] == [A, B]


def test_already_absent_raid_source_db_completion_still_logs_once(ctx, db_app, sample_token):
    ctx.api.playlists["raid"] = [C]
    payload = {
        "source_playlist_id": "raid",
        "target_track_uris": [A, B],
        "track_ids": [ctx.record.id],
    }
    activities = ActivityLog.query.filter_by(user_id=ctx.user.id, playlist_id="target")
    before = activities.count()
    with (
        patch("shuffify.routes.require_auth", return_value=ctx.api),
        patch("shuffify.routes.get_db_user", return_value=ctx.user),
        db_app.test_client() as client,
    ):
        with client.session_transaction() as session:
            session.update(spotify_token=sample_token, user_data={"id": ctx.user.spotify_id})
        completed = post_and_run(client, ctx.api, api_path("/playlist/target/pending-raids/finalize"), json=payload)
        assert completed.get_json()["status"] == "complete"
        assert completed.get_json()["affected_count"] == 1
        db.session.refresh(ctx.record)
        assert ctx.record.status == PendingRaidStatus.PROMOTED
        assert activities.count() == before + 1
        replay = post_and_run(client, ctx.api, api_path("/playlist/target/pending-raids/finalize"), json=payload)
    assert replay.get_json()["status"] == "not_required"
    assert replay.get_json()["affected_count"] == 0
    assert activities.count() == before + 1
    assert ctx.api.writes == []


def test_raid_affected_count_excludes_already_completed_selected_rows(ctx):
    PendingRaidService.complete_finalize([ctx.record])
    PendingRaidService.stage_tracks(
        ctx.user.id, "target", [{"uri": B, "name": "Pending"}]
    )
    pending = PendingRaidService.list_pending(ctx.user.id, "target")[0]
    ctx.api.playlists["raid"] = []
    result = raid(ctx, track_ids=[ctx.record.id, pending.id])
    assert result.status == "complete"
    assert result.affected_count == 1
    assert result.to_dict()["promoted_count"] == 2
    assert ctx.api.writes == []
