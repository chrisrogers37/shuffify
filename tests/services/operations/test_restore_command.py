"""A reviewed child restores only known current effects and resolves its owned parent atomically."""

from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, JobExecution, db
from shuffify.services.operations.history import HistoryStore
from shuffify.services.operations.restoration import capture_restore
from shuffify.services.operations.restore_review import build_context, read_metadata, read_tracks
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import Crash, expire_lease, uri
from tests.services.operations.test_routine_commands import routine as routine


@pytest.fixture
def restoration(routine):
    from shuffify.services.operations.commands.restore import capture_command, operation_restore

    journal = routine.world.journal()
    journal.begin_step("original")
    journal.freeze_replace("target", [uri(9), uri(9)])
    journal.resume()
    journal._attention("external_edit")
    parent = JobExecution(
        user_id=routine.user.id,
        kind="shuffle",
        targets=["target"],
        state="needs_attention",
        status="failed",
        journal=deepcopy(journal.doc),
    )
    db.session.add(parent)
    db.session.commit()
    routine.world.writes.clear()
    routine.parent_id = parent.id
    routine.scope = "restoration_scope_" + "a" * 32

    def review():
        captured = capture_restore(routine.repository.get(parent.id), routine.user.id)
        api = routine.factory(routine.user)
        try:
            return build_context(
                captured,
                lambda resource: read_tracks(api, routine.user, resource),
                lambda resource: read_metadata(api, routine.user, resource),
            )["reviewed"]
        finally:
            api.close()

    def enqueue(reviewed=None):
        with Session(db.engine) as storage:
            command = capture_command(storage, routine.user.id, parent.id, review() if reviewed is None else reviewed)
        binding = HistoryStore().capture(routine.user.id, routine.scope, command.reserved_targets)
        return routine.repository.enqueue(
            kind=command.kind,
            user_id=routine.user.id,
            targets=command.reserved_targets,
            payload={"version": 1, "command": command.model_dump(mode="json"), "history": binding},
        )

    def run(row, factory=None):
        worker = OperationWorker(
            routine.app,
            repository=routine.repository,
            handlers={"operation_restore": operation_restore},
            api_factory=factory or routine.factory,
            heartbeat_interval=0,
        )
        return worker.run_once(row.id), routine.repository.get(row.id)

    routine.review_restore, routine.enqueue_restore, routine.run_restore = review, enqueue, run
    return routine


def test_restoration_accepts_one_child_history_activity_and_owned_parent_resolution(restoration):
    parent = restoration.repository.get(restoration.parent_id)
    original_journal = deepcopy(parent.journal)
    row = restoration.enqueue_restore()
    finished, completed = restoration.run_restore(row)
    assert finished and completed.state == "succeeded"
    assert restoration.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
    assert len(restoration.world.writes) == 1
    assert ActivityLog.query.count() == 1 and JobExecution.query.count() == 2
    parent = restoration.repository.get(parent.id)
    assert parent.state == "needs_attention" and parent.journal["plans"] == original_journal["plans"]
    assert parent.journal["runtime"]["restoration"]["operation_id"] == row.id
    assert parent.journal["recovery"]["available"] is False
    entry, receipt = HistoryStore().load(restoration.user.id, restoration.scope, "target")
    assert receipt == row.id and entry["states"][-1] == [uri(1), uri(2), uri(3)]
    assert completed.journal["result"]["parent_operation_id"] == parent.id
    assert completed.journal["result"]["parent_resolution"] == "complete"


def test_changed_since_review_refuses_before_the_first_write(restoration):
    reviewed = restoration.review_restore()
    restoration.world.playlists["target"].update(track_uris=[uri(88)], snapshot_id="external")
    row = restoration.enqueue_restore(reviewed)
    finished, completed = restoration.run_restore(row)
    assert finished and completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "restore_review_changed"
    assert restoration.world.writes == [] and ActivityLog.query.count() == 0
    assert not restoration.repository.get(restoration.parent_id).journal.get("runtime", {}).get("restoration")


@pytest.mark.parametrize("lost_reply", [False, True])
def test_verified_restoration_local_failure_never_compensates_and_lost_reply_uses_proven_receipt(
    restoration, monkeypatch, lost_reply
):
    row = restoration.enqueue_restore()
    original = restoration.repository.local_step

    def fail(lease, name, callback, **kwargs):
        if name != "accept.restore":
            return original(lease, name, callback, **kwargs)
        if lost_reply:
            original(lease, name, callback, **kwargs)
        raise RuntimeError("private acceptance failure")

    monkeypatch.setattr(restoration.repository, "local_step", fail)
    finished, completed = restoration.run_restore(row)
    assert finished and completed.state == ("succeeded" if lost_reply else "needs_attention")
    assert restoration.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
    assert len(restoration.world.writes) == 1 and not completed.journal.get("compensation")
    assert completed.journal["result"]["changed_position_count"] == 3
    assert ActivityLog.query.count() == int(lost_reply)
    resolved = restoration.repository.get(restoration.parent_id).journal.get("runtime", {}).get("restoration")
    assert bool(resolved) is lost_reply


def test_crash_after_ack_recovers_same_plan_and_one_required_receipt(restoration, monkeypatch):
    row = restoration.enqueue_restore()
    original = restoration.repository.checkpoint
    cut = []

    def crash(lease, document, **kwargs):
        result = original(lease, document, **kwargs)
        if not cut and any(
            batch["state"] == "acknowledged" for plan in document.get("plans", []) for batch in plan["batches"]
        ):
            cut.append(True)
            raise Crash("restoration acknowledged")
        return result

    with monkeypatch.context() as patches:
        patches.setattr(restoration.repository, "checkpoint", crash)
        with pytest.raises(Crash):
            restoration.run_restore(row)
    expire_lease(restoration.repository, row.id)
    finished, completed = restoration.run_restore(row)
    assert finished and completed.state == "succeeded"
    assert len(restoration.world.writes) == 1 and ActivityLog.query.count() == 1
    assert completed.journal["plans"][0]["batches"][0]["attempts"] == 1


def test_database_control_outage_after_fresh_apply_verification_preserves_provider_facts(restoration, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from shuffify.services.operations.commands import restore
    from shuffify.services.operations.runtime import OperationRuntime

    original_apply, original_control = restore._apply, OperationRuntime.check_pending_control
    ready, failed = [], []

    def apply(*args, **kwargs):
        value = original_apply(*args, **kwargs)
        ready.append(True)
        return value

    def control(self):
        if ready and not failed:
            failed.append(True)
            raise OperationalError("SELECT control", {}, Exception("private outage"))
        return original_control(self)

    monkeypatch.setattr(restore, "_apply", apply)
    monkeypatch.setattr(OperationRuntime, "check_pending_control", control)
    finished, completed = restoration.run_restore(restoration.enqueue_restore())
    assert finished and completed.state == "needs_attention"
    assert completed.journal["result"]["changed_position_count"] == 3
    assert restoration.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
    assert len(restoration.world.writes) == 1
    assert not completed.journal.get("compensation")


def replace_parent(case, setup, targets=("target",), *, metadata=False):
    from shuffify.services.operations.journal import DurableJournal

    case.world.saved = None
    case.world.playlists["target"].update(track_uris=[uri(1), uri(2), uri(3)], snapshot_id="initial")
    case.world.playlists["target"]["metadata"]["public"] = False
    journal = DurableJournal(
        lambda: case.world.saved,
        case.world.checkpoint,
        case.world.read,
        case.world.dispatch,
        read_metadata=case.world.read if metadata else None,
    )
    setup(journal)
    journal._attention("external_edit")
    with Session(case.repository.engine) as storage, storage.begin():
        parent = storage.get(JobExecution, case.parent_id)
        parent.journal, parent.targets = deepcopy(journal.doc), sorted(targets)
    case.world.writes.clear()
    return journal


def test_accepted_prefix_stays_guarded_and_untouched_while_current_step_restores(restoration):
    def setup(journal):
        journal.begin_step("accepted")
        journal.freeze_add("archive", [uri(8)])
        journal.resume()
        proof = journal.verify_step("accepted")
        restoration.world.checkpoint(journal.accept_step_document(journal.doc, "accepted", proof=proof, result={}))
        journal.reload_latest()
        journal.begin_step("current")
        journal.freeze_replace("target", [uri(9)])
        journal.resume()

    replace_parent(restoration, setup, targets=("archive", "target"))
    prefix = deepcopy(restoration.world.playlists["archive"])
    row = restoration.enqueue_restore()
    assert row.targets == ["archive", "target"]
    finished, completed = restoration.run_restore(row)
    assert finished and completed.state == "succeeded"
    assert restoration.world.playlists["archive"] == prefix
    assert [path for _, path, _ in restoration.world.writes] == ["/playlists/target/items"]
    assert [item["playlist_id"] for item in completed.journal["result"]["resources"]] == ["target"]


def test_read_only_dependency_changed_since_review_refuses_all_restore_writes(restoration):
    def setup(journal):
        journal.begin_step("current")
        journal.observe_resource("archive", expected_baseline=restoration.world.read("archive"))
        journal.freeze_replace("target", [uri(9)])
        journal.resume()

    replace_parent(restoration, setup, targets=("archive", "target"))
    row = restoration.enqueue_restore()
    restoration.world.playlists["archive"].update(track_uris=[uri(88)], snapshot_id="external")
    finished, completed = restoration.run_restore(row)
    assert finished and completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "restore_review_changed"
    assert restoration.world.writes == []
    assert restoration.world.playlists["target"]["track_uris"] == [uri(9)]
    assert not restoration.repository.get(restoration.parent_id).journal.get("runtime", {}).get("restoration")


def test_metadata_only_restore_ignores_unsupported_tracks_and_preserves_unrelated_metadata(restoration):
    def setup(journal):
        journal.begin_step("privacy")
        journal.freeze_details("target", public=True)
        journal.resume()

    replace_parent(restoration, setup, metadata=True)
    restoration.world.playlists["target"]["metadata"].update(name="New unrelated name", description="Keep this")
    restoration.world.playlists["target"]["track_uris"] = ["unsupported item"]
    row = restoration.enqueue_restore()

    def factory(user):
        api = restoration.factory(user)
        api.get_playlist_items_raw = lambda *args, **kwargs: pytest.fail("Metadata restoration must not read items")
        return api

    finished, completed = restoration.run_restore(row, factory)
    assert finished and completed.state == "succeeded"
    assert restoration.world.playlists["target"]["metadata"] == {
        "name": "New unrelated name",
        "description": "Keep this",
        "public": False,
    }
    assert restoration.world.playlists["target"]["track_uris"] == ["unsupported item"]
    assert [write[2] for write in restoration.world.writes] == [{"public": False}]
    assert completed.journal["result"]["changed_position_count"] == 0


def test_track_and_metadata_restore_use_separate_acknowledged_phase_baselines(restoration):
    def setup(journal):
        journal.begin_step("current")
        journal.freeze_replace("target", [uri(9)])
        journal.freeze_details("target", public=True)
        journal.resume()

    replace_parent(restoration, setup)
    restoration.world.playlists["target"]["metadata"]["description"] = "Unrelated current description"
    finished, completed = restoration.run_restore(restoration.enqueue_restore())
    assert finished and completed.state == "succeeded"
    assert restoration.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
    assert restoration.world.playlists["target"]["metadata"]["public"] is False
    assert restoration.world.playlists["target"]["metadata"]["description"] == "Unrelated current description"
    assert [path for _, path, _ in restoration.world.writes] == ["/playlists/target/items", "/playlists/target"]


@pytest.mark.parametrize("failure", ["history", "locks", "activity"])
def test_local_publication_failures_preserve_provider_facts_and_do_not_resolve_parent(
    restoration, monkeypatch, failure
):
    def fail(*args, **kwargs):
        raise RuntimeError("private local failure")

    path = {
        "history": "shuffify.services.state_service.StateService.record_verified_change",
        "locks": "shuffify.services.operations.bookkeeping.OperationBookkeeping.reconcile_locks",
        "activity": "shuffify.services.operations.bookkeeping.OperationBookkeeping.activity",
    }[failure]
    monkeypatch.setattr(path, fail)
    finished, completed = restoration.run_restore(restoration.enqueue_restore())
    optional = failure == "activity"
    assert finished and completed.state == ("succeeded" if optional else "needs_attention")
    assert restoration.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
    assert len(restoration.world.writes) == 1
    assert completed.journal["result"]["changed_position_count"] == 3
    assert not completed.journal.get("compensation")
    resolved = restoration.repository.get(restoration.parent_id).journal.get("runtime", {}).get("restoration")
    assert bool(resolved) is optional
    assert ActivityLog.query.count() == 0


def test_conclusive_zero_dispatch_refusal_allows_a_new_reviewed_child(restoration):
    reviewed = restoration.review_restore()
    restoration.world.playlists["target"].update(track_uris=[uri(88)], snapshot_id="external")
    first = restoration.enqueue_restore(reviewed)
    assert restoration.run_restore(first)[1].state == "failed"
    second = restoration.enqueue_restore()
    assert second.id != first.id
    assert restoration.run_restore(second)[1].state == "succeeded"
    assert len(restoration.world.writes) == 1


def test_history_error_then_marker_outage_keeps_verified_restore_and_blocks_a_second_attempt(restoration, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from shuffify.services.operations.restoration import RestoreUnavailable
    from shuffify.services.operations.runtime import OperationRuntime
    from shuffify.services.state_service import StateService

    row, failed = restoration.enqueue_restore(), []
    checkpoint = OperationRuntime.checkpoint

    def record(*args, **kwargs):
        raise ValueError("private history record")

    def marker(self, document):
        if document.get("runtime", {}).get("history_unconfirmed"):
            failed.append(True)
            raise OperationalError("UPDATE marker", {}, Exception("private database"))
        return checkpoint(self, document)

    monkeypatch.setattr(StateService, "record_verified_change", record)
    monkeypatch.setattr(OperationRuntime, "checkpoint", marker)
    finished, completed = restoration.run_restore(row)
    assert failed and finished and completed.state == "needs_attention"
    assert restoration.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
    assert len(restoration.world.writes) == 1 and not completed.journal.get("compensation")
    result = completed.journal["result"]
    assert result["changed_position_count"] == 3 and result["parent_resolution"] == "unknown"
    assert "private" not in str(result) and ActivityLog.query.count() == 0
    with pytest.raises(RestoreUnavailable) as caught:
        restoration.enqueue_restore()
    assert caught.value.code == "restoration_attempt_unresolved" and caught.value.operation_id == row.id


def test_second_resource_refusal_rolls_back_only_this_child_before_any_parent_resolution(restoration):
    import requests

    def setup(journal):
        journal.begin_step("current")
        journal.freeze_replace("archive", [uri(88)])
        journal.freeze_replace("target", [uri(99)])
        journal.resume()

    replace_parent(restoration, setup, targets=("archive", "target"))
    reviewed_current = deepcopy(restoration.world.playlists)

    def factory(user):
        api = restoration.factory(user)
        request = api._http._session.request
        calls = []

        def reject(method, url, **kwargs):
            calls.append(url)
            if len(calls) == 2:
                response = requests.Response()
                response.status_code, response._content = 403, b"{}"
                return response
            return request(method, url, **kwargs)

        api._http._session.request = reject
        return api

    finished, completed = restoration.run_restore(restoration.enqueue_restore(), factory)
    assert finished and completed.state == "rolled_back"
    for resource in ("archive", "target"):
        assert restoration.world.playlists[resource]["track_uris"] == reviewed_current[resource]["track_uris"]
    assert completed.journal["result"]["changed_position_count"] == 0
    facts = completed.journal["result"]["resources"]
    assert {item["playlist_id"] for item in facts} == {"archive", "target"}
    assert all(item["current_track_uris"] == reviewed_current[item["playlist_id"]]["track_uris"] for item in facts)
    assert not restoration.repository.get(restoration.parent_id).journal.get("runtime", {}).get("restoration")
    assert ActivityLog.query.count() == 0


@pytest.mark.parametrize("control", ["deferred", "lease_lost"])
def test_typed_control_during_history_publication_never_compensates_or_repeats_restore(
    restoration, monkeypatch, control
):
    from datetime import UTC, datetime, timedelta

    from shuffify.services.operations.journal import OperationDeferred
    from shuffify.services.operations.repository import LeaseLost
    from shuffify.services.state_service import StateService

    row = restoration.enqueue_restore()

    def stop(*args, **kwargs):
        if control == "deferred":
            raise OperationDeferred(datetime.now(UTC) + timedelta(seconds=0.1))
        raise LeaseLost("lost claim")

    with monkeypatch.context() as patches:
        patches.setattr(StateService, "record_verified_change", stop)
        assert restoration.run_restore(row)[0] is False
    pending = restoration.repository.get(row.id)
    assert pending.state == "running" and not pending.journal.get("compensation")
    assert restoration.world.playlists["target"]["track_uris"] == [uri(1), uri(2), uri(3)]
    assert len(restoration.world.writes) == 1 and ActivityLog.query.count() == 0
    expire_lease(restoration.repository, row.id)
    finished, completed = restoration.run_restore(row)
    assert finished and completed.state == "succeeded"
    assert len(restoration.world.writes) == 1 and ActivityLog.query.count() == 1
