"""Workshop and routine reviews and their reviewed runs, over HTTP.

Real executors run against a complete local Spotify fake; every point where a
run compares its review with the current state is exercised where it happens.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from shuffify.enums import JobType, SnapshotType
from shuffify.models.db import (
    ActivityLog,
    JobExecution,
    PendingRaidTrack,
    PlaylistPair,
    PlaylistSnapshot,
    RaidPlaylistLink,
    Schedule,
    TrackLock,
    UpstreamSource,
    User,
    db,
)
from shuffify.services.executors import JobExecutorService
from shuffify.services.live_review_scope import ReviewedInputChanged
from shuffify.services.operations.commands.registry import COMMAND_HANDLERS
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.playlist_snapshot_service import PlaylistSnapshotService
from shuffify.services.scheduled_admission import admit_scheduled, build_cadence
from shuffify.services.source_resolver import SourceResolver
from shuffify.services.source_resolver.direct_api_pathway import DirectAPIPathway
from shuffify.services.upstream_source_service import UpstreamSourceService
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.spotify.exceptions import SpotifyAPIError, SpotifyNotFoundError
from shuffify.worker import OperationWorker
from tests.paths import api_path
from tests.queued_operations import post_and_run, run_operation, scoped_history, worker_api
from tests.routes.live_review_helpers import binding_bundle, history, post_context, seed_history, serving
from tests.routes.test_live_raid_outcomes import MutableSpotify, uri

DIGEST = "a" * 64
MUTATIONS = {"raid": "raid-now", "drip": "drip-now", "raid_and_drip": "raid-and-drip"}
SAVED_HISTORY = {"states": [[uri(9)], [uri(1)]], "current_index": 1}


class Spotify(MutableSpotify):
    """Records metadata and track reads; any playlist can fail or belong to someone else."""

    def __init__(self):
        super().__init__()
        self.playlists.update(archive=[uri(5)], second=[], fresh=[uri(7)], p1=[uri(1)])
        self.metadata_reads, self.track_reads = [], []
        self.failing, self.foreign = {}, set()

    def get_playlist(self, playlist_id, **kwargs):
        self.metadata_reads.append(playlist_id)
        if playlist_id in self.failing:
            raise self.failing[playlist_id]
        metadata = super().get_playlist(playlist_id, **kwargs)
        if playlist_id in self.foreign:
            metadata["owner"] = {"id": "someone-else"}
        return metadata

    def get_playlist_tracks(self, playlist_id, **kwargs):
        self.track_reads.append(playlist_id)
        return super().get_playlist_tracks(playlist_id, **kwargs)


@pytest.fixture
def workshop(raid_target):
    """The shared raid target, paired with an 'archive', with stored executor authorization."""
    user = raid_target.user
    user.encrypted_refresh_token = "stored-refresh-token"
    pair = PlaylistPair(
        user_id=user.id, production_playlist_id="target", production_playlist_name="Saved target",
        archive_playlist_id="archive", archive_playlist_name="Saved archive",
    )
    db.session.add(pair)
    db.session.commit()
    api = Spotify()
    with serving(api) as auto_snapshot, patch("shuffify.scheduler.add_job_for_schedule") as register:
        yield SimpleNamespace(
            client=raid_target.client, api=api, user=user, link=raid_target.link, pair=pair,
            source=raid_target.source, register=register, auto_snapshot=auto_snapshot,
        )


def routine(user, job_type, **fields):
    row = Schedule(**{
        "user_id": user.id, "job_type": job_type, "target_playlist_id": "target",
        "target_playlist_name": "Saved target", "schedule_type": "interval", "schedule_value": "daily",
        "is_enabled": False, "source_playlist_ids": ["persisted"], "algorithm_name": "BasicShuffle",
        "algorithm_params": {}, **fields,
    })
    db.session.add(row)
    db.session.commit()
    return row


def routine_review(client, schedule_id):
    response = client.get(api_path(f"/schedules/{schedule_id}/run-context"))
    assert response.headers["Cache-Control"] == "no-store"
    return response


def scope(response):
    assert response.status_code == 200, response.get_json()
    return response.get_json()["context"]["review_binding"]["scope"]


def state(row):
    row = db.session.get(Schedule, row.id, populate_existing=True)
    return row.last_run_at, row.last_status, row.last_error, row.updated_at, row.is_enabled


def assert_nothing_ran(api, *, rows=0):
    assert api.writes == []
    assert JobExecution.query.count() == rows
    assert PendingRaidTrack.query.count() == 0
    assert ActivityLog.query.count() == 0


def target(role, access, mutation_type=None, resource="target", name="Fresh target"):
    return {
        "resource": resource, "resource_name": name, "role": role,
        "domain": "inbox" if role == "inbox" else "spotify",
        "access": access, "mutation_type": mutation_type, "field": None,
    }


TARGET_READ = target("target", "read")
TARGET_ADDS = target("target", "write", "track_membership")
TARGET_ORDER = target("target", "write", "track_order")
COLLECTION = target("collection", "write", "track_membership", "collection", "Fresh collection")
ARCHIVE_READ = target("archive", "read", None, "archive", "Fresh archive")
ARCHIVE_WRITE = target("archive", "write", "track_membership", "archive", "Fresh archive")
INBOX = target("inbox", "write", "inbox_state")


def unstarted_target(step, name="Fresh target"):
    """A never-started step's entry for the target it would write: nothing changed, and it did not run."""
    return {
        "resource": "target", "resource_name": name, "step": step,
        "mutation_type": {"drip": "track_membership", "shuffle": "track_order", "rotate": "track_membership"}[step],
        "status": "not_required", "dispatched": False, "affected_count": 0, "retryable": False,
        "error_code": None, "verification": None, "started": False,
    }


# --- Workshop action reviews -------------------------------------------------


def test_collect_review_names_every_resource_without_reading_tracks(workshop):
    response = post_context(workshop.client, "target", {"kind": "raid", "source_playlist_ids": ["extra", "extra"]})
    assert response.status_code == 200
    body = response.get_json()
    assert body.keys() == {"success", "context"} and body["success"] is True
    context = body["context"]
    binding = context.pop("review_binding")
    assert binding["version"] == 1 and binding["before_order"] is None and binding["history"] is None
    assert len(binding["scope"]) == 64
    assert context == {
        "version": 1, "kind": "raid", "label": "Collect for review",
        "target": {"id": "target", "name": "Fresh target"}, "schedule": None, "snapshot": None,
        "resources": [TARGET_READ, COLLECTION, ARCHIVE_READ, INBOX],
        "steps": ["raid"], "required_bookkeeping": ["pending_stage", "execution_record"],
        "current": {
            "source_count": 1, "extra_source_ids": ["extra", "extra"],
            "collection": {"id": "collection", "name": "Fresh collection"},
        },
        "action": {
            "sources": [{"id": workshop.source.id, "label": "Persisted source"}],
            "extras": ["extra", "extra"],
        },
    }
    # Fresh metadata only: no discovery resolution, complete reads, writes or records.
    assert sorted(workshop.api.metadata_reads) == ["archive", "collection", "target"]
    assert workshop.api.track_reads == []
    assert_nothing_ran(workshop.api)
    workshop.register.assert_not_called()


def test_unlinked_collect_is_inbox_only(workshop):
    db.session.delete(workshop.link)
    db.session.commit()
    context = post_context(workshop.client, "target", {"kind": "raid"}).get_json()["context"]
    assert context["resources"] == [TARGET_READ, ARCHIVE_READ, INBOX]
    assert context["current"]["collection"] is None


@pytest.mark.parametrize("kind,steps,resources,bookkeeping,current,action", [
    ("drip", ["drip"], [TARGET_ADDS, COLLECTION, INBOX], ["pending_promotion", "execution_record"],
     {"collection": {"id": "collection", "name": "Fresh collection"}, "drip_enabled": False},
     {"count": 1}),
    ("raid_and_drip", ["raid", "drip"], [TARGET_ADDS, COLLECTION, ARCHIVE_READ, INBOX],
     ["pending_stage", "pending_promotion", "execution_record"],
     {"source_count": 1, "extra_source_ids": [],
      "collection": {"id": "collection", "name": "Fresh collection"}, "drip_enabled": False},
     {"sources": "persisted", "extras": [], "count": 1}),
])
def test_drip_reviews_declare_target_and_collection_writes(workshop, kind, steps, resources, bookkeeping,
                                                           current, action):
    context = post_context(workshop.client, "target", {"kind": kind}).get_json()["context"]
    if "sources" in action:
        action["sources"] = [{"id": workshop.source.id, "label": "Persisted source"}]
    assert (context["label"], context["steps"], context["resources"]) == (
        {"drip": "Add collected tracks now", "raid_and_drip": "Collect and add tracks now"}[kind], steps, resources,
    )
    assert context["required_bookkeeping"] == bookkeeping
    assert (context["current"], context["action"]) == (current, action)
    assert workshop.api.track_reads == []
    assert_nothing_ran(workshop.api)


@pytest.mark.parametrize("body", [
    {"kind": "raid", "source_playlist_ids": None},
    {"kind": "raid", "source_playlist_ids": []},
    {"kind": "raid", "source_playlist_ids": [""]},
    {"kind": "raid", "source_playlist_ids": ["  "]},
    {"kind": "raid", "source_playlist_ids": [7]},
    {"kind": "raid", "source_playlist_ids": "extra"},
    {"kind": "raid_and_drip", "source_playlist_ids": None},
    {"kind": "drip", "source_playlist_ids": ["extra"]},
    {"kind": "raid", "reviewed_scope": DIGEST},
    {"kind": "drip", "count": 3},
])
def test_executor_review_requests_are_exact(workshop, body):
    response = post_context(workshop.client, "target", body)
    assert response.status_code == 400
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"], operation["retryable"]) == (
        body["kind"], "invalid_request", False
    )
    assert workshop.api.metadata_reads == []


# --- Routine and snapshot reviews -------------------------------------------


@pytest.mark.parametrize("job_type,params,resources,steps,bookkeeping,action", [
    (JobType.SHUFFLE, {}, [TARGET_ORDER], ["shuffle"], ["execution_record"],
     {"algorithm": "BasicShuffle", "params": {}, "rotation": None, "drip": None}),
    (JobType.RAID, {}, [TARGET_READ, COLLECTION, ARCHIVE_READ, INBOX], ["raid"],
     ["pending_stage", "execution_record"], {"algorithm": None, "params": {}, "rotation": None, "drip": None}),
    (JobType.RAID_AND_SHUFFLE, {"keep_first": 1}, [TARGET_ORDER, COLLECTION, ARCHIVE_READ, INBOX],
     ["raid", "shuffle"], ["pending_stage", "execution_record"],
     {"algorithm": "BasicShuffle", "params": {"keep_first": 1}, "rotation": None, "drip": None}),
    (JobType.RAID_AND_DRIP, {"drip_count": 4}, [TARGET_ADDS, COLLECTION, ARCHIVE_READ, INBOX], ["raid", "drip"],
     ["pending_stage", "pending_promotion", "execution_record"],
     {"algorithm": None, "params": {}, "rotation": None, "drip": {"count": 4, "gate": "closed"}}),
    (JobType.DRIP, {}, [TARGET_ADDS, COLLECTION, INBOX], ["drip"], ["pending_promotion", "execution_record"],
     {"algorithm": None, "params": {}, "rotation": None, "drip": {"count": 1, "gate": "closed"}}),
    (JobType.ROTATE, {"target_size": "3", "rotation_count": 2}, [TARGET_ADDS, ARCHIVE_WRITE], ["rotate"],
     ["execution_record"],
     {"algorithm": None, "params": {}, "rotation": {"mode": "swap", "count": 2, "cap": 3, "protect": 0},
      "drip": None}),
])
def test_routine_review_follows_the_saved_job_type(workshop, job_type, params, resources, steps, bookkeeping,
                                                   action):
    row = routine(workshop.user, job_type, algorithm_params=params)
    before = state(row)
    response = routine_review(workshop.client, row.id)
    assert response.status_code == 200, response.get_json()
    context = response.get_json()["context"]
    if action["algorithm"]:
        action["algorithm"] = {"id": "BasicShuffle", "name": ShuffleRegistry.get_algorithm("BasicShuffle")().name}
    assert context["kind"] == "schedule" and context["label"] == "Run routine now"
    assert context["schedule"] == {"id": row.id, "job_type": str(job_type)}
    assert context["snapshot"] is None and context["target"] == {"id": "target", "name": "Fresh target"}
    assert (context["resources"], context["steps"], context["required_bookkeeping"]) == (
        resources, steps, bookkeeping
    )
    assert context["action"] == {"job_type": str(job_type), **action}
    assert context["current"] == {"is_enabled": False, "source_count": 1 if "raid" in steps else None}
    assert context["review_binding"]["before_order"] is None
    # A paused routine stays paused; reviewing never registers, records or reads tracks.
    assert state(row) == before
    assert workshop.api.track_reads == []
    assert_nothing_ran(workshop.api)
    workshop.register.assert_not_called()


def test_snapshot_review_reads_the_complete_target_and_binds_its_order(workshop):
    seed_history(workshop.client, "target", SAVED_HISTORY["states"])
    snapshot = PlaylistSnapshotService.create_snapshot(
        workshop.user.id, "target", "Saved name", [uri(2), uri(1), uri(2)], SnapshotType.MANUAL,
    )
    response = workshop.client.get(api_path(f"/snapshots/{snapshot.id}/restore-context"))
    assert response.headers["Cache-Control"] == "no-store"
    assert response.status_code == 200
    context = response.get_json()["context"]
    binding = context.pop("review_binding")
    assert len(binding["scope"]) == len(binding["before_order"]) == 64 and binding["history"] is None
    loaded_at = context["current"].pop("loaded_at")
    assert isinstance(loaded_at, str) and loaded_at
    assert context == {
        "version": 1, "kind": "snapshot_restore", "label": "Restore saved snapshot now",
        "target": {"id": "target", "name": "Fresh target"}, "schedule": None,
        "snapshot": {"id": snapshot.id, "created_at": snapshot.created_at.isoformat()},
        "resources": [TARGET_ADDS], "steps": [], "required_bookkeeping": ["session_history"],
        "current": {"track_count": 1, "track_uris": [uri(1)]},
        "action": {
            "snapshot_id": snapshot.id, "desired_track_uris": [uri(2), uri(1), uri(2)],
            "summary": {"added_count": 2, "removed_count": 0, "order_changed": False},
        },
    }
    assert history(workshop.client, "target") == SAVED_HISTORY
    assert PlaylistSnapshot.query.count() == 1
    assert_nothing_ran(workshop.api)


def test_snapshot_review_uses_track_order_when_occurrences_agree(workshop):
    workshop.api.playlists["target"] = [uri(1), uri(2)]
    snapshot = PlaylistSnapshotService.create_snapshot(
        workshop.user.id, "target", "Saved", [uri(2), uri(1)], SnapshotType.MANUAL,
    )
    context = workshop.client.get(api_path(f"/snapshots/{snapshot.id}/restore-context")).get_json()["context"]
    assert context["resources"] == [TARGET_ORDER]
    assert context["action"]["summary"] == {"added_count": 0, "removed_count": 0, "order_changed": True}


# --- Blocking reviews -------------------------------------------------------


@pytest.mark.parametrize("kind", ["drip", "raid_and_drip", JobType.DRIP, JobType.RAID_AND_DRIP])
def test_drip_kinds_without_a_link_block(workshop, kind):
    db.session.delete(workshop.link)
    db.session.commit()
    if kind in MUTATIONS:
        response = post_context(workshop.client, "target", {"kind": kind})
    else:
        response = routine_review(workshop.client, routine(workshop.user, kind).id)
    assert response.status_code == 400
    operation = response.get_json()["operation"]
    assert (operation["error_code"], operation["retryable"]) == ("link_required", False)
    assert workshop.api.metadata_reads == []


@pytest.mark.parametrize("kind", ["raid", "raid_and_drip"])
def test_collect_without_sources_or_extras_blocks(workshop, kind):
    db.session.delete(workshop.source)
    db.session.commit()
    response = post_context(workshop.client, "target", {"kind": kind})
    assert response.status_code == 400
    assert response.get_json()["operation"]["error_code"] == "no_sources"
    allowed = post_context(workshop.client, "target", {"kind": kind, "source_playlist_ids": ["extra"]})
    assert allowed.status_code == 200
    assert allowed.get_json()["context"]["current"]["source_count"] == 0


@pytest.mark.parametrize("change", ["collection", "archive", "algorithm", "rotation_mode", "rotation_cap",
                                    "rotation_count", "no_pair", "drip_count", "non_finite"])
def test_invalid_configuration_blocks_without_reading_spotify(workshop, change):
    kind, row = "schedule", None
    if change == "collection":
        workshop.link.raid_playlist_id = "target"
        kind = "raid"
    elif change == "archive":
        workshop.pair.archive_playlist_id = "target"
        kind = "raid"
    elif change == "algorithm":
        row = routine(workshop.user, JobType.SHUFFLE, algorithm_name="NoSuchShuffle")
    elif change.startswith("rotation") or change == "no_pair":
        params = {"target_size": 3, **{
            "rotation_mode": {"rotation_mode": "shrink"}, "rotation_cap": {"target_size": None},
            "rotation_count": {"rotation_count": "many"},
        }.get(change, {})}
        row = routine(workshop.user, JobType.ROTATE, algorithm_params=params)
        if change == "no_pair":
            db.session.delete(workshop.pair)
    elif change == "non_finite":
        # SQLite keeps NaN (PostgreSQL JSON refuses it); a value that cannot be fingerprinted is still a
        # configuration problem, not an unavailable review.
        row = routine(workshop.user, JobType.SHUFFLE, algorithm_params={"keep_first": float("nan")})
    else:
        row = routine(workshop.user, JobType.DRIP, algorithm_params={"drip_count": "lots"})
    db.session.commit()
    if kind == "raid":
        response = post_context(workshop.client, "target", {"kind": kind})
    else:
        response = routine_review(workshop.client, row.id)
    assert response.status_code == 409
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"], operation["retryable"]) == (
        kind, "invalid_configuration", False
    )
    assert workshop.api.metadata_reads == []


@pytest.mark.parametrize("kind", ["raid", "drip", "raid_and_drip", "schedule"])
def test_missing_stored_authorization_blocks_executor_reviews(workshop, kind):
    row = routine(workshop.user, JobType.SHUFFLE)
    workshop.user.encrypted_refresh_token = None
    db.session.commit()
    response = routine_review(workshop.client, row.id) if kind == "schedule" else post_context(
        workshop.client, "target", {"kind": kind}
    )
    assert response.status_code == 409
    body = response.get_json()
    assert (body["operation"]["error_code"], body["operation"]["retryable"]) == ("authorization_unavailable", False)
    assert "Sign in again" in body["message"]
    assert workshop.api.metadata_reads == []


@pytest.mark.parametrize("kind", ["raid", "schedule"])
def test_unreadable_settings_are_retryable_never_absent(workshop, kind):
    row = routine(workshop.user, JobType.RAID)

    def unavailable(*args):
        raise OperationalError("SELECT", {}, Exception("private database detail"))

    with patch.dict("shuffify.services.live_review_scope._READERS", {"link": unavailable}):
        response = routine_review(workshop.client, row.id) if kind == "schedule" else post_context(
            workshop.client, "target", {"kind": kind}
        )
    assert response.status_code == 502
    operation = response.get_json()["operation"]
    assert (operation["error_code"], operation["retryable"]) == ("database_read_failed", True)
    assert "private" not in response.get_data(as_text=True)
    assert workshop.api.metadata_reads == []


@pytest.mark.parametrize("playlist", ["target", "collection", "archive"])
@pytest.mark.parametrize("failure,status,code,retryable", [
    (SpotifyNotFoundError("private"), 404, "playlist_not_found", False),
    ("foreign", 403, "playlist_access_denied", False),
    (SpotifyAPIError("private"), 502, "preflight_failed", True),
])
def test_unreadable_or_uneditable_playlists_block_the_review(workshop, playlist, failure, status, code, retryable):
    if failure == "foreign":
        workshop.api.foreign.add(playlist)
    else:
        workshop.api.failing[playlist] = failure
    response = post_context(workshop.client, "target", {"kind": "raid"})
    assert response.status_code == status
    body = response.get_json()
    assert (body["operation"]["kind"], body["operation"]["error_code"], body["operation"]["retryable"]) == (
        "raid", code, retryable
    )
    assert "private" not in response.get_data(as_text=True)
    assert body["operation"]["resources"] == []


def test_routine_review_owned_lookup_matches_its_run(workshop):
    other = User(spotify_id="someone-else", display_name="Other")
    db.session.add(other)
    db.session.commit()
    foreign = routine(other, JobType.SHUFFLE)
    for schedule_id in (foreign.id, 9999):
        response = routine_review(workshop.client, schedule_id)
        assert response.status_code == 404
        assert response.get_json()["operation"]["error_code"] == "schedule_not_found"
    with patch(
        "shuffify.services.live_executor_context_service.SchedulerService.get_schedule",
        side_effect=OperationalError("SELECT", {}, Exception("private")),
    ):
        response = routine_review(workshop.client, foreign.id)
    assert response.status_code == 503
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"], operation["retryable"]) == (
        "schedule", "schedule_unavailable", True
    )
    assert workshop.api.metadata_reads == []


def test_snapshot_review_owned_lookup_matches_its_restore(workshop):
    other = User(spotify_id="someone-else", display_name="Other")
    db.session.add(other)
    db.session.commit()
    foreign = PlaylistSnapshotService.create_snapshot(other.id, "target", "Other", [uri(1)], SnapshotType.MANUAL)
    empty = PlaylistSnapshotService.create_snapshot(workshop.user.id, "target", "Empty", [], SnapshotType.MANUAL)
    for snapshot_id, status, code in ((foreign.id, 404, "snapshot_not_found"), (9999, 404, "snapshot_not_found"),
                                      (empty.id, 400, "empty_snapshot")):
        response = workshop.client.get(api_path(f"/snapshots/{snapshot_id}/restore-context"))
        assert (response.status_code, response.get_json()["operation"]["error_code"]) == (status, code)
    with patch(
        "shuffify.services.live_action_context_service.PlaylistSnapshotService.get_snapshot",
        side_effect=OperationalError("SELECT", {}, Exception("private")),
    ):
        response = workshop.client.get(api_path(f"/snapshots/{foreign.id}/restore-context"))
    assert response.status_code == 503
    operation = response.get_json()["operation"]
    assert (operation["kind"], operation["error_code"], operation["retryable"]) == (
        "snapshot_restore", "snapshot_unavailable", True
    )
    assert workshop.api.metadata_reads == []


@pytest.mark.parametrize("path,kind", [(api_path("/schedules/1/run-context"), "schedule"),
                                       (api_path("/snapshots/1/restore-context"), "snapshot_restore")])
@pytest.mark.parametrize("status", [401, 503])
def test_review_preflight_matches_the_mutation_and_is_never_cached(auth_client, path, kind, status):
    with (
        patch("shuffify.routes.require_auth", return_value=None if status == 401 else Spotify()),
        patch("shuffify.is_db_available", return_value=status != 503),
    ):
        response = auth_client.get(path)
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["operation"]["kind"] == kind
    assert body["operation"]["retryable"] is True
    assert not {"execution_id", "started_at", "execution_state", "http_status"} & body.keys()


@pytest.mark.parametrize("path,kind", [(api_path("/schedules/1/run-context"), "schedule"),
                                       (api_path("/snapshots/1/restore-context"), "snapshot_restore")])
def test_root_handlers_classify_review_failures_as_no_mutation(db_app, path, kind):
    from werkzeug.exceptions import RequestEntityTooLarge

    from shuffify.error_handlers import handle_request_too_large

    with db_app.test_request_context(path):
        response, status = handle_request_too_large(RequestEntityTooLarge())
    body = response.get_json()
    assert status == 413
    assert (body["operation"]["kind"], body["operation"]["error_code"]) == (kind, "request_too_large")
    assert body["operation"]["aggregate_status"] == "partial"
    assert "Nothing was changed" in body["message"]


# --- Reviewed runs: binding shapes ------------------------------------------


def run_url(kind, row=None):
    path = f"/schedules/{row.id}/run" if kind == "schedule" else f"/playlist/target/{MUTATIONS[kind]}"
    return api_path(path)


@pytest.mark.parametrize("kind", ["raid", "drip", "raid_and_drip", "schedule"])
@pytest.mark.parametrize("send", [
    {"json": {"reviewed_scope": "A" * 64}},
    {"json": {"reviewed_scope": "a" * 63}},
    {"json": {"reviewed_scope": "a" * 65}},
    {"json": {"reviewed_scope": 7}},
    {"json": {"reviewed_scope": None}},
    {"json": {"reviewed_scope": DIGEST, "reviewed_history": DIGEST}},
    {"json": {"reviewed_before_order": DIGEST}},
    {"json": {}, "query_string": {"reviewed_scope": DIGEST}},
    {"data": {"reviewed_scope": DIGEST}},
])
def test_executor_binding_shapes_are_refused_before_any_read(workshop, kind, send):
    row = routine(workshop.user, JobType.RAID)
    response = post_and_run(workshop.client, workshop.api, run_url(kind, row), **send)
    assert response.status_code == 400
    body = response.get_json()
    assert (body["operation"]["kind"], body["operation"]["error_code"], body["operation"]["retryable"]) == (
        kind, "invalid_review_binding", False
    )
    assert (body["result"] if kind == "schedule" else body)["execution_state"] == "not_started"
    assert "Review it again" in body["message"]
    assert workshop.api.metadata_reads == []
    assert_nothing_ran(workshop.api)


@pytest.mark.parametrize("send", [
    {"json": {"reviewed_scope": DIGEST}},
    {"json": {"reviewed_before_order": DIGEST}},
    {"json": {"reviewed_scope": DIGEST, "reviewed_before_order": "A" * 64}},
    {"json": {"reviewed_scope": DIGEST, "reviewed_before_order": None}},
    {"json": {"reviewed_scope": DIGEST, "reviewed_before_order": DIGEST, "reviewed_history": DIGEST}},
    {"json": {}, "query_string": {"reviewed_scope": DIGEST, "reviewed_before_order": DIGEST}},
    {"data": {"reviewed_scope": DIGEST, "reviewed_before_order": DIGEST}},
])
def test_snapshot_binding_shapes_are_refused_before_any_read(workshop, send):
    with patch("shuffify.routes.snapshots.PlaylistSnapshotService.get_snapshot") as lookup:
        response = workshop.client.post(api_path("/snapshots/1/restore"), **send)
    assert response.status_code == 400
    body = response.get_json()
    assert (body["operation"]["kind"], body["operation"]["error_code"]) == (
        "snapshot_restore", "invalid_review_binding"
    )
    assert not {"execution_id", "started_at", "execution_state"} & body.keys()
    lookup.assert_not_called()
    assert workshop.api.metadata_reads == []


# --- Reviewed runs: matching reviews run once -------------------------------


def test_reviewed_collect_runs_once_with_only_its_reviewed_inputs(workshop):
    reviewed = scope(post_context(workshop.client, "target", {"kind": "raid", "source_playlist_ids": ["extra"]}))
    response = post_and_run(workshop.client, workshop.api,
        api_path("/playlist/target/raid-now"), json={"source_playlist_ids": ["extra"], "reviewed_scope": reviewed},
    )
    data = response.get_json()
    assert response.status_code == 200, data
    assert data["operation"]["aggregate_status"] == "complete"
    assert set(workshop.api.playlists["collection"]) == {uri(2), uri(3)}
    assert workshop.api.playlists["target"] == [uri(1)]
    assert db.session.get(JobExecution, data["execution_id"]).schedule_id is None
    assert {item["kind"]: item["status"] for item in data["operation"]["bookkeeping"]} == {
        "pending_stage": "complete", "execution_record": "complete",
    }


def test_reviewed_drip_runs_once_past_the_saved_setting(workshop):
    workshop.api.playlists["collection"] = [uri(2)]
    reviewed = scope(post_context(workshop.client, "target", {"kind": "drip"}))
    response = post_and_run(
        workshop.client, workshop.api,
        api_path("/playlist/target/drip-now"), json={"reviewed_scope": reviewed},
    )
    data = response.get_json()
    assert response.status_code == 200, data
    assert workshop.api.playlists == {**workshop.api.playlists, "target": [uri(2), uri(1)], "collection": []}
    db.session.refresh(workshop.link)
    assert workshop.link.drip_enabled is False


def test_reviewed_raid_and_drip_runs_both_children(workshop):
    reviewed = scope(post_context(workshop.client, "target", {"kind": "raid_and_drip"}))
    response = post_and_run(
        workshop.client, workshop.api,
        api_path("/playlist/target/raid-and-drip"), json={"reviewed_scope": reviewed},
    )
    data = response.get_json()
    assert response.status_code == 200, data
    assert [step["aggregate_status"] for step in data["operation"]["steps"]] == ["complete", "complete"]
    assert (workshop.api.playlists["target"], workshop.api.playlists["collection"]) == ([uri(2), uri(1)], [])
    assert JobExecution.query.count() == 1
    assert [step["execution_id"] for step in data["operation"]["steps"]] == [response.operation["id"]] * 2
    assert [step["step_index"] for step in data["operation"]["steps"]] == [0, 1]


@pytest.mark.parametrize("job_type,params", [
    (JobType.SHUFFLE, {}),
    (JobType.RAID, {}),
    (JobType.RAID_AND_SHUFFLE, {}),
    (JobType.RAID_AND_DRIP, {}),
    (JobType.DRIP, {"drip_count": 1}),
    (JobType.ROTATE, {"target_size": 2, "rotation_count": 1}),
])
def test_reviewed_routine_runs_once_and_stays_paused(workshop, job_type, params):
    workshop.link.drip_enabled = True
    workshop.api.playlists["collection"] = [uri(4)]
    row = routine(workshop.user, job_type, algorithm_params=params)
    reviewed = scope(routine_review(workshop.client, row.id))
    response = post_and_run(
        workshop.client, workshop.api,
        api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
    )
    data = response.get_json()
    assert response.status_code == 200, data
    assert data["operation"]["aggregate_status"] == "complete"
    assert JobExecution.query.filter_by(schedule_id=row.id).count() == 1
    db.session.refresh(row)
    assert row.is_enabled is False
    workshop.register.assert_not_called()


def test_names_timing_status_and_pause_are_not_review_inputs(workshop):
    row = routine(workshop.user, JobType.RAID)
    reviewed = scope(routine_review(workshop.client, row.id))
    row.target_playlist_name, row.schedule_value, row.is_enabled, row.last_status = "Renamed", "weekly", True, "failed"
    workshop.link.raid_playlist_name = workshop.pair.archive_playlist_name = "Renamed"
    workshop.source.source_name, workshop.source.last_resolve_status = "Renamed", "failed"
    db.session.commit()
    response = post_and_run(
        workshop.client, workshop.api,
        api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
    )
    assert response.status_code == 200, response.get_json()


def test_reviewed_no_op_runs_report_their_inbox_outcome(workshop):
    workshop.api.playlists["target"] = [uri(1), uri(2)]  # The only discovery is already there.
    reviewed = scope(post_context(workshop.client, "target", {"kind": "raid"}))
    collect = post_and_run(
        workshop.client, workshop.api,
        api_path("/playlist/target/raid-now"), json={"reviewed_scope": reviewed},
    )
    reviewed = scope(post_context(workshop.client, "target", {"kind": "drip"}))
    drip = post_and_run(
        workshop.client, workshop.api,
        api_path("/playlist/target/drip-now"), json={"reviewed_scope": reviewed},
    )
    for response, kind in ((collect, "pending_stage"), (drip, "pending_promotion")):
        data = response.get_json()
        assert response.status_code == 200, data
        entry = next(item for item in data["operation"]["bookkeeping"] if item["kind"] == kind)
        assert (entry["status"], entry["affected_count"]) == ("not_required", 0)
    assert workshop.api.writes == []


# --- Reviewed runs: every scope input change is refused before anything runs -


def edit(workshop, row, change):
    # The admission request closes its Flask session; worker-boundary edits use fresh rows.
    link = db.session.get(RaidPlaylistLink, workshop.link.id)
    pair = db.session.get(PlaylistPair, workshop.pair.id)
    source = db.session.get(UpstreamSource, workshop.source.id)
    row = db.session.get(Schedule, row.id) if row is not None else None
    if change == "relink":
        link.raid_playlist_id = "second"
    elif change == "unlink":
        db.session.delete(link)
    elif change == "pair":
        pair.archive_playlist_id = "fresh"
    elif change == "unpair":
        db.session.delete(pair)
    elif change == "watch":
        db.session.add(UpstreamSource(
            user_id=workshop.user.id, target_playlist_id="target", source_playlist_id="extra",
            source_type="own", raid_count=5,
        ))
    elif change == "unwatch":
        db.session.delete(source)
    elif change == "raid_count":
        source.raid_count = 9
    elif change == "query":
        source.search_query = "late night"
    elif change == "source_type":
        source.source_type = "external"
    elif change == "drip_count":
        link.drip_count = 2
    elif change == "gate":
        link.drip_enabled = not link.drip_enabled
    elif change == "algorithm":
        row.algorithm_name = "BalancedShuffle"
    elif change in ("params", "rotation"):
        row.algorithm_params = {**row.algorithm_params, "protect_count" if change == "rotation" else "keep_first": 1}
    elif change == "lock":
        db.session.add(TrackLock(
            user_id=workshop.user.id, spotify_playlist_id="target", track_uri=uri(1), position=0, lock_tier="super",
        ))
    elif change == "target":
        row.target_playlist_id = "fresh"
    elif change == "job_type":
        row.job_type = JobType.RAID_AND_SHUFFLE
    elif change == "saved_sources":
        row.source_playlist_ids = ["persisted", "extra"]
    elif change == "drip_override":
        row.algorithm_params = {"drip_count": 3}
    else:
        raise AssertionError(change)
    db.session.commit()


def assert_review_changed(response, kind, *, code="review_changed"):
    body = response.get_json()
    assert response.status_code == 200, body
    assert response.accepted.status_code == 202
    assert response.operation["state"] == "failed"
    summary = body["result"] if kind == "schedule" else body
    assert summary["execution_state"] == "not_started"
    assert summary["execution_id"] == response.operation["id"]
    operation = body["operation"]
    assert (operation["error_code"], operation["retryable"], operation["aggregate_status"]) == (
        code, False, "partial"
    )
    if code == "review_changed":
        assert body["message"] == "The reviewed action changed. Review it again."


@pytest.mark.parametrize("kind,change", [
    *[("raid", change) for change in (
        "relink", "unlink", "pair", "unpair", "watch", "unwatch", "raid_count", "query", "source_type", "extras",
    )],
    *[("drip", change) for change in ("relink", "unlink", "drip_count")],
    *[("raid_and_drip", change) for change in ("relink", "unlink", "pair", "watch", "drip_count")],
])
def test_changed_workshop_inputs_dispatch_nothing(workshop, kind, change):
    workshop.api.playlists["collection"] = [uri(4)]
    reviewed = scope(post_context(workshop.client, "target", {"kind": kind}))
    body = {"reviewed_scope": reviewed}
    if change == "extras":
        body["source_playlist_ids"] = ["extra"]
    else:
        edit(workshop, None, change)
    response = post_and_run(workshop.client, workshop.api, api_path(f"/playlist/target/{MUTATIONS[kind]}"), json=body)
    if kind == "raid_and_drip":
        assert response.status_code == 200
        assert response.operation["state"] == "failed"
        assert response.get_json()["operation"]["error_code"] == "review_changed"
        first, second = response.get_json()["operation"]["steps"]
        assert (first["error_code"], first["execution_state"], second["status"]) == (
            "review_changed", "not_started", "not_started"
        )
    else:
        assert_review_changed(response, kind)
    assert_nothing_ran(workshop.api, rows=1)


@pytest.mark.parametrize("job_type,change", [
    *[(JobType.SHUFFLE, change) for change in ("algorithm", "params", "lock", "target", "job_type")],
    *[(JobType.RAID, change) for change in (
        "relink", "unlink", "pair", "unpair", "watch", "unwatch", "raid_count", "saved_sources",
    )],
    *[(JobType.RAID_AND_DRIP, change) for change in ("relink", "drip_count", "gate", "drip_override")],
    *[(JobType.DRIP, change) for change in ("drip_count", "gate", "drip_override")],
    *[(JobType.ROTATE, change) for change in ("rotation", "lock", "pair")],
])
def test_changed_routine_inputs_record_and_dispatch_nothing(workshop, job_type, change):
    workshop.api.playlists["collection"] = [uri(4)]
    params = {"target_size": 2} if job_type == JobType.ROTATE else {}
    row = routine(workshop.user, job_type, algorithm_params=params)
    reviewed = scope(routine_review(workshop.client, row.id))
    edit(workshop, row, change)
    before = state(row)
    response = post_and_run(
        workshop.client, workshop.api,
        api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
    )
    assert_review_changed(response, "schedule")
    assert state(row) == before
    assert_nothing_ran(workshop.api, rows=1)
    workshop.register.assert_not_called()


@contextmanager
def before_step(step, change):
    """Change DB configuration just before this child's fresh owned capture."""
    from shuffify.services.operations.commands import routines

    real = routines._step_inputs

    def capture(runtime, command, name):
        if name == step:
            change()
        return real(runtime, command, name)

    with patch.object(routines, "_step_inputs", side_effect=capture):
        yield


@contextmanager
def lock_wait(change, on=1, *, legacy=False):
    """Change configuration after queue admission while the worker acquires reservations."""
    if on == 2:
        with before_step("drip", change):
            yield
        return
    if legacy:
        from shuffify.services.executors import base_executor

        real = base_executor.playlist_lock

        @contextmanager
        def waiting(*args, **kwargs):
            with real(*args, **kwargs) as acquired:
                change()
                yield acquired

        with patch.object(base_executor, "playlist_lock", waiting):
            yield
        return
    from shuffify.services.operations.repository import OperationRepository

    real = OperationRepository.target_locks

    @contextmanager
    def waiting(repository, *args, **kwargs):
        with real(repository, *args, **kwargs) as acquired:
            change()
            yield acquired

    with patch.object(OperationRepository, "target_locks", waiting):
        yield


@pytest.mark.parametrize("kind", ["raid", "schedule"])
def test_a_change_during_the_lock_wait_is_compared_inside_the_lock(workshop, kind):
    row = routine(workshop.user, JobType.RAID)
    reviewed = scope(routine_review(workshop.client, row.id) if kind == "schedule" else post_context(
        workshop.client, "target", {"kind": "raid"}
    ))
    before = state(row)
    with lock_wait(lambda: edit(workshop, row, "relink")):
        response = post_and_run(workshop.client, workshop.api, run_url(kind, row), json={"reviewed_scope": reviewed})
    assert_review_changed(response, kind, code="configuration_changed")
    assert state(row) == before
    assert_nothing_ran(workshop.api, rows=1)


@pytest.mark.parametrize("kind", ["drip", "raid_and_drip"])
def test_a_relink_by_another_process_during_the_drip_lock_wait_is_refused_in_the_lock(workshop, kind):
    workshop.api.playlists["collection"] = [uri(4)]
    reviewed = scope(post_context(workshop.client, "target", {"kind": kind}))
    link_id = workshop.link.id

    def relink_elsewhere():
        # Another process commits a relink: the row moves, but not the link this request read before the wait.
        db.session.execute(
            update(RaidPlaylistLink).where(RaidPlaylistLink.id == link_id).values(raid_playlist_id="second")
            .execution_options(synchronize_session=False)
        )
        db.session.commit()

    with lock_wait(relink_elsewhere, on=1 if kind == "drip" else 2):
        response = post_and_run(workshop.client, workshop.api,
            api_path(f"/playlist/target/{MUTATIONS[kind]}"), json={"reviewed_scope": reviewed},
        )
    if kind == "drip":
        assert_review_changed(response, "drip", code="configuration_changed")
        assert_nothing_ran(workshop.api, rows=1)
    else:
        raid, drip = response.get_json()["operation"]["steps"]
        assert raid["aggregate_status"] == "complete"
        assert (drip["error_code"], drip["execution_state"], drip["execution_id"]) == (
            "configuration_changed", "not_started", response.operation["id"]
        )
        assert JobExecution.query.count() == 1  # The parent receipt covers both children.
        assert workshop.api.writes == [("add", "collection", [uri(2)])]


def test_a_routine_deleted_during_the_lock_wait_is_a_changed_review(workshop):
    row = routine(workshop.user, JobType.SHUFFLE)
    reviewed = scope(routine_review(workshop.client, row.id))

    def delete():
        from shuffify.services.scheduler_service import SchedulerService

        SchedulerService.delete_schedule(row.id, workshop.user.id)

    with lock_wait(delete):
        response = post_and_run(
            workshop.client, workshop.api,
            api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
        )
    assert_review_changed(response, "schedule", code="configuration_changed")
    assert_nothing_ran(workshop.api, rows=1)


def test_an_unreadable_lock_comparison_is_retryable_and_runs_nothing(workshop):
    row = routine(workshop.user, JobType.SHUFFLE)
    reviewed = scope(routine_review(workshop.client, row.id))

    def unavailable(*args):
        raise OperationalError("SELECT", {}, Exception("private database detail"))

    with patch("shuffify.services.operations.commands.routines.recapture", side_effect=unavailable):
        response = post_and_run(
            workshop.client, workshop.api,
            api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
        )
    body = response.get_json()
    assert response.status_code == 200
    assert (body["operation"]["error_code"], body["operation"]["retryable"]) == ("database_read_failed", True)
    assert body["result"]["execution_state"] == "not_started"
    assert "private" not in response.get_data(as_text=True)
    assert_nothing_ran(workshop.api, rows=1)


# --- Executor re-checks after the lock comparison ---------------------------


def test_relink_during_collection_fails_the_step_before_its_write(workshop):
    reviewed = scope(post_context(workshop.client, "target", {"kind": "raid"}))
    read_source = workshop.api.get_playlist_tracks

    def relink_while_resolving(playlist_id, **kwargs):
        if playlist_id == "persisted":
            edit(workshop, None, "relink")
        return read_source(playlist_id, **kwargs)

    workshop.api.get_playlist_tracks = relink_while_resolving
    response = post_and_run(
        workshop.client, workshop.api,
        api_path("/playlist/target/raid-now"), json={"reviewed_scope": reviewed},
    )
    data = response.get_json()
    assert response.status_code == 200, data
    assert (data["operation"]["error_code"], data["operation"]["aggregate_status"]) == (
        "configuration_changed", "partial"
    )
    assert workshop.api.writes == []
    assert PendingRaidTrack.query.count() == 0
    assert db.session.get(JobExecution, data["execution_id"]).status == "failed"


def after_check(step, change):
    """Change a checked parent input immediately before this child's fresh capture."""
    return before_step(step, change)


@pytest.mark.parametrize("job_type,step,change", [
    (JobType.RAID_AND_DRIP, "drip", "relink"),
    (JobType.RAID_AND_DRIP, "drip", "drip_override"),
    (JobType.RAID_AND_SHUFFLE, "raid", "relink"),
    (JobType.RAID, "raid", "pair"),
    (JobType.ROTATE, "rotate", "pair"),
])
def test_executors_recheck_the_link_pair_and_count_they_consume(workshop, job_type, step, change):
    workshop.link.drip_enabled = True
    row = routine(workshop.user, job_type, algorithm_params={"target_size": 2} if job_type == JobType.ROTATE else {})
    reviewed = scope(routine_review(workshop.client, row.id))
    with after_check(step, lambda: edit(workshop, row, change)):
        response = post_and_run(
            workshop.client, workshop.api,
            api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
        )
    data = response.get_json()
    assert response.status_code == 200, data
    assert data["operation"]["error_code"] == "configuration_changed"
    assert db.session.get(JobExecution, data["result"]["execution_id"]).status == "failed"
    if step == "drip":
        # The verified Collect is kept; the changed Drip dispatched nothing.
        assert [entry["status"] for entry in data["operation"]["steps"]] == ["complete", "not_started"]
        assert workshop.api.writes == [("add", "collection", [uri(2)])]
        assert PendingRaidTrack.query.one().status == "pending"
    else:
        assert workshop.api.writes == []


def test_every_routine_run_refuses_a_target_changed_while_it_waited(workshop):
    workshop.api.playlists["target"] = [uri(1), uri(2)]
    row = routine(workshop.user, JobType.SHUFFLE)
    with lock_wait(lambda: edit(workshop, row, "target")):
        # an unreviewed run
        response = post_and_run(
            workshop.client, workshop.api,
            api_path(f"/schedules/{row.id}/run"),
        )
    data = response.get_json()
    assert response.status_code == 200, data
    assert data["operation"]["error_code"] == "configuration_changed"
    assert workshop.api.writes == []
    assert db.session.get(JobExecution, data["result"]["execution_id"]).status == "failed"
    # The step never started; its fact names the playlist this run locked, never the new target.
    assert data["operation"]["resources"] == [unstarted_target("shuffle", None)]


def test_a_scheduled_fire_refuses_a_routine_retargeted_after_it_was_admitted(workshop, sample_token):
    workshop.api.playlists["target"] = [uri(1), uri(2)]
    row = routine(workshop.user, JobType.SHUFFLE, is_enabled=True)
    fire = admit_scheduled(OperationRepository(), row.id, build_cadence(row).fingerprint, datetime.now(timezone.utc))
    assert fire.state == "admitted"
    worker = OperationWorker(
        workshop.client.application, handlers=COMMAND_HANDLERS, heartbeat_interval=0,
        api_factory=lambda user: worker_api(workshop.api, sample_token),
    )
    with lock_wait(lambda: edit(workshop, row, "target")):
        assert worker.run_once(fire.operation_id) is True
    operation = OperationRepository().get(fire.operation_id)
    assert (operation.kind, operation.state) == ("schedule_run", "failed")
    assert operation.payload["command"]["triggered_by"] == "scheduler"
    assert operation.journal["result"]["error_code"] == "configuration_changed"
    assert workshop.api.writes == []
    assert db.session.get(JobExecution, fire.operation_id, populate_existing=True).status == "failed"


def test_scheduled_runs_refuse_a_retargeted_routine_too(workshop):
    workshop.api.playlists["target"] = [uri(1), uri(2)]
    row = routine(workshop.user, JobType.SHUFFLE, is_enabled=True)
    with lock_wait(lambda: edit(workshop, row, "target"), legacy=True):
        JobExecutorService.execute(row.id)
    assert JobExecution.query.one().status == "failed"
    assert workshop.api.writes == []


@pytest.mark.parametrize("job_type,step", [(JobType.SHUFFLE, "shuffle"), (JobType.RAID_AND_SHUFFLE, "shuffle")])
def test_reviewed_steps_recompare_after_the_record_commit(workshop, job_type, step):
    workshop.api.playlists["target"] = [uri(1), uri(6)]
    row = routine(workshop.user, job_type)
    reviewed = scope(routine_review(workshop.client, row.id))
    with before_step(step, lambda: edit(workshop, row, "params")):
        response = post_and_run(
            workshop.client, workshop.api,
            api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
        )
    data = response.get_json()
    assert response.status_code == 200, data
    assert data["operation"]["error_code"] == "configuration_changed"
    if job_type == JobType.RAID_AND_SHUFFLE:
        assert [entry["status"] for entry in data["operation"]["steps"]] == ["complete", "not_started"]
        assert workshop.api.writes == [("add", "collection", [uri(2)])]
    else:
        assert workshop.api.writes == []


def test_a_step_refused_by_its_check_reports_its_inbox_fact_as_not_started(workshop):
    row = routine(workshop.user, JobType.RAID_AND_DRIP)
    reviewed = scope(routine_review(workshop.client, row.id))
    with before_step("drip", lambda: edit(workshop, row, "drip_count")):
        response = post_and_run(
            workshop.client, workshop.api,
            api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
        )
    data = response.get_json()
    assert response.status_code == 200, data
    assert [entry["status"] for entry in data["operation"]["steps"]] == ["complete", "not_started"]
    promotion = [entry for entry in data["operation"]["bookkeeping"] if entry["kind"] == "pending_promotion"]
    assert [(entry["step"], entry["status"]) for entry in promotion] == [("drip", "not_required")]
    assert [entry for entry in data["operation"]["resources"] if entry["step"] == "drip"] == [unstarted_target("drip")]


@pytest.mark.parametrize("reviewed", [True, False])
def test_reviewed_collect_never_raids_a_source_unwatched_during_its_lock_wait(workshop, reviewed):
    scope_digest = scope(post_context(workshop.client, "target", {"kind": "raid"}))
    late = UpstreamSourceService.add_source("user123", "target", "extra", source_type="own")
    late_id = late.id

    def unwatch():
        db.session.delete(db.session.get(UpstreamSource, late_id))
        db.session.commit()

    with lock_wait(unwatch):
        response = post_and_run(workshop.client, workshop.api,
            api_path("/playlist/target/raid-now"), json={"reviewed_scope": scope_digest} if reviewed else {},
        )
    data = response.get_json()
    assert response.status_code == 200, data
    assert response.accepted.status_code == 202
    assert response.operation["state"] == "failed"
    assert data["operation"]["error_code"] == "configuration_changed"
    assert data["execution_id"] == response.operation["id"]
    assert data["execution_state"] == "not_started"
    assert workshop.api.playlists["collection"] == []
    assert "extra" not in workshop.api.track_reads
    assert_nothing_ran(workshop.api, rows=1)


@pytest.mark.parametrize("change", ["drip_count", "relink"])
def test_raid_and_drip_child_compares_its_own_part_in_its_own_lock(workshop, change):
    reviewed = scope(post_context(workshop.client, "target", {"kind": "raid_and_drip"}))
    with before_step("drip", lambda: edit(workshop, None, change)):
        response = post_and_run(
            workshop.client, workshop.api,
            api_path("/playlist/target/raid-and-drip"), json={"reviewed_scope": reviewed},
        )
    data = response.get_json()
    assert response.status_code == 200, data
    operation = data["operation"]
    assert (operation["aggregate_status"], operation["error_code"]) == ("partial", "configuration_changed")
    raid, drip = operation["steps"]
    assert raid["aggregate_status"] == "complete"
    assert (drip["error_code"], drip["execution_state"], drip["execution_id"]) == (
        "configuration_changed", "not_started", response.operation["id"]
    )
    # The refused child never started: it promoted nothing, and the reply says why.
    promotion = [entry for entry in operation["bookkeeping"] if entry["kind"] == "pending_promotion"]
    assert [(entry["status"], entry["affected_count"]) for entry in promotion] == [("not_required", 0)]
    # Nor did it write the target, which the reply lists as unstarted.
    assert [entry for entry in operation["resources"] if entry["resource"] == "target"] == [
        unstarted_target("drip")
    ]
    assert data["message"] == "The captured executor configuration changed before this step ran."
    assert JobExecution.query.count() == 1
    assert (workshop.api.playlists["target"], workshop.api.playlists["collection"]) == ([uri(1)], [uri(2)])
    assert PendingRaidTrack.query.one().status == "pending"


# --- Steps that never ran, and inbox changes never attempted --------------


def test_a_closed_drip_gate_after_a_collect_reports_the_target_as_not_started(workshop):
    row = routine(workshop.user, JobType.RAID_AND_DRIP)  # The saved Drip setting is off.
    reviewed = scope(routine_review(workshop.client, row.id))
    response = post_and_run(
        workshop.client, workshop.api,
        api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
    )
    data = response.get_json()
    assert response.status_code == 200, data
    operation = data["operation"]
    assert operation["error_code"] == "drip_disabled"
    assert [entry["status"] for entry in operation["steps"]] == ["complete", "not_started"]
    collection, drip = operation["resources"]
    assert (collection["resource"], collection["step"], collection["status"]) == ("collection", "raid", "complete")
    assert drip == unstarted_target("drip")
    promotion = next(entry for entry in operation["bookkeeping"] if entry["kind"] == "pending_promotion")
    assert (promotion["status"], promotion["affected_count"]) == ("not_required", 0)
    assert workshop.api.writes == [("add", "collection", [uri(2)])]


def test_a_skipped_raid_and_drip_child_reports_the_target_as_not_started(workshop, monkeypatch):
    # Sources resolve through the Spotify fake only, never a public page.
    monkeypatch.setattr(
        "shuffify.services.operations.executors.raid._resolver",
        lambda *_args: (SourceResolver(pathways=[DirectAPIPathway()]), SimpleNamespace(updates=[])),
    )
    real_read = workshop.api.get_playlist_tracks

    def read(playlist_id, **kwargs):
        if playlist_id == "gone":
            raise SpotifyNotFoundError("gone")
        return real_read(playlist_id, **kwargs)

    workshop.api.get_playlist_tracks = read  # One requested source can no longer be read.
    body = {"source_playlist_ids": ["gone"]}
    reviewed = scope(post_context(workshop.client, "target", {"kind": "raid_and_drip", **body}))
    response = post_and_run(workshop.client, workshop.api,
        api_path("/playlist/target/raid-and-drip"), json={**body, "reviewed_scope": reviewed},
    )
    data = response.get_json()
    assert response.status_code == 200, data
    operation = data["operation"]
    raid, drip = operation["steps"]
    assert (raid["status"], raid["execution_state"]) == ("partial", "recorded")
    assert (drip["status"], drip["execution_state"], drip["error_code"]) == (
        "not_started", "not_started", "previous_step_incomplete"
    )
    collection, target_entry = operation["resources"]
    assert (collection["resource"], collection["status"]) == ("collection", "complete")
    # The skipped child knows no fresh name for the target; it still says it changed nothing.
    assert target_entry == unstarted_target("drip")
    promotion = next(entry for entry in operation["bookkeeping"] if entry["kind"] == "pending_promotion")
    assert (promotion["step"], promotion["status"], promotion["affected_count"]) == ("drip", "not_required", 0)
    assert workshop.api.writes == [("add", "collection", [uri(2)])]


def test_a_busy_reserved_target_defers_both_children_then_runs_the_same_operation(workshop):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone
    from threading import Event

    from sqlalchemy.orm import Session

    from shuffify.services.operations.repository import OperationRepository

    repository = OperationRepository()
    ready, release = Event(), Event()

    def holder():
        with repository.target_locks(["target"]) as acquired:
            assert acquired
            ready.set()
            assert release.wait(10)

    reviewed = scope(post_context(workshop.client, "target", {"kind": "raid_and_drip"}))
    before_reads = (list(workshop.api.metadata_reads), list(workshop.api.track_reads))
    with ThreadPoolExecutor(max_workers=1) as executor:
        held = executor.submit(holder)
        assert ready.wait(5)
        try:
            response = post_and_run(
                workshop.client, workshop.api, api_path("/playlist/target/raid-and-drip"),
                json={"reviewed_scope": reviewed},
            )
            assert response.accepted.status_code == 202
            assert response.operation["state"] == "queued"
            assert response.operation["result"] is None
            assert_nothing_ran(workshop.api, rows=1)
            assert (workshop.api.metadata_reads, workshop.api.track_reads) == before_reads
            assert workshop.api.playlists["collection"] == []
        finally:
            release.set()
        held.result(timeout=5)
    operation_id = response.operation["id"]
    # Advance the known defer deadline; no repeated HTTP trigger is needed.
    with Session(repository.engine) as storage, storage.begin():
        row = storage.get(JobExecution, operation_id)
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    completed = run_operation(workshop.client, workshop.api, operation_id, response.accepted)
    assert completed.operation["state"] == "succeeded"
    data = completed.get_json()
    assert [step["execution_id"] for step in data["operation"]["steps"]] == [operation_id] * 2
    assert [step["aggregate_status"] for step in data["operation"]["steps"]] == ["complete", "complete"]
    assert (workshop.api.playlists["target"], workshop.api.playlists["collection"]) == ([uri(2), uri(1)], [])
    assert JobExecution.query.count() == 1


@pytest.mark.parametrize("job_type,step,change,name", [
    (JobType.RAID_AND_DRIP, "drip", "relink", "Fresh target"),
    (JobType.ROTATE, "rotate", "pair", None),
])
def test_a_step_refused_by_a_reviewed_input_it_consumes_never_started(workshop, job_type, step, change, name):
    workshop.link.drip_enabled = True
    row = routine(workshop.user, job_type, algorithm_params={"target_size": 2} if job_type == JobType.ROTATE else {})
    reviewed = scope(routine_review(workshop.client, row.id))
    with after_check(step, lambda: edit(workshop, row, change)):
        response = post_and_run(
            workshop.client, workshop.api,
            api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
        )
    data = response.get_json()
    assert response.status_code == 200, data
    operation = data["operation"]
    assert operation["error_code"] == "configuration_changed"
    # As if refused by its check: only its target entry, saying it did not run; no archive or collection.
    assert [entry for entry in operation["resources"] if entry["step"] == step] == [unstarted_target(step, name)]
    inbox = [(entry["kind"], entry["status"], entry["affected_count"]) for entry in operation["bookkeeping"]
             if entry["step"] == step]
    assert inbox == ([("pending_promotion", "not_required", 0)] if step == "drip" else [])
    assert all(write[1] != "target" for write in workshop.api.writes)


def unresolved_claims(context, operation):
    """The written resources a review client still claims for this reply (claimsOf, before any check)."""
    known, claims = {"complete", "not_required", "failed"}, []
    pending = {
        entry["kind"]: entry["status"] for entry in operation["bookkeeping"] if entry["kind"].startswith("pending_")
    }
    for resource in context["resources"]:
        if resource["access"] != "write":
            continue
        if resource["domain"] == "inbox":
            required = [kind for kind in context["required_bookkeeping"] if kind.startswith("pending_")]
            if any(pending.get(kind) in (None, "unknown") for kind in required) or "unknown" in pending.values():
                claims.append("inbox:" + resource["resource"])
            continue
        statuses = [entry["status"] for entry in operation["resources"] if entry["resource"] == resource["resource"]]
        if not statuses or any(status not in known for status in statuses):
            claims.append("tracks:" + resource["resource"])
    return claims


def test_a_drip_read_failure_reports_unstarted_target_and_uncertain_collection(workshop):
    context = post_context(workshop.client, "target", {"kind": "raid_and_drip"}).get_json()["context"]

    def unreadable():
        workshop.api.failing["collection"] = SpotifyAPIError("collection unavailable")

    with before_step("drip", unreadable):
        response = post_and_run(workshop.client, workshop.api,
            api_path("/playlist/target/raid-and-drip"), json={"reviewed_scope": context["review_binding"]["scope"]},
        )
    data = response.get_json()
    assert response.status_code == 200, data
    operation = data["operation"]
    raid, drip = operation["steps"]
    assert (raid["aggregate_status"], drip["execution_state"]) == ("complete", "not_started")
    # Preparation failed before the child had an immutable plan or any write.
    assert [entry for entry in operation["resources"] if entry["resource"] == "target"] == [
        unstarted_target("drip")
    ]
    assert unresolved_claims(context, operation) == ["tracks:collection"]
    collection = next(entry for entry in operation["resources"] if entry["resource"] == "collection")
    assert collection["status"] == "unknown"
    assert "current_track_uris" not in collection
    assert workshop.api.writes == [("add", "collection", [uri(2)])]


def test_a_drip_whose_target_fails_verification_never_attempted_promotion(workshop):
    workshop.api.playlists["collection"] = [uri(2)]
    real_add = workshop.api.playlist_add_items

    def add_one_extra(playlist_id, uris, position=None):
        real_add(playlist_id, uris, position=position)
        if playlist_id == "target":
            workshop.api.playlists["target"].append(uris[0])  # The readback no longer verifies.

    workshop.api.playlist_add_items = add_one_extra
    reviewed = scope(post_context(workshop.client, "target", {"kind": "drip"}))
    response = post_and_run(
        workshop.client, workshop.api,
        api_path("/playlist/target/drip-now"), json={"reviewed_scope": reviewed},
    )
    data = response.get_json()
    assert response.status_code == 200, data
    operation = data["operation"]
    assert response.operation["state"] == "needs_attention"
    assert (data["status"], operation["error_code"]) == ("failed", "needs_attention")
    assert {(entry["resource"], entry["status"]) for entry in operation["resources"]} == {
        ("target", "unknown"), ("collection", "failed"),
    }
    # Promotion runs only after both playlists verify, so it was never attempted.
    promotion = [entry for entry in operation["bookkeeping"] if entry["kind"] == "pending_promotion"]
    assert [(entry["status"], entry["affected_count"]) for entry in promotion] == [("not_required", 0)]
    # The observed duplicate is an external mismatch, so compensation may not overwrite it.
    assert workshop.api.playlists["target"] == [uri(2), uri(1), uri(2)]
    assert workshop.api.playlists["collection"] == [uri(2)]
    assert workshop.api.writes == [("add", "target", [uri(2)])]
    assert PendingRaidTrack.query.count() == 0


@pytest.mark.parametrize("when", ["lock", "record"])
def test_a_saved_value_that_no_longer_fingerprints_is_a_changed_review(workshop, when):
    workshop.api.playlists["target"] = [uri(1), uri(6)]
    row = routine(workshop.user, JobType.SHUFFLE)
    reviewed = scope(routine_review(workshop.client, row.id))

    def corrupt():
        current = db.session.get(Schedule, row.id)
        current.algorithm_params = {"keep_first": float("nan")}  # Stored by SQLite; no canonical JSON form.
        db.session.commit()

    if when == "lock":
        corrupt()
        from contextlib import nullcontext

        after_record = nullcontext()
    else:
        after_record = before_step("shuffle", corrupt)
    with after_record:
        response = post_and_run(
            workshop.client, workshop.api,
            api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
        )
    data = response.get_json()
    assert data["operation"]["error_code"] == ("preflight_failed" if when == "lock" else "configuration_changed"), data
    assert response.status_code == (400 if when == "lock" else 200)
    assert JobExecution.query.count() == (0 if when == "lock" else 1)
    assert workshop.api.writes == []


@pytest.mark.parametrize("point,code", [("bind", "coordination_failed"), ("step", "execution_failed")])
def test_a_programming_error_in_a_comparison_is_never_a_changed_review(workshop, point, code):
    row = routine(workshop.user, JobType.SHUFFLE)
    reviewed = scope(routine_review(workshop.client, row.id))
    target = "_initial_binding" if point == "bind" else "_step_inputs"
    broken = patch(f"shuffify.services.operations.commands.routines.{target}", side_effect=ValueError("bug"))
    with broken:
        response = post_and_run(
            workshop.client, workshop.api,
            api_path(f"/schedules/{row.id}/run"), json={"reviewed_scope": reviewed},
        )
    data = response.get_json()
    assert response.status_code == 200, data
    assert data["operation"]["error_code"] == code
    assert workshop.api.writes == []


# --- Reviewed snapshot restore ----------------------------------------------


def test_reviewed_snapshot_restore_runs_against_the_reviewed_order(workshop):
    seed_history(workshop.client, "target", SAVED_HISTORY["states"])
    snapshot = PlaylistSnapshotService.create_snapshot(
        workshop.user.id, "target", "Saved", [uri(2), uri(1)], SnapshotType.MANUAL,
    )
    context = workshop.client.get(api_path(f"/snapshots/{snapshot.id}/restore-context")).get_json()["context"]
    response = post_and_run(
        workshop.client, workshop.api, api_path(f"/snapshots/{snapshot.id}/restore"), json=binding_bundle(context),
    )
    data = response.get_json()
    assert response.status_code == 200, data
    assert workshop.api.writes == [("replace", "target", [uri(2), uri(1)])]
    assert scoped_history(workshop.client, "target")["current_index"] == 2


@pytest.mark.parametrize("change,code", [("snapshot", "review_changed"), ("target", "resource_changed")])
def test_stale_snapshot_review_never_snapshots_writes_or_moves_history(workshop, change, code):
    seed_history(workshop.client, "target", SAVED_HISTORY["states"])
    reviewed, other = (
        PlaylistSnapshotService.create_snapshot(workshop.user.id, "target", "Saved", order, SnapshotType.MANUAL)
        for order in ([uri(2), uri(1)], [uri(3)])
    )
    context = workshop.client.get(api_path(f"/snapshots/{reviewed.id}/restore-context")).get_json()["context"]
    restored = reviewed
    if change == "snapshot":
        restored = other  # The reviewed bundle names a different saved snapshot.
    else:
        workshop.api.playlists["target"] = [uri(1), uri(4)]  # Changed on Spotify after the review.
    response = post_and_run(
        workshop.client, workshop.api, api_path(f"/snapshots/{restored.id}/restore"), json=binding_bundle(context),
    )
    body = response.get_json()
    assert response.status_code == 200, body
    assert response.operation["state"] == "failed"
    assert (body["operation"]["error_code"], body["operation"]["retryable"]) == (code, False)
    assert not {"execution_id", "started_at", "execution_state"} & body.keys()
    assert workshop.api.writes == []
    workshop.auto_snapshot.assert_not_called()
    assert scoped_history(workshop.client, "target") == SAVED_HISTORY
    assert ActivityLog.query.count() == 0


def test_reviewed_input_change_is_a_runtime_error_the_runner_records(workshop):
    # The executors' consumption checks raise this before dispatch; the runner records the failure.
    assert issubclass(ReviewedInputChanged, RuntimeError)
