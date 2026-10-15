"""Successful and recovered verified effects survive local publication outages."""
# ruff: noqa: F811

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from shuffify.models.db import ActivityLog, JobExecution
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.worker import OperationWorker
from tests.services.operations.test_composite_runtime import (
    BEFORE,
    COLLECTED,
    DRIPPED,
    PLAYLIST,
    collect,
    composite,  # noqa: F401
)
from tests.services.operations.test_crash_recovery import Crash
from tests.services.operations.test_finalizer_commands import cleanup  # noqa: F401
from tests.services.operations.test_routine_commands import handlers, routine  # noqa: F401
from tests.services.operations.test_unarchive_command import move  # noqa: F401


def outage():
    return OperationalError("local publication statement", {}, Exception("private database detail"))


def expire(repository, operation_id):
    with repository.engine.begin() as connection:
        connection.execute(
            update(JobExecution)
            .where(JobExecution.id == operation_id)
            .values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )


@pytest.fixture(params=["routine", "finalizer", "unarchive"])
def family(request, cleanup):
    if request.param == "routine":
        row, _command = cleanup.enqueue("shuffle")
        registry, label = handlers(), "shuffle"
    elif request.param == "finalizer":
        from shuffify.services.operations.commands.finalizers import HANDLERS

        row, registry, label = cleanup.enqueue_cleanup(), HANDLERS, "finalize_raids"
    else:
        from shuffify.services.operations.commands.archive_move import unarchive

        cleanup = request.getfixturevalue("move")
        row, registry, label = cleanup.enqueue_move(), {"unarchive": unarchive}, "unarchive"
    runner = OperationWorker(
        cleanup.app, repository=cleanup.repository, handlers=registry, api_factory=cleanup.factory, heartbeat_interval=0
    )
    return SimpleNamespace(**vars(cleanup), row=row, runner=runner, label=label, family=request.param)


def test_completed_handler_publication_failure_stays_pending_and_recovers_same_receipts(family, monkeypatch):
    checkpoint, failed = family.repository.checkpoint, []

    def unavailable(lease, document, **options):
        if document.get("boundaries", {}).get("accepted") and not failed:
            failed.append(True)
            raise outage()
        return checkpoint(lease, document, **options)

    monkeypatch.setattr(family.repository, "checkpoint", unavailable)
    assert family.runner.run_once(family.row.id) is False
    writes = deepcopy(family.world.writes)
    assert len(writes) == (2 if family.family == "unarchive" else 1)
    assert family.repository.get(family.row.id).state == "running"
    monkeypatch.setattr(family.repository, "checkpoint", checkpoint)
    expire(family.repository, family.row.id)
    assert family.runner.run_once(family.row.id) is True
    result = family.repository.get(family.row.id)
    assert result.state == "succeeded"
    assert family.world.writes == writes
    assert ActivityLog.query.count() == 1
    assert "private database detail" not in str(result.journal["result"])


def test_local_control_read_outage_after_executor_proof_never_compensates(family, monkeypatch):
    from shuffify.services.operations.commands import archive_move, routines
    from shuffify.services.operations.commands.finalizers import finalize_raids

    proved, failed = [], []

    def after_proof(execute):
        def wrapped(*args, **options):
            value = execute(*args, **options)
            proved.append(True)
            return value

        return wrapped

    if family.family == "routine":
        prepare, execute = routines.EXECUTORS["shuffle"]
        monkeypatch.setitem(routines.EXECUTORS, "shuffle", (prepare, after_proof(execute)))
    elif family.family == "finalizer":
        monkeypatch.setattr(finalize_raids, "execute", after_proof(finalize_raids.execute))
    else:
        monkeypatch.setattr(archive_move, "execute_unarchive", after_proof(archive_move.execute_unarchive))
    control = OperationRuntime.check_pending_control

    def unavailable(self):
        if proved and not failed:
            failed.append(True)
            raise outage()
        return control(self)

    monkeypatch.setattr(OperationRuntime, "check_pending_control", unavailable)
    assert family.runner.run_once(family.row.id) is True
    completed = family.repository.get(family.row.id)
    assert proved and failed == [True]
    assert completed.state == "needs_attention"
    assert len(family.world.writes) == (2 if family.family == "unarchive" else 1)
    assert completed.journal.get("compensation", []) == []


def test_recovered_composite_proof_save_failure_preserves_verified_provider_effect(family, monkeypatch):
    local_step, stopped = family.repository.local_step, []

    def crash(lease, name, callback, **options):
        if name == "accept." + family.label and not stopped:
            stopped.append(True)
            raise Crash()
        return local_step(lease, name, callback, **options)

    monkeypatch.setattr(family.repository, "local_step", crash)
    with pytest.raises(Crash):
        family.runner.run_once(family.row.id)
    assert len(family.world.writes) == (2 if family.family == "unarchive" else 1)
    after = deepcopy(family.world.playlists)
    expire(family.repository, family.row.id)
    checkpoint, failed = OperationRuntime.checkpoint, []

    def unavailable(self, document):
        if self.lease.recovering and document.get("status") == "succeeded" and not failed:
            failed.append(True)
            raise outage()
        return checkpoint(self, document)

    monkeypatch.setattr(OperationRuntime, "checkpoint", unavailable)
    assert family.runner.run_once(family.row.id) is True
    result = family.repository.get(family.row.id)
    assert result.state == "needs_attention"
    assert family.world.playlists == after
    assert len(family.world.writes) == (2 if family.family == "unarchive" else 1)
    body = result.journal["result"]
    assert body["error_code"] == "verified_step_publication_failed"
    if family.family == "routine":
        target = next(fact for fact in body["operation"]["resources"] if fact["resource"] == "target")
        assert target["status"] == "complete"
        assert target["current_track_uris"] == after["target"]["track_uris"]
        assert body["tracks_total"] == len(after["target"]["track_uris"])
    elif family.family == "finalizer":
        assert body["source_status"] == "complete"
        assert body["database_status"] == "unknown" and body["promoted_count"] is None
    else:
        assert body["unarchived_count"] == body["tracks_added"] == 3
        assert body["removed_count"] == 2
        assert body["tracks_total"] == len(after["target"]["track_uris"])
    assert "private database detail" not in str(result.journal["result"])
    assert "before" not in result.journal["result"]
    assert "after" not in result.journal["result"]


def test_recovery_reuses_accepted_prefix_then_accepts_current_before_follower(composite, monkeypatch):
    repository, operation_id, world, build, expire_lease = composite
    local_step, stopped, followers = repository.local_step, [], []

    def interrupted(lease, name, callback, **options):
        if name == "accept.drip" and not stopped:
            stopped.append(True)
            raise Crash()
        return local_step(lease, name, callback, **options)

    monkeypatch.setattr(repository, "local_step", interrupted)

    def handler(runtime):
        collect(runtime)
        runtime.composite_step(
            "drip",
            lambda: runtime.step(
                "drip-write",
                lambda: {
                    "success": runtime.api.playlist_add_items(PLAYLIST, [DRIPPED]),
                },
            ),
            lambda session, result: result,
        )
        assert runtime.verified_step_failure is None
        followers.append(True)
        return {"success": True}

    with pytest.raises(Crash):
        build(handler).run_once(operation_id)
    expire_lease()
    assert build(handler).run_once(operation_id) is True
    assert repository.get(operation_id).state == "succeeded"
    assert world.uris == [BEFORE, COLLECTED, DRIPPED]
    assert world.calls == 2
    assert ActivityLog.query.count() == 1
    assert followers == [True]


@pytest.mark.parametrize("lost_conflict_checkpoint", [False, True])
def test_fresh_external_conflict_removes_domain_counts_and_never_overwrites(
    family,
    monkeypatch,
    lost_conflict_checkpoint,
):
    local_step = family.repository.local_step

    def crash(lease, name, callback, **options):
        if name == "accept." + family.label:
            raise Crash()
        return local_step(lease, name, callback, **options)

    monkeypatch.setattr(family.repository, "local_step", crash)
    with pytest.raises(Crash):
        family.runner.run_once(family.row.id)
    resource = "collection" if family.family == "finalizer" else "target"
    family.world.playlists[resource]["track_uris"] = ["spotify:track:external"]
    family.world.playlists[resource]["snapshot_id"] = "external-revision"
    expected, writes = deepcopy(family.world.playlists), deepcopy(family.world.writes)
    expire(family.repository, family.row.id)
    checkpoint, failures = OperationRuntime.checkpoint, []

    def unavailable(self, document):
        if lost_conflict_checkpoint and document.get("status") == "needs_attention" and not failures:
            failures.append(True)
            raise outage()
        return checkpoint(self, document)

    monkeypatch.setattr(OperationRuntime, "checkpoint", unavailable)
    assert family.runner.run_once(family.row.id) is True
    completed = family.repository.get(family.row.id)
    assert completed.state == "needs_attention"
    assert family.world.playlists == expected and family.world.writes == writes
    body = completed.journal["result"]
    assert body["error_code"] != "verified_step_publication_failed"
    if family.family == "routine":
        assert body["tracks_total"] is None
        assert all("current_track_uris" not in fact for fact in body["operation"]["resources"])
    elif family.family == "finalizer":
        assert body["source_status"] == "unknown" and body["promoted_count"] is None
    else:
        assert body["unarchived_count"] is body["removed_count"] is body["tracks_total"] is None


def test_fresh_recovery_guard_blocks_an_unrelated_new_child(composite, monkeypatch):
    repository, operation_id, world, build, expire_lease = composite
    local_step, stopped = repository.local_step, []

    def interrupted(lease, name, callback, **options):
        if name == "accept.collect" and not stopped:
            stopped.append(True)
            raise Crash()
        return local_step(lease, name, callback, **options)

    monkeypatch.setattr(repository, "local_step", interrupted)
    with pytest.raises(Crash):
        build(collect).run_once(operation_id)
    expire_lease()

    def wrong_child(runtime):
        return runtime.composite_step(
            "unrelated", lambda: pytest.fail("Unrelated child cannot dispatch"), lambda session, result: result
        )

    assert build(wrong_child).run_once(operation_id) is True
    assert repository.get(operation_id).state == "needs_attention"
    assert world.uris == [BEFORE, COLLECTED]
    assert world.calls == 1


def test_fresh_recovery_conflict_never_installs_a_verified_guard(composite, monkeypatch):
    repository, operation_id, world, build, expire_lease = composite
    local_step, stopped = repository.local_step, []

    def interrupted(lease, name, callback, **options):
        if name == "accept.collect" and not stopped:
            stopped.append(True)
            raise Crash()
        return local_step(lease, name, callback, **options)

    monkeypatch.setattr(repository, "local_step", interrupted)
    with pytest.raises(Crash):
        build(collect).run_once(operation_id)
    world.uris, world.snapshot = ["spotify:track:external"], world.snapshot + 1
    expire_lease()
    protect, calls = OperationRuntime.protect_verified, []

    def observed(self, name, body, **options):
        calls.append(name)
        return protect(self, name, body, **options)

    monkeypatch.setattr(OperationRuntime, "protect_verified", observed)
    assert build(collect).run_once(operation_id) is True
    assert calls == []
    assert repository.get(operation_id).state == "needs_attention"
    assert world.uris == ["spotify:track:external"]
    assert world.calls == 1
