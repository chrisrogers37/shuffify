"""One queued identity owns routine steps, accepted receipts, and terminal reporting."""

import importlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from sqlalchemy import update
from sqlalchemy.orm import Session

from shuffify.models.db import (
    ActivityLog,
    JobExecution,
    PendingRaidTrack,
    PlaylistPair,
    PlaylistSnapshot,
    RaidPlaylistLink,
    Schedule,
    UpstreamSource,
    User,
    db,
)
from shuffify.services.operations.commands.routine_capture import capture_direct, capture_schedule
from shuffify.services.operations.repository import OperationRepository
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import Crash, World, uri


def handlers():
    module = importlib.import_module("shuffify.services.operations.commands.routines")
    return {name: getattr(module, name) for name in ("schedule_run", "raid_now", "drip_now", "raid_and_drip")}


@pytest.fixture
def routine(db_app, sample_token, monkeypatch):
    from shuffify.shuffle_algorithms.basic import BasicShuffle

    monkeypatch.setattr(
        BasicShuffle, "shuffle", lambda self, tracks, **kwargs: [track["uri"] for track in reversed(tracks)]
    )
    user = User(spotify_id="owner")
    db.session.add(user)
    db.session.commit()
    rows = [
        RaidPlaylistLink(
            user_id=user.id,
            target_playlist_id="target",
            target_playlist_name="Target",
            raid_playlist_id="collection",
            raid_playlist_name="Collection",
            drip_count=2,
            drip_enabled=True,
        ),
        PlaylistPair(
            user_id=user.id,
            production_playlist_id="target",
            production_playlist_name="Target",
            archive_playlist_id="archive",
            archive_playlist_name="Archive",
        ),
        UpstreamSource(
            user_id=user.id,
            target_playlist_id="target",
            source_playlist_id="source",
            source_type="own",
            source_name="Source",
            raid_count=2,
        ),
    ]
    db.session.add_all(rows)
    db.session.commit()
    world = World([uri(1), uri(2), uri(3)])
    for resource, values in [("collection", [uri(4), uri(4)]), ("archive", [uri(6)]), ("source", [uri(5), uri(7)])]:
        world.playlists[resource] = deepcopy(world.playlists["target"])
        world.playlists[resource]["track_uris"] = values
        world.playlists[resource]["metadata"]["name"] = resource.title()
    repository = OperationRepository()
    factories, source_reads = [], []

    def factory(_user):
        factories.append(_user.id)
        api = SpotifyAPI(TokenInfo.from_dict(sample_token), auto_refresh=False)

        def metadata(resource, **kwargs):
            value = world.read(resource)
            return {
                "id": resource,
                "owner": {"id": "owner"},
                "collaborative": False,
                "items": {"total": len(value["track_uris"])},
                "snapshot_id": value["snapshot_id"],
                "description": None,
                **value["metadata"],
            }

        def tracks(resource, **kwargs):
            if resource == "source":
                source_reads.append(resource)
            return [
                {"uri": value, "name": value, "artists": [], "type": "track"}
                for value in world.read(resource)["track_uris"]
            ]

        def items(resource, limit=100, offset=0):
            values = tracks(resource)
            return {
                "offset": offset,
                "total": len(values),
                "next": None,
                "items": [
                    {"added_at": "2026-01-01T00:00:00Z", "track": value} for value in values[offset : offset + limit]
                ],
            }

        def send(method, url, **kwargs):
            value = world.dispatch(method, urlsplit(url).path.removeprefix("/v1"), kwargs["json"])
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(value).encode() if value else b""
            return response

        api.get_playlist, api.get_playlist_tracks, api.get_playlist_items_raw = metadata, tracks, items
        api.get_tracks = lambda values: [{"uri": value, "name": value, "artists": []} for value in values]
        api._http._session.request = send
        return api

    def enqueue(kind="raid", *, direct=False, enabled=True, origin="manual", params=None, reviewed=None):
        if direct:
            with Session(db.engine) as storage:
                command = capture_direct(storage, user.id, "target", kind, reviewed=reviewed)
        else:
            schedule = Schedule(
                user_id=user.id,
                target_playlist_id="target",
                target_playlist_name="Target",
                job_type=kind,
                algorithm_name="BasicShuffle",
                algorithm_params=params or {},
                source_playlist_ids=[],
                is_enabled=enabled,
                schedule_type="interval",
                schedule_value="daily",
            )
            db.session.add(schedule)
            db.session.commit()
            with Session(db.engine) as storage:
                command = capture_schedule(storage, user.id, schedule.id, triggered_by=origin, reviewed=reviewed)
        row = repository.enqueue(
            kind=command.kind,
            user_id=user.id,
            targets=command.reserved_targets,
            schedule_id=command.definition.schedule_id,
            scheduled_for=datetime.now(timezone.utc) if origin == "scheduler" else None,
            payload={"version": 1, "command": command.model_dump(mode="json")},
        )
        return row, command

    def run(row):
        worker = OperationWorker(
            db_app, repository=repository, handlers=handlers(), api_factory=factory, heartbeat_interval=0
        )
        assert worker.run_once(row.id) is True
        return repository.get(row.id)

    return SimpleNamespace(
        user=user,
        repository=repository,
        world=world,
        factory=factory,
        factories=factories,
        source_reads=source_reads,
        enqueue=enqueue,
        run=run,
        app=db_app,
    )


@pytest.mark.parametrize("kind", ["raid", "shuffle", "raid_and_shuffle", "raid_and_drip", "rotate", "drip"])
def test_all_saved_routines_use_one_identity_and_explicit_terminal_receipts(routine, kind):
    params = {"target_size": 3, "rotation_count": 1} if kind == "rotate" else {}
    row, command = routine.enqueue(kind, params=params)
    completed = routine.run(row)
    assert completed.state == "succeeded"
    result = completed.journal["result"]
    assert result["execution_id"] == row.id
    assert result["execution_state"] == "recorded"
    assert result["triggered_by"] == "manual"
    assert result["status"] == "success"
    assert result["result_detail"]["detail_status"] == "available"
    assert JobExecution.query.count() == 1
    assert len(routine.factories) == 1
    assert len(completed.journal["boundaries"]["accepted"]) == len(command.steps)
    assert all(snapshot.job_execution_id == row.id for snapshot in PlaylistSnapshot.query.all())
    assert ActivityLog.query.filter_by(activity_type="schedule_run").count() == 1


@pytest.mark.parametrize("kind", ["raid", "drip", "raid_and_drip"])
def test_explicit_actions_share_the_same_step_executor_and_recorded_identity(routine, kind):
    row, command = routine.enqueue(kind, direct=True)
    completed = routine.run(row)
    assert completed.state == "succeeded"
    assert completed.schedule_id is None
    assert completed.journal["result"]["execution_id"] == row.id
    assert len(completed.journal["boundaries"]["accepted"]) == len(command.steps)
    assert JobExecution.query.count() == 1


def test_partial_collect_is_accepted_and_stops_the_dependent_drip(routine, monkeypatch):
    from shuffify.services.source_resolver.base import ResolveResult

    monkeypatch.setattr(
        "shuffify.services.operations.executors.raid.SourceResolver.resolve",
        lambda *args, **kwargs: ResolveResult([uri(5)], "fixture", False, partial=True),
    )
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    completed = routine.run(row)
    result = completed.journal["result"]
    assert completed.state == "failed"
    assert [step["name"] for step in completed.journal["boundaries"]["accepted"]] == ["raid"]
    assert result["operation"]["steps"][1]["status"] == "not_started"
    assert result["operation"]["steps"][1]["error_code"] == "previous_step_incomplete"
    assert result["operation"]["error_code"] == "source_resolution_failed"
    assert PendingRaidTrack.query.one().track_uri == uri(5)
    assert routine.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
    assert routine.world.playlists["collection"]["track_uris"] == [uri(4), uri(4), uri(5)]


@pytest.mark.parametrize("origin,enabled,expected", [("manual", False, "succeeded"), ("scheduler", False, "failed")])
def test_only_scheduled_origin_requires_an_enabled_schedule(routine, origin, enabled, expected):
    row, _ = routine.enqueue("raid", origin=origin, enabled=enabled)
    completed = routine.run(row)
    assert completed.state == expected
    if origin == "scheduler":
        assert routine.factories == []
        db.session.expire_all()
        assert db.session.get(Schedule, row.schedule_id).last_run_at is None


def test_saved_drip_closed_gate_is_recorded_skipped_and_does_not_log_mutation(routine):
    RaidPlaylistLink.query.one().drip_enabled = False
    db.session.commit()
    row, _ = routine.enqueue("drip")
    completed = routine.run(row)
    assert completed.state == "failed"
    assert completed.status == "skipped"
    assert completed.journal["result"]["skipped_reason"] == "drip_disabled"
    assert routine.world.writes == []
    assert ActivityLog.query.count() == 0


def test_review_or_config_refusal_never_fabricates_a_schedule_run_or_authenticates(routine):
    row, command = routine.enqueue("raid")
    source = UpstreamSource.query.one()
    source.raid_count += 1
    db.session.commit()
    completed = routine.run(row)
    assert completed.state == "failed"
    assert routine.factories == []
    assert completed.journal["result"]["operation"]["error_code"] == "configuration_changed"
    db.session.expire_all()
    assert db.session.get(Schedule, command.definition.schedule_id).last_run_at is None
    assert ActivityLog.query.count() == 0


def test_lost_accepted_collect_reply_does_not_repeat_append_snapshot_or_staging(routine, monkeypatch):
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    local = routine.repository.local_step

    def crash(lease, name, callback, **kwargs):
        value = local(lease, name, callback, **kwargs)
        if name == "accept.raid":
            raise Crash()
        return value

    monkeypatch.setattr(routine.repository, "local_step", crash)
    runner = OperationWorker(
        routine.app,
        repository=routine.repository,
        handlers=handlers(),
        api_factory=routine.factory,
        heartbeat_interval=0,
    )
    with pytest.raises(Crash):
        runner.run_once(row.id)
    monkeypatch.setattr(routine.repository, "local_step", local)
    with routine.repository.engine.begin() as connection:
        connection.execute(
            update(JobExecution)
            .where(JobExecution.id == row.id)
            .values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
    completed = routine.run(row)
    assert completed.state == "succeeded"
    assert routine.source_reads == ["source"]
    assert PendingRaidTrack.query.count() == 2
    assert (
        sum(path == "/playlists/collection/items" and method == "POST" for method, path, _ in routine.world.writes) == 1
    )
    assert ActivityLog.query.filter_by(activity_type="schedule_run").count() == 1


def test_later_drip_failure_restores_only_drip_and_reports_retained_collect(routine):
    dispatch = routine.world.dispatch
    rejected = []

    def reject(method, path, body):
        if method == "DELETE" and path == "/playlists/collection/items" and not rejected:
            rejected.append(True)
            from shuffify.spotify.exceptions import SpotifyWriteRejectedError

            raise SpotifyWriteRejectedError("Example rejection")
        return dispatch(method, path, body)

    # Return a real provider rejection through the transport while preserving the actual HTTP observer.
    factory = routine.factory

    def api(user):
        value = factory(user)
        send = value._http._session.request

        def transport(*args, **kwargs):
            try:
                return send(*args, **kwargs)
            except Exception as error:
                from shuffify.spotify.exceptions import SpotifyWriteRejectedError

                if not isinstance(error, SpotifyWriteRejectedError):
                    raise
                response = requests.Response()
                response.status_code, response._content = 400, b"{}"
                return response

        value._http._session.request = transport
        return value

    routine.world.dispatch = reject
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    runner = OperationWorker(
        routine.app, repository=routine.repository, handlers=handlers(), api_factory=api, heartbeat_interval=0
    )
    assert runner.run_once(row.id) is True
    completed = routine.repository.get(row.id)
    assert completed.state == "rolled_back"
    result = completed.journal["result"]
    assert result["status"] == "failed_rolled_back"
    assert result["tracks_raided"] == 2
    assert result["tracks_dripped"] == 0
    assert result["operation"]["steps"][1]["status"] == "restored"
    assert result["operation"]["error_code"] == "write_rejected"
    assert routine.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
    assert routine.world.playlists["collection"]["track_uris"] == [uri(4), uri(4), uri(5), uri(7)]
    assert PendingRaidTrack.query.count() == 2
    assert all(row.status == "pending" for row in PendingRaidTrack.query.all())
    assert ActivityLog.query.filter_by(activity_type="schedule_run_rolled_back").count() == 1


def test_required_promotion_failure_preserves_verified_drip_facts_without_compensation(routine, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("promotion unavailable")

    monkeypatch.setattr("shuffify.services.operations.executors.collect_storage.promote_pending", unavailable)
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    completed = routine.run(row)
    assert completed.state == "needs_attention"
    result = completed.journal["result"]
    assert result["error_code"] == "required_bookkeeping_failed"
    assert result["tracks_dripped"] == 2
    target = next(value for value in result["operation"]["resources"] if value["resource"] == "target")
    assert target["status"] == "complete"
    assert target["current_track_uris"] == routine.world.playlists["target"]["track_uris"]
    assert len(routine.world.writes) == 3
    assert PendingRaidTrack.query.count() == 2
    assert all(value.status == "pending" for value in PendingRaidTrack.query.all())


def test_no_link_composite_retains_database_collect_before_refusing_drip(routine):
    db.session.delete(RaidPlaylistLink.query.one())
    db.session.commit()
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    completed = routine.run(row)
    assert completed.state == "failed"
    result = completed.journal["result"]
    assert result["tracks_raided"] == 2
    assert result["operation"]["steps"][1]["status"] == "not_started"
    assert result["error_code"] == "link_required"
    assert PendingRaidTrack.query.count() == 2
    assert routine.world.writes == []


def test_terminal_recovery_updates_existing_activity_after_external_edit(routine, monkeypatch):
    row, _ = routine.enqueue("raid")
    finish = routine.repository.finish
    monkeypatch.setattr(routine.repository, "finish", lambda *args, **kwargs: (_ for _ in ()).throw(Crash()))
    runner = OperationWorker(
        routine.app,
        repository=routine.repository,
        handlers=handlers(),
        api_factory=routine.factory,
        heartbeat_interval=0,
    )
    with pytest.raises(Crash):
        runner.run_once(row.id)
    assert ActivityLog.query.filter_by(activity_type="schedule_run").count() == 1
    first = ActivityLog.query.filter_by(activity_type="schedule_run").one().id
    routine.world.playlists["target"].update(track_uris=[uri(99)], snapshot_id="external")
    written = deepcopy(routine.world.writes)
    monkeypatch.setattr(routine.repository, "finish", finish)
    with routine.repository.engine.begin() as connection:
        connection.execute(
            update(JobExecution)
            .where(JobExecution.id == row.id)
            .values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
    completed = routine.run(row)
    assert completed.state == "needs_attention"
    assert completed.journal["result"]["tracks_total"] is None
    assert routine.world.writes == written
    db.session.expire_all()
    assert ActivityLog.query.filter_by(activity_type="schedule_run").one().id == first
    assert (
        ActivityLog.query.filter_by(activity_type="schedule_run").one().metadata_json["aggregate_status"] == "unknown"
    )
    assert db.session.get(Schedule, row.schedule_id).last_status == "failed"


def test_rejected_collection_is_failed_while_only_changed_target_is_restored(routine):
    factory = routine.factory

    def api(user):
        value = factory(user)
        send = value._http._session.request

        def transport(method, url, **kwargs):
            if method == "DELETE" and "/collection/items" in url:
                response = requests.Response()
                response.status_code, response._content = 400, b"{}"
                return response
            return send(method, url, **kwargs)

        value._http._session.request = transport
        return value

    row, _ = routine.enqueue("drip", direct=True)
    runner = OperationWorker(
        routine.app, repository=routine.repository, handlers=handlers(), api_factory=api, heartbeat_interval=0
    )
    assert runner.run_once(row.id)
    result = routine.repository.get(row.id).journal["result"]
    resources = {item["resource"]: item for item in result["operation"]["resources"]}
    assert resources["target"]["status"] == "restored"
    assert resources["collection"]["status"] == "failed"
    assert resources["collection"]["affected_count"] == 0


@pytest.mark.parametrize("kind,empty", [("shuffle", True), ("rotate", True), ("rotate", False), ("drip", False)])
def test_proven_noop_steps_are_complete_with_zero_counts_and_no_mutation_log(routine, kind, empty):
    routine.world.playlists["collection"]["track_uris"] = []
    routine.world.playlists["archive"]["track_uris"] = []
    if empty:
        routine.world.playlists["target"]["track_uris"] = []
    row, _ = routine.enqueue(kind, params={"target_size": 3})
    completed = routine.run(row)
    result = completed.journal["result"]
    assert completed.state == "succeeded"
    assert result["result_detail"]["steps"][0]["status"] == "complete"
    assert all(value == 0 for value in result["result_detail"]["steps"][0]["counts"].values())
    assert result["result_detail"]["steps"][0]["counts"]
    assert routine.world.writes == []
    assert ActivityLog.query.count() == 0


def test_saved_schedule_keeps_nested_run_summary_with_the_same_parent_identity(routine):
    row, _ = routine.enqueue("raid_and_drip")
    result = routine.run(row).journal["result"]
    for field in (
        "execution_id",
        "execution_state",
        "started_at",
        "tracks_added",
        "tracks_total",
        "status",
        "error_code",
        "result_detail",
    ):
        assert result["result"][field] == result[field]
    assert all(step["execution_id"] == row.id for step in result["result"]["steps"])


def test_source_resolution_configuration_drift_is_refused_before_first_write(routine, monkeypatch):
    from shuffify.services.operations.executors.raid import SourceResolver

    resolve = SourceResolver.resolve

    def relink(*args, **kwargs):
        result = resolve(*args, **kwargs)
        RaidPlaylistLink.query.one().raid_playlist_id = "relinked"
        db.session.commit()
        return result

    monkeypatch.setattr(SourceResolver, "resolve", relink)
    row, _ = routine.enqueue("raid")
    completed = routine.run(row)
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "configuration_changed"
    assert routine.world.writes == []
    assert PendingRaidTrack.query.count() == 0
    assert PlaylistSnapshot.query.count() == 0


def test_dependent_step_revalidates_after_collect_acceptance_and_retains_collect(routine, monkeypatch):
    local = routine.repository.local_step

    def change_after_collect(lease, name, callback, **kwargs):
        value = local(lease, name, callback, **kwargs)
        if name == "accept.raid":
            RaidPlaylistLink.query.one().drip_count += 1
            db.session.commit()
        return value

    monkeypatch.setattr(routine.repository, "local_step", change_after_collect)
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    completed = routine.run(row)
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "configuration_changed"
    assert [step["name"] for step in completed.journal["boundaries"]["accepted"]] == ["raid"]
    assert PendingRaidTrack.query.count() == 2
    assert len(routine.world.writes) == 1
    assert routine.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]


@pytest.mark.parametrize("status", ["failed", "failed_rolled_back", "skipped"])
def test_contradictory_producer_status_keeps_verified_collect_and_stops_drip(routine, monkeypatch, status):
    module = importlib.import_module("shuffify.services.operations.commands.routines")
    prepare, execute = module.EXECUTORS["raid"]

    def contradictory(*args, **kwargs):
        result = execute(*args, **kwargs)
        result["result"].update(success=True, status=status, execution_id=999999)
        return result

    monkeypatch.setitem(module.EXECUTORS, "raid", (prepare, contradictory))
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    completed = routine.run(row)
    result = completed.journal["result"]
    assert completed.state == "failed"
    assert result["success"] is False
    assert result["operation"]["aggregate_status"] == "unknown"
    assert result["operation"]["error_code"] == "producer_result_inconsistent"
    assert result["execution_id"] == row.id
    assert result["operation"]["steps"][1]["status"] == "not_started"
    assert len(routine.world.writes) == 1
    assert PendingRaidTrack.query.count() == 2


@pytest.mark.parametrize(
    "helper,receipt", [("snapshot", "routine.raid.snapshots.v1"), ("activity", "routine.finalize.succeeded.v1")]
)
def test_optional_run_receipts_record_failure_once_without_reversing_verified_effects(
    routine,
    monkeypatch,
    helper,
    receipt,
):
    from shuffify.services.operations.bookkeeping import OperationBookkeeping

    calls = []

    def unavailable(*args, **kwargs):
        calls.append(helper)
        raise ValueError("Optional storage unavailable")

    monkeypatch.setattr(OperationBookkeeping, helper, unavailable)
    row, _ = routine.enqueue("raid")
    completed = routine.run(row)
    assert completed.state == "succeeded"
    assert PendingRaidTrack.query.count() == 2
    assert len(routine.world.writes) == 1
    saved = completed.journal["runtime"]["local_steps"][receipt]
    facts = saved if isinstance(saved, list) else [saved["activity"]]
    assert facts and all(item["status"] == "failed" for item in facts)
    assert calls


def test_changed_review_scope_refuses_before_authentication_or_run_metadata(routine):
    row, _ = routine.enqueue("raid", reviewed={"reviewed_scope": "0" * 64})
    completed = routine.run(row)
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "review_changed"
    from shuffify.services.live_review_bindings import REVIEW_CHANGED

    assert completed.journal["result"]["message"] == REVIEW_CHANGED
    assert routine.factories == []
    assert routine.world.writes == []
    db.session.expire_all()
    assert db.session.get(Schedule, row.schedule_id).last_run_at is None


def _expire(routine, operation_id):
    with routine.repository.engine.begin() as connection:
        connection.execute(
            update(JobExecution)
            .where(JobExecution.id == operation_id)
            .values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )


def test_prepared_drip_recovery_promotes_only_frozen_pending_row_ids(routine, monkeypatch):
    from shuffify.services.operations.runtime import OperationRuntime

    monkeypatch.setattr(
        "shuffify.services.operations.executors.drip.random.sample", lambda pool, count: list(pool)[:count]
    )
    prepare = OperationRuntime.prepare
    crashed = []

    def crash_after_preparation(self, name, calculate):
        value = prepare(self, name, calculate)
        if name == "routine.drip.plan.v1" and not crashed:
            crashed.append(True)
            raise Crash()
        return value

    monkeypatch.setattr(OperationRuntime, "prepare", crash_after_preparation)
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    with pytest.raises(Crash):
        routine.run(row)
    frozen_ids = {item.id for item in PendingRaidTrack.query.all()}
    late = PendingRaidTrack(
        user_id=routine.user.id,
        target_playlist_id="target",
        track_uri=uri(4),
        track_name="Later discovery",
        status="pending",
    )
    db.session.add(late)
    db.session.commit()
    late_id = late.id
    _expire(routine, row.id)
    completed = routine.run(row)
    assert completed.state == "succeeded"
    db.session.expire_all()
    assert db.session.get(PendingRaidTrack, late_id).status == "pending"
    assert PendingRaidTrack.query.filter_by(status="promoted").count() == 0
    assert {item.id for item in PendingRaidTrack.query.filter_by(status="pending")} == frozen_ids | {late_id}
    accepted = next(item for item in completed.journal["boundaries"]["accepted"] if item["name"] == "drip")
    assert accepted["result"]["receipt"]["promoted_ids"] == []
    assert routine.source_reads == ["source"]
    assert len(routine.world.writes) == 3


def test_provider_retry_hint_defers_the_same_operation_without_compensation_or_choice_replay(routine):
    factory, rejected = routine.factory, []

    def api(user):
        value = factory(user)
        send = value._http._session.request

        def transport(method, url, **kwargs):
            if method == "POST" and "/target/items" in url and not rejected:
                rejected.append(True)
                response = requests.Response()
                response.status_code, response._content = 429, b"{}"
                response.headers["Retry-After"] = "120"
                return response
            return send(method, url, **kwargs)

        value._http._session.request = transport
        return value

    row, _ = routine.enqueue("raid_and_drip", direct=True)
    runner = OperationWorker(
        routine.app, repository=routine.repository, handlers=handlers(), api_factory=api, heartbeat_interval=0
    )
    assert runner.run_once(row.id) is False
    deferred = routine.repository.get(row.id)
    assert deferred.state == "running"
    assert deferred.journal["boundaries"]["accepted"][0]["name"] == "raid"
    assert "failure" not in deferred.journal["runtime"]
    assert len(routine.world.writes) == 1
    assert ActivityLog.query.filter_by(activity_type="schedule_run").count() == 0
    assert runner.run_once(row.id) is False
    assert len(routine.world.writes) == 1
    document = deferred.journal
    for plan in document["plans"]:
        for batch in plan["batches"]:
            if batch.get("next_attempt_at"):
                batch["next_attempt_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with routine.repository.engine.begin() as connection:
        connection.execute(
            update(JobExecution)
            .where(JobExecution.id == row.id)
            .values(
                journal=document,
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
    assert runner.run_once(row.id) is True
    completed = routine.repository.get(row.id)
    assert completed.state == "succeeded"
    assert JobExecution.query.count() == 1
    assert routine.source_reads == ["source"]
    assert len(routine.world.writes) == 3


def test_lost_lease_from_local_commit_is_not_converted_into_a_cached_child_failure(routine, monkeypatch):
    from shuffify.services.operations.repository import LeaseLost

    def lost(*args, **kwargs):
        raise LeaseLost("Claim changed")

    monkeypatch.setattr("shuffify.services.operations.commands.routines.apply_raid_local", lost)
    row, _ = routine.enqueue("raid", direct=True)
    runner = OperationWorker(
        routine.app,
        repository=routine.repository,
        handlers=handlers(),
        api_factory=routine.factory,
        heartbeat_interval=0,
    )
    assert runner.run_once(row.id) is False
    saved = routine.repository.get(row.id).journal
    assert saved["boundaries"]["accepted"] == []
    assert "failure" not in saved["runtime"]
    assert "routine_db_failure" not in saved["runtime"]
    assert "accept.raid" not in saved["runtime"]["local_steps"]
    assert len(routine.world.writes) == 1
    assert PendingRaidTrack.query.count() == 0


def test_refused_initial_binding_keeps_queue_identity_but_does_not_claim_the_run_started(routine):
    row, _ = routine.enqueue("raid", reviewed={"reviewed_scope": "0" * 64})
    completed = routine.run(row)
    result = completed.journal["result"]
    assert result["execution_id"] == row.id
    assert result["execution_state"] == result["result"]["execution_state"] == "not_started"
    assert result["started_at"] is result["result"]["started_at"] is None
    assert all(step["execution_state"] == "not_started" for step in result["operation"]["steps"])
    assert routine.factories == []


def test_required_staging_failure_keeps_verified_collection_count_and_needs_review(routine, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("Inbox storage unavailable")

    monkeypatch.setattr("shuffify.services.operations.executors.collect_storage.stage_pending", unavailable)
    row, _ = routine.enqueue("raid", direct=True)
    completed = routine.run(row)
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert result["tracks_added"] == 2
    collection = next(item for item in result["operation"]["resources"] if item["resource"] == "collection")
    assert collection["status"] == "complete"
    assert collection["current_track_uris"] == routine.world.playlists["collection"]["track_uris"]
    assert PendingRaidTrack.query.count() == 0
    assert len(routine.world.writes) == 1
    assert result["operation"]["bookkeeping"][1]["status"] == "failed"


def test_terminal_database_failure_downgrades_both_summaries_without_compensation(routine, monkeypatch):
    local = routine.repository.local_step

    def unavailable(lease, name, callback, **kwargs):
        if name.startswith("routine.finalize."):
            raise ValueError("Run storage unavailable")
        return local(lease, name, callback, **kwargs)

    monkeypatch.setattr(routine.repository, "local_step", unavailable)
    row, _ = routine.enqueue("raid")
    completed = routine.run(row)
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert result["error_code"] == result["result"]["error_code"] == "terminal_finalization_failed"
    assert result["status"] == result["result"]["status"] == "failed"
    assert result["tracks_added"] == 2
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert len(routine.world.writes) == 1
    assert PendingRaidTrack.query.count() == 2


def test_explicit_drip_forces_a_closed_saved_gate_and_preserves_duplicate_occurrences(routine):
    RaidPlaylistLink.query.one().drip_enabled = False
    db.session.commit()
    row, _ = routine.enqueue("drip", direct=True)
    completed = routine.run(row)
    result = completed.journal["result"]
    assert completed.state == "succeeded"
    assert result["tracks_added"] == 2
    assert routine.world.playlists["target"]["track_uris"] == [uri(4), uri(4), uri(1), uri(2), uri(3)]
    assert routine.world.playlists["collection"]["track_uris"] == []
    assert result["result_detail"]["steps"][0]["counts"] == {
        "tracks_moved_to_target": 2,
        "tracks_removed_from_collection": 2,
    }


@pytest.mark.parametrize("empty,expected", [(True, "succeeded"), (False, "failed")])
def test_empty_rotate_checks_empty_before_cap(routine, empty, expected):
    if empty:
        routine.world.playlists["target"]["track_uris"] = []
    row, _ = routine.enqueue("rotate", params={})
    completed = routine.run(row)
    assert completed.state == expected
    assert routine.world.writes == []
    assert completed.journal["result"]["tracks_added"] == 0


def test_accepted_collect_receipt_exactly_identifies_staged_rows_and_source_tracking(routine):
    row, _ = routine.enqueue("raid", direct=True)
    completed = routine.run(row)
    accepted = completed.journal["boundaries"]["accepted"][0]
    receipt = accepted["result"]["receipt"]
    rows = PendingRaidTrack.query.all()
    assert receipt["staged_ids"] == receipt["pending_stage"]["ids"] == [value.id for value in rows]
    assert receipt["pending_stage"]["affected_count"] == len(rows) == 2
    source = UpstreamSource.query.one()
    db.session.refresh(source)
    assert source.last_track_count == 2
    assert source.last_resolved_at is not None
    assert source.last_resolve_status == "success"
    assert source.last_resolve_pathway == "direct_api"


@pytest.mark.parametrize(
    "kind,destinations",
    [
        ("raid", {"target", "collection"}),
        ("drip", {"target", "collection"}),
        ("rotate", {"target"}),
        ("shuffle", {"target"}),
    ],
)
def test_automatic_snapshot_destinations_match_the_step_not_readonly_inputs(routine, kind, destinations):
    row, _ = routine.enqueue(kind, params={"target_size": 3, "rotation_count": 1} if kind == "rotate" else {})
    assert routine.run(row).state == "succeeded"
    snapshots = PlaylistSnapshot.query.all()
    assert {item.playlist_id for item in snapshots} == destinations
    assert len(snapshots) == len(destinations)


def test_each_step_exposes_its_own_outcome_code_and_preserves_known_bookkeeping_failure(routine, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("Storage unavailable")

    monkeypatch.setattr("shuffify.services.operations.commands.routines.apply_drip_local", unavailable)
    row, _ = routine.enqueue("raid_and_drip", direct=True)
    result = routine.run(row).journal["result"]
    assert result["operation"]["aggregate_status"] == "partial"
    assert [step["aggregate_status"] for step in result["operation"]["steps"]] == ["complete", "partial"]
    assert [step["error_code"] for step in result["operation"]["steps"]] == [None, "required_bookkeeping_failed"]
    assert result["result_detail"]["steps"][1]["status"] == "partial"


@pytest.mark.parametrize(
    "point,expected", [("_initial_binding", "coordination_failed"), ("_step_inputs", "execution_failed")]
)
def test_internal_comparison_errors_use_authored_phase_codes_without_exception_details(
    routine,
    monkeypatch,
    point,
    expected,
):
    def broken(*args, **kwargs):
        raise ValueError("private implementation detail")

    monkeypatch.setattr("shuffify.services.operations.commands.routines." + point, broken)
    row, _ = routine.enqueue("shuffle")
    result = routine.run(row).journal["result"]
    assert result["operation"]["error_code"] == expected
    assert "private" not in json.dumps(result)
    assert routine.world.writes == []


def test_database_comparison_outage_is_authored_retryable_and_does_not_leak_driver_code(routine, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def unavailable(*args, **kwargs):
        raise OperationalError("SELECT private_column", {}, Exception("private database details"))

    monkeypatch.setattr("shuffify.services.operations.commands.routines.recapture", unavailable)
    row, _ = routine.enqueue("shuffle")
    result = routine.run(row).journal["result"]
    assert (result["error_code"], result["operation"]["retryable"]) == ("database_read_failed", True)
    assert result["retryable"] is True
    assert result["execution_state"] == "not_started"
    assert "private" not in json.dumps(result)
    assert "e3q8" not in json.dumps(result)
    assert routine.factories == []


def test_detached_schedule_binding_retains_accepted_identity_and_not_started_summary(routine):
    row, _ = routine.enqueue("shuffle")
    with routine.repository.engine.begin() as connection:
        connection.execute(update(JobExecution).where(JobExecution.id == row.id).values(schedule_id=None))
    completed = routine.run(row)
    result = completed.journal["result"]
    assert completed.state == "failed"
    assert result["execution_id"] == result["result"]["execution_id"] == row.id
    assert result["execution_state"] == result["result"]["execution_state"] == "not_started"
    assert result["error_code"] == "configuration_changed"
    assert routine.factories == []


def test_postwrite_verification_outage_does_not_republish_stale_acknowledged_current_claims(routine):
    factory = routine.factory

    def api(user):
        value = factory(user)
        send, metadata = value._http._session.request, value.get_playlist
        unavailable = []

        def transport(method, url, **kwargs):
            response = send(method, url, **kwargs)
            if method == "DELETE" and "/collection/items" in url:
                unavailable.append(True)
            return response

        def read(resource, **kwargs):
            if resource == "collection" and unavailable:
                raise RuntimeError("private read failure")
            return metadata(resource, **kwargs)

        value._http._session.request, value.get_playlist = transport, read
        return value

    row, _ = routine.enqueue("drip", direct=True)
    runner = OperationWorker(
        routine.app, repository=routine.repository, handlers=handlers(), api_factory=api, heartbeat_interval=0
    )
    assert runner.run_once(row.id)
    result = routine.repository.get(row.id).journal["result"]
    assert result["operation"]["aggregate_status"] == "unknown"
    assert result["tracks_total"] is None
    assert all(
        item["status"] == "unknown" and "current_track_uris" not in item for item in result["operation"]["resources"]
    )


def test_nonfinite_config_after_initial_binding_is_authored_configuration_change(routine, monkeypatch):
    from shuffify.services.operations.commands import routines

    bind = routines._initial_binding

    def change(runtime, command):
        captured = bind(runtime, command)
        schedule = db.session.get(Schedule, command.definition.schedule_id)
        schedule.algorithm_params = {"keep_first": float("nan")}
        db.session.commit()
        return captured

    monkeypatch.setattr(routines, "_initial_binding", change)
    row, _ = routine.enqueue("shuffle")
    result = routine.run(row).journal["result"]
    assert result["error_code"] == "configuration_changed"
    assert routine.factories == []


def test_failed_compensation_cannot_republish_forward_current_state_or_erase_accepted_prefix(routine):
    factory = routine.factory

    def api(user):
        value = factory(user)
        send = value._http._session.request

        def transport(method, url, **kwargs):
            if method == "DELETE" and "/collection/items" in url or method == "PUT" and "/target/items" in url:
                response = requests.Response()
                response.status_code = 400 if method == "DELETE" else 500
                response._content = b"{}"
                return response
            return send(method, url, **kwargs)

        value._http._session.request = transport
        return value

    row, _ = routine.enqueue("raid_and_drip", direct=True)
    runner = OperationWorker(
        routine.app, repository=routine.repository, handlers=handlers(), api_factory=api, heartbeat_interval=0
    )
    assert runner.run_once(row.id)
    completed = routine.repository.get(row.id)
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert completed.journal["phase"] == "compensating"
    assert [item["name"] for item in completed.journal["boundaries"]["accepted"]] == ["raid"]
    assert result["operation"]["aggregate_status"] == "unknown"
    assert result["operation"]["steps"][0]["status"] == "complete"
    assert result["operation"]["steps"][0]["aggregate_status"] == "complete"
    assert result["operation"]["steps"][1]["aggregate_status"] == "unknown"
    assert result["tracks_raided"] == 2
    assert PendingRaidTrack.query.count() == 2
    assert all(
        item["status"] == "unknown" and "current_track_uris" not in item
        for item in result["operation"]["resources"]
        if item["dispatched"]
    )


def test_saved_closed_drip_gate_precedes_invalid_count_conversion(routine):
    RaidPlaylistLink.query.one().drip_enabled = False
    db.session.commit()
    row, _ = routine.enqueue("drip", params={"drip_count": "bad"})
    completed = routine.run(row)
    result = completed.journal["result"]
    assert completed.state == "failed"
    assert completed.status == result["status"] == "skipped"
    assert result["skipped_reason"] == "drip_disabled"
    assert result["result_detail"]["steps"][0]["status"] == "not_started"
    assert routine.world.writes == []


@pytest.mark.parametrize("kind,expected", [("drip", True), ("raid_and_drip", False)])
def test_closed_saved_gate_is_retryable_only_when_no_prior_child_effects_exist(routine, kind, expected):
    RaidPlaylistLink.query.one().drip_enabled = False
    db.session.commit()
    row, _ = routine.enqueue(kind)
    completed = routine.run(row)
    result = completed.journal["result"]
    assert completed.state == "failed"
    assert result["skipped_reason"] == "drip_disabled"
    assert result["retryable"] is result["operation"]["retryable"] is expected
