"""Queue admission and lease fencing use independent database transactions."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from shuffify.models.db import JobExecution, Schedule, User, db
from shuffify.services.operations.repository import (
    BusyOperation,
    LeaseLost,
    OperationRepository,
    PlaylistNeedsReview,
    RequestKeyConflict,
)
from shuffify.services.playlist_lock import playlist_lock


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'operations.sqlite'}", connect_args={"check_same_thread": False})
    db.metadata.create_all(engine)
    yield OperationRepository(engine)
    engine.dispose()


def enqueue(repository, *, target="example-target", key="example-key", payload=None):
    return repository.enqueue(kind="example", user_id=1, targets=[target],
                              payload=payload or {"version": 1, "value": "example"}, request_key=key)


def test_replaying_an_identical_command_returns_the_one_row(repository):
    first = enqueue(repository)
    second = enqueue(repository, payload={"value": "example", "version": 1})
    assert first.id == second.id
    assert first.state == "queued"
    with Session(repository.engine) as session:
        assert len(session.scalars(select(JobExecution)).all()) == 1


def test_request_key_is_bound_to_kind_payload_and_targets(repository):
    enqueue(repository)
    with pytest.raises(RequestKeyConflict):
        enqueue(repository, payload={"value": "changed"})
    with pytest.raises(RequestKeyConflict):
        enqueue(repository, target="different-target")
    with pytest.raises(RequestKeyConflict):
        repository.enqueue(kind="different", user_id=1, targets=["example-target"], payload={"version": 1},
                           request_key="example-key")


@pytest.mark.parametrize("value", [True, 1.0])
def test_idempotency_distinguishes_json_scalar_types(repository, value):
    enqueue(repository, payload={"value": 1})
    with pytest.raises(RequestKeyConflict):
        enqueue(repository, payload={"value": value})


def test_busy_reservations_include_queued_and_shared_targets(repository):
    first = repository.enqueue(kind="example", user_id=1, targets=["target", "shared"], payload={}, request_key="first")
    with pytest.raises(BusyOperation) as error:
        repository.enqueue(kind="example", user_id=2, targets=["other", "shared"], payload={}, request_key="second")
    assert error.value.operation_id == first.id
    lease = repository.claim()
    with pytest.raises(BusyOperation):
        enqueue(repository, target="target", key="third")
    repository.checkpoint(lease, {"phase": "compensating"}, state="compensating")
    with pytest.raises(BusyOperation):
        enqueue(repository, target="target", key="fourth")
    repository.finish(lease, "rolled_back")
    assert enqueue(repository, target="target", key="fifth").id != first.id


def needs_attention(repository, *, target="target", key="attention"):
    operation = enqueue(repository, target=target, key=key)
    lease = repository.claim(operation_id=operation.id)
    repository.finish(lease, "needs_attention")
    return operation


def restore_payload(parent_id):
    return {"version": 1, "command": {"capture": {"operation_id": parent_id}}}


def test_a_manual_operation_is_refused_on_a_playlist_with_an_unresolved_needs_attention_run(repository):
    parent = needs_attention(repository)
    with pytest.raises(PlaylistNeedsReview) as error:
        enqueue(repository, target="target", key="next")
    assert isinstance(error.value, BusyOperation)
    assert error.value.operation_id == parent.id
    with Session(repository.engine) as session:
        assert len(session.scalars(select(JobExecution)).all()) == 1


def test_a_shared_target_is_enough_to_be_refused(repository):
    parent = repository.enqueue(kind="example", user_id=1, targets=["a", "b"], payload={}, request_key="first")
    repository.finish(repository.claim(operation_id=parent.id), "needs_attention")
    with pytest.raises(PlaylistNeedsReview) as error:
        repository.enqueue(kind="example", user_id=2, targets=["b", "c"], payload={}, request_key="second")
    assert error.value.operation_id == parent.id


def test_a_different_playlist_is_admitted_beside_a_needs_attention_run(repository):
    needs_attention(repository, target="target")
    assert enqueue(repository, target="elsewhere", key="other").state == "queued"


def test_the_restore_of_the_needs_attention_parent_is_admitted(repository):
    parent = needs_attention(repository)
    restore = repository.enqueue(kind="operation_restore", user_id=1, targets=["target"],
                                 payload=restore_payload(parent.id), request_key="restore")
    assert restore.state == "queued"


def test_a_restore_of_another_run_does_not_bypass_the_block(repository):
    parent = needs_attention(repository)
    with pytest.raises(PlaylistNeedsReview) as error:
        repository.enqueue(kind="operation_restore", user_id=1, targets=["target"],
                           payload=restore_payload(parent.id + 100), request_key="restore")
    assert error.value.operation_id == parent.id


def test_a_second_needs_attention_run_still_blocks_the_restore_of_the_first(repository):
    first = needs_attention(repository, key="first")
    with Session(repository.engine) as session, session.begin():
        session.add(JobExecution(kind="example", user_id=1, targets=["target"], payload={}, state="needs_attention",
                                 status="failed", journal={}))
    with pytest.raises(PlaylistNeedsReview):
        repository.enqueue(kind="operation_restore", user_id=1, targets=["target"],
                           payload=restore_payload(first.id), request_key="restore")


def test_an_active_restore_blocks_as_busy_and_a_restored_parent_releases_the_playlist(repository):
    parent = needs_attention(repository)
    restore = repository.enqueue(kind="operation_restore", user_id=1, targets=["target"],
                                 payload=restore_payload(parent.id), request_key="restore")
    with pytest.raises(BusyOperation) as error:
        enqueue(repository, target="target", key="during")
    assert not isinstance(error.value, PlaylistNeedsReview) and error.value.operation_id == restore.id
    lease = repository.claim(operation_id=restore.id)
    repository.finish(lease, "succeeded")
    with pytest.raises(PlaylistNeedsReview):
        enqueue(repository, target="target", key="before-marker")
    with Session(repository.engine) as session, session.begin():
        row = session.get(JobExecution, parent.id)
        row.journal = {"runtime": {"restoration": {"operation_id": restore.id}}}
    assert enqueue(repository, target="target", key="after").state == "queued"


def test_a_parent_the_operator_marks_failed_releases_the_playlist(repository):
    parent = needs_attention(repository)
    with Session(repository.engine) as session, session.begin():
        session.get(JobExecution, parent.id).state = "failed"
    assert enqueue(repository, target="target", key="after").state == "queued"


def test_a_failed_restore_child_that_needs_attention_keeps_the_playlist_blocked(repository):
    parent = needs_attention(repository)
    child = repository.enqueue(kind="operation_restore", user_id=1, targets=["target"],
                               payload=restore_payload(parent.id), request_key="restore")
    repository.finish(repository.claim(operation_id=child.id), "needs_attention")
    with pytest.raises(PlaylistNeedsReview):
        enqueue(repository, target="target", key="next")


def test_targets_are_sorted_and_copied_and_json_is_detached(repository):
    payload = {"nested": ["example"]}
    operation = repository.enqueue(kind="example", user_id=1, targets=["z", "a", "z"], payload=payload)
    payload["nested"].append("changed")
    operation.payload["nested"].append("also changed")
    assert repository.get(operation.id).targets == ["a", "z"]
    assert repository.get(operation.id).payload == {"nested": ["example"]}


def test_legacy_rows_and_future_deferred_rows_are_not_claimed(repository):
    with Session(repository.engine) as session, session.begin():
        session.add(JobExecution(status="running"))
        session.add(JobExecution(state="queued", kind=None, targets=[]))
    operation = enqueue(repository)
    lease = repository.claim()
    assert lease.operation_id == operation.id
    deferred = repository.defer(lease, delay_seconds=30)
    assert deferred.state == "queued"
    assert repository.claim() is None
    with pytest.raises(LeaseLost):
        repository.renew(lease)


def test_recovery_uses_a_fresh_token_and_fences_every_old_write(repository):
    operation = enqueue(repository)
    first = repository.claim()
    assert first.recovering is False
    with Session(repository.engine) as session, session.begin():
        session.execute(update(JobExecution).where(JobExecution.id == operation.id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        ))
    with pytest.raises(LeaseLost):
        repository.checkpoint(first, {"unsafe": True})
    second = repository.claim()
    assert second.operation_id == first.operation_id
    assert second.owner != first.owner
    assert second.recovering is True
    for action in (lambda: repository.renew(first), lambda: repository.checkpoint(first, {}),
                   lambda: repository.finish(first, "failed"), lambda: repository.defer(first)):
        with pytest.raises(LeaseLost):
            action()
    assert repository.checkpoint(second, {"version": 1}).journal == {"version": 1}


def test_checkpoint_replaces_json_without_clobbering_lease_fields(repository):
    operation = enqueue(repository)
    lease = repository.claim()
    journal = {"version": 1, "nested": ["first"]}
    repository.checkpoint(lease, journal)
    journal["nested"].append("caller mutation")
    updated = repository.checkpoint(lease, {"version": 1, "progress": {"batches_done": 2}})
    assert updated.journal == {"version": 1, "progress": {"batches_done": 2}}
    assert updated.lease_owner == lease.owner
    assert repository.renew(lease).lease_owner == lease.owner
    assert repository.get(operation.id).state == "running"


@pytest.mark.parametrize("state,status", [("succeeded", "success"), ("failed", "failed"),
                                          ("rolled_back", "failed_rolled_back"), ("needs_attention", "failed")])
def test_terminal_results_preserve_journal_and_release_the_claim(repository, state, status):
    operation = enqueue(repository)
    lease = repository.claim()
    repository.checkpoint(lease, {"version": 1, "choices": {"example": True}})
    result = {"success": state == "succeeded", "message": "Example outcome"}
    finished = repository.finish(lease, state, result=result, tracks_added=2, tracks_total=3)
    assert finished.journal == {"version": 1, "choices": {"example": True}, "result": result}
    assert finished.status == status
    assert finished.completed_at is not None
    assert finished.lease_owner is None
    assert finished.lease_expires_at is None
    assert finished.tracks_added == 2 and finished.tracks_total == 3
    assert repository.get_owned(1, operation.id)["result"] == result
    with pytest.raises(LeaseLost):
        repository.renew(lease)


def test_public_lookup_never_exposes_private_journal_payload_or_claim(repository):
    operation = enqueue(repository, payload={"refresh_token": "private-token", "baseline": ["private-order"]})
    assert repository.get_owned(2, operation.id) is None
    assert repository.get_owned(1, operation.id)["started_at"] is None
    lease = repository.claim()
    repository.checkpoint(lease, {"baseline": ["private-order"],
                                  "progress": {"batches_done": 1, "total_batches": 2, "phase": "forward",
                                               "private": "private-progress"},
                                  "recovery": {"available": True, "action": "restore", "reason": "external_edit",
                                               "baseline": ["private-order"]}})
    public = repository.get_owned(1, operation.id)
    assert public["progress"] == {"batches_done": 1, "total_batches": 2, "phase": "forward"}
    assert public["recovery"] == {"available": False, "action": None, "reason": "external_edit"}
    assert "private" not in json.dumps(public)
    repository.finish(lease, "failed", result={"message": "Example", "access_token": "private-token",
                                              "nested": {"lease_owner": "private-claim"}})
    assert "private" not in json.dumps(repository.get_owned(1, operation.id))


def test_deferred_recovery_retains_its_compensation_state(repository):
    operation = enqueue(repository)
    lease = repository.claim()
    repository.checkpoint(lease, {"phase": "compensating"}, state="compensating")
    with Session(repository.engine) as session, session.begin():
        session.execute(update(JobExecution).where(JobExecution.id == operation.id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        ))
    recovered = repository.claim()
    deferred = repository.defer(recovered, delay_seconds=30)
    assert deferred.state == "compensating"
    assert deferred.lease_owner is None
    assert repository.claim() is None


def test_next_ready_at_returns_only_the_earliest_known_active_deadline(repository):
    assert repository.next_ready_at() is None
    first = enqueue(repository)
    second = enqueue(repository, target="other", key="other")
    assert repository.next_ready_at() is None
    first_lease = repository.claim(operation_id=first.id, lease_seconds=30)
    repository.claim(operation_id=second.id, lease_seconds=60)
    first_deadline = repository.get(first.id).lease_expires_at
    assert repository.next_ready_at() == first_deadline
    repository.finish(first_lease, "succeeded")
    assert repository.next_ready_at() == repository.get(second.id).lease_expires_at


def test_target_locks_are_reentrant_with_existing_domain_service_locks(repository):
    with repository.target_locks(["a", "z"], timeout_s=0) as acquired:
        assert acquired is True
        with playlist_lock("a", timeout_s=0) as nested:
            assert nested is True
        with repository.target_locks(["z"], timeout_s=0) as nested:
            assert nested is True
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(try_lock, repository, ["a"]).result(timeout=5) is False
    assert try_lock(repository, ["a"]) is True


def test_result_detail_storage_uses_existing_bounded_projection(repository):
    operation = enqueue(repository)
    lease = repository.claim()
    detail = {"version": 1, "operation": "shuffle", "execution_id": operation.id, "outcome": "complete",
              "steps": [{"step": "shuffle", "status": "complete"}], "private_baseline": ["private-order"]}
    finished = repository.finish(lease, "succeeded", result={"result_detail": detail})
    assert finished.result_json["execution_id"] == operation.id
    assert "private_baseline" not in finished.result_json


@pytest.fixture
def postgres_repository(postgres_url):
    schema = "queue_test_" + uuid4().hex
    admin = create_engine(postgres_url)
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(postgres_url, execution_options={"schema_translate_map": {None: schema}})
    db.metadata.create_all(engine)
    try:
        yield OperationRepository(engine)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def race(function, count=2):
    barrier = Barrier(count)

    def run(index):
        barrier.wait(timeout=10)
        try:
            return function(index)
        except (BusyOperation, RequestKeyConflict) as error:
            return error

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


def try_lock(repository, targets):
    with repository.target_locks(targets, timeout_s=0) as acquired:
        return acquired


@pytest.mark.integration
def test_postgres_concurrent_idempotency_and_overlapping_admission(postgres_repository):
    repository = postgres_repository
    replayed = race(lambda _: enqueue(repository))
    assert replayed[0].id == replayed[1].id
    outcomes = race(lambda index: repository.enqueue(kind="example", user_id=1, targets=["shared", str(index)],
                                                     payload={}, request_key=f"different-{index}"))
    assert sum(isinstance(result, BusyOperation) for result in outcomes) == 1
    conflicts = race(lambda index: repository.enqueue(kind="example", user_id=1, targets=[f"different-{index}"],
                                                      payload={"value": index}, request_key="same-key"))
    assert sum(isinstance(result, RequestKeyConflict) for result in conflicts) == 1


@pytest.mark.integration
def test_postgres_scheduled_fire_replay_is_atomic(postgres_repository):
    repository = postgres_repository
    with Session(repository.engine) as session, session.begin():
        user = User(spotify_id="scheduled-user")
        session.add(user)
        session.flush()
        schedule = Schedule(user_id=user.id, job_type="shuffle", target_playlist_id="scheduled-target")
        session.add(schedule)
        session.flush()
        user_id, schedule_id = user.id, schedule.id
    scheduled_for = datetime.now(timezone.utc)

    def submit(_):
        return repository.enqueue(kind="scheduled_shuffle", user_id=user_id, targets=["scheduled-target"],
                                  payload={"schedule_id": schedule_id}, schedule_id=schedule_id,
                                  scheduled_for=scheduled_for)

    outcomes = race(submit)
    assert outcomes[0].id == outcomes[1].id
    with pytest.raises(RequestKeyConflict):
        repository.enqueue(kind="scheduled_shuffle", user_id=user_id, targets=["scheduled-target"],
                           payload={"different": True}, schedule_id=schedule_id, scheduled_for=scheduled_for)


@pytest.mark.integration
def test_postgres_skip_locked_claim_and_fencing(postgres_repository):
    repository = postgres_repository
    first = enqueue(repository)
    second = enqueue(repository, target="other", key="other")
    with Session(repository.engine) as held, held.begin():
        held.scalar(select(JobExecution).where(JobExecution.id == first.id).with_for_update())
        claimed = repository.claim()
        assert claimed.operation_id == second.id
    claimed_first = repository.claim()
    assert claimed_first.operation_id == first.id
    with repository.engine.begin() as connection:
        connection.execute(update(JobExecution).where(JobExecution.id == first.id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        ))
    recovered = repository.claim(operation_id=first.id)
    assert recovered.recovering is True and recovered.owner != claimed_first.owner
    with pytest.raises(LeaseLost):
        repository.finish(claimed_first, "succeeded")
    assert repository.finish(recovered, "succeeded").state == "succeeded"


@pytest.mark.integration
def test_postgres_simultaneous_claims_never_share_one_operation(postgres_repository):
    operation = enqueue(postgres_repository)
    claimed = race(lambda _: postgres_repository.claim())
    assert sum(lease is not None for lease in claimed) == 1
    assert next(lease.operation_id for lease in claimed if lease is not None) == operation.id


@pytest.mark.integration
def test_postgres_target_lock_lives_across_transactions_and_blocks_recovery_dispatch(postgres_repository):
    repository = postgres_repository
    with repository.target_locks(["z", "a"], timeout_s=0.1) as acquired:
        assert acquired is True
        with Session(repository.engine) as session:
            assert session.scalar(select(text("1"))) == 1
        with repository.target_locks(["a"], timeout_s=0) as nested:
            assert nested is True
        with playlist_lock("a", timeout_s=0) as nested:
            assert nested is True
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(try_lock, repository, ["a"]).result(timeout=5) is False
    with repository.target_locks(["a"], timeout_s=0) as released:
        assert released is True


@pytest.mark.integration
def test_postgres_partial_target_lock_failure_releases_already_acquired_locks(postgres_repository):
    repository = postgres_repository
    with repository.target_locks(["z"], timeout_s=0) as acquired:
        assert acquired is True
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(try_lock, repository, ["a", "z"]).result(timeout=5) is False
            assert executor.submit(try_lock, repository, ["a"]).result(timeout=5) is True
