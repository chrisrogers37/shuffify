"""Verified provider effects cannot be rolled back by later local publication failures."""

# ruff: noqa: F811

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import OperationalError

from shuffify.models.db import ActivityLog
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from shuffify.services.operations.journal import OperationAborted, OperationDeferred
from shuffify.services.operations.repository import LeaseLost
from shuffify.services.operations.runtime import OperationRuntime
from tests.services.operations.test_composite_runtime import BEFORE, COLLECTED, PLAYLIST, composite  # noqa: F401


def outage():
    return OperationalError("private receipt statement", {}, Exception("private database detail"))


def child(runtime, *, after_verify=None):
    def apply():
        runtime.step("verified-write", lambda: {"success": runtime.api.playlist_add_items(PLAYLIST, [COLLECTED])})
        return {
            "result": {"success": True, "message": "Collected", "tracks_added": 1},
            "before": {PLAYLIST: {"track_uris": [BEFORE]}},
            "private": "must never be public",
        }

    def commit(storage, applied):
        item = OperationBookkeeping(storage, runtime.user.id, runtime.operation.id).activity("raid", "Collected")
        return {**applied, "receipt": {"activity_id": item["id"]}}

    options = {"after_verify": after_verify} if after_verify is not None else {}
    return runtime.composite_step("verified", apply, commit, **options)["result"]


@pytest.mark.parametrize("lost_ack", [False, True])
def test_acceptance_storage_failure_after_verification_retains_provider_effects(composite, monkeypatch, lost_ack):
    repository, operation_id, world, build, _expire = composite
    local = repository.local_step

    def fail(lease, name, callback, **options):
        if name == "accept.verified":
            if lost_ack:
                local(lease, name, callback, **options)
            raise outage()
        return local(lease, name, callback, **options)

    monkeypatch.setattr(repository, "local_step", fail)
    assert build(child).run_once(operation_id) is True
    row = repository.get(operation_id)
    assert row.state == ("succeeded" if lost_ack else "needs_attention")
    assert world.uris == [BEFORE, COLLECTED]
    assert world.calls == 1
    assert ActivityLog.query.count() == int(lost_ack)
    assert "private" not in str(row.journal["result"])
    assert "before" not in row.journal["result"]
    assert row.journal.get("compensation", []) == []


def test_after_verify_callback_follows_fresh_proof_and_cannot_mutate_frozen_facts(composite):
    repository, operation_id, world, build, _expire = composite
    captured = []

    def after(value):
        captured.append(deepcopy(value))
        value["result"]["message"] = "Mutated callback alias"
        value["private"] = "changed"

    def handler(runtime):
        real = runtime.journal.verify_step

        def verified(name, **kwargs):
            proof = real(name, **kwargs)
            captured.append("verified")
            return proof

        runtime.journal.verify_step = verified
        return child(runtime, after_verify=after)

    assert build(handler).run_once(operation_id) is True
    assert captured[0] == "verified"
    assert captured[1]["result"]["message"] == "Collected"
    assert repository.get(operation_id).journal["result"]["message"] == "Collected"
    assert world.calls == 1


def test_history_failure_then_marker_checkpoint_failure_stays_postverified(composite, monkeypatch):
    repository, operation_id, world, build, _expire = composite

    def marker(self):
        raise outage()

    monkeypatch.setattr(OperationRuntime, "mark_history_unconfirmed", marker)

    def history(value):
        try:
            raise ValueError("private history detail")
        except ValueError:
            runtime.mark_history_unconfirmed()

    def handler(current):
        nonlocal runtime
        runtime = current
        return child(current, after_verify=history)

    runtime = None
    assert build(handler).run_once(operation_id) is True
    assert repository.get(operation_id).state == "needs_attention"
    assert world.uris == [BEFORE, COLLECTED]
    assert world.calls == 1


@pytest.mark.parametrize("lost_ack", [False, True])
def test_unavailable_database_and_broken_domain_error_projection_remain_recoverable(
    composite,
    monkeypatch,
    lost_ack,
):
    repository, operation_id, world, build, expire = composite
    get, local = repository.get, repository.local_step
    interrupted = []

    def broken_get(*args, **kwargs):
        raise outage()

    def fail(lease, name, callback, **options):
        if name == "accept.verified" and not interrupted:
            interrupted.append(True)
            if lost_ack:
                local(lease, name, callback, **options)
            monkeypatch.setattr(repository, "get", broken_get)
            raise outage()
        return local(lease, name, callback, **options)

    monkeypatch.setattr(repository, "local_step", fail)

    def handler(runtime):
        try:
            return child(runtime)
        except Exception:
            # A domain's rich error projection must not erase the verified boundary
            # when its own journal read also fails.
            runtime.get_journal()
            raise

    assert build(handler).run_once(operation_id) is False
    monkeypatch.setattr(repository, "get", get)
    row = get(operation_id)
    assert row.state == "running"
    assert row.journal.get("runtime", {}).get("failure") is None
    assert row.journal.get("compensation", []) == []
    assert world.uris == [BEFORE, COLLECTED]
    assert world.calls == 1
    expire()
    assert build(handler).run_once(operation_id) is True
    assert get(operation_id).state == "succeeded"
    assert world.calls == 1
    assert ActivityLog.query.count() == 1


@pytest.mark.parametrize("control", ["lease", "defer", "abort"])
def test_postproof_controls_keep_their_direction_without_observer_database_reads(composite, monkeypatch, control):
    repository, operation_id, world, build, _expire = composite

    def after(value):
        signal = {
            "lease": LeaseLost("lost"),
            "defer": OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=10)),
            "abort": OperationAborted("example_abort"),
        }[control]
        try:
            raise signal
        except Exception as original:
            raise ValueError("ordinary wrapper") from original

    assert build(lambda runtime: child(runtime, after_verify=after)).run_once(operation_id) is (control == "abort")
    row = repository.get(operation_id)
    assert row.state == ("rolled_back" if control == "abort" else "running")
    assert world.uris == ([BEFORE] if control == "abort" else [BEFORE, COLLECTED])
    assert world.calls == (2 if control == "abort" else 1)


def test_secondary_observer_database_failure_cannot_mask_detached_verified_facts(composite, monkeypatch):
    from shuffify.services.operations.runtime import OperationResult, VerifiedStepError

    repository, operation_id, world, build, _expire = composite
    captured = []

    def handler(runtime):
        def after(applied):
            def broken_observer():
                raise outage()

            monkeypatch.setattr(runtime.journal, "check_pending_control", broken_observer)
            raise ValueError("private history projection failure")

        try:
            child(runtime, after_verify=after)
        except VerifiedStepError as error:
            assert type(error.__cause__) is ValueError
            assert error.step_name == "verified"
            assert error.applied["result"]["tracks_added"] == 1
            error.applied["result"]["tracks_added"] = 999
            assert runtime.verified_step_failure.applied["result"]["tracks_added"] == 1
            captured.append(True)
            return OperationResult(
                "needs_attention",
                {
                    "success": False,
                    "error_code": "history_confirmation_failed",
                    "tracks_added": 1,
                    "message": "Verified tracks retained; local history needs review.",
                },
            )

    assert build(handler).run_once(operation_id) is True
    assert captured == [True]
    row = repository.get(operation_id)
    assert row.state == "needs_attention"
    assert row.journal["result"]["tracks_added"] == 1
    assert "private" not in str(row.journal["result"])
    assert world.calls == 1


def test_a_swallowed_verified_failure_cannot_start_a_follower_or_publish_success(composite, monkeypatch):
    repository, operation_id, world, build, _expire = composite
    local = repository.local_step

    def fail(lease, name, callback, **options):
        if name == "accept.verified":
            raise outage()
        return local(lease, name, callback, **options)

    monkeypatch.setattr(repository, "local_step", fail)

    def handler(runtime):
        try:
            child(runtime)
        except Exception:
            pass
        with pytest.raises(Exception) as blocked:
            runtime.composite_step(
                "follower",
                lambda: pytest.fail("No follower after unaccepted verification"),
                lambda storage, applied: applied,
            )
        assert type(blocked.value).__name__ == "VerifiedStepError"
        return {"success": True, "message": "Incorrect success must be refused"}

    assert build(handler).run_once(operation_id) is True
    row = repository.get(operation_id)
    assert row.state == "needs_attention"
    assert row.journal["result"]["success"] is False
    assert world.calls == 1


@pytest.mark.parametrize("missing", ["receipt", "boundary", "matching_result", "matching_type"])
def test_lost_ack_requires_both_matching_durable_receipt_and_boundary(composite, monkeypatch, missing):
    repository, operation_id, world, build, _expire = composite
    original = repository.local_step

    def interrupted(lease, name, callback, **options):
        result = original(lease, name, callback, **options)
        if name == "accept.verified":
            document = repository.get(operation_id).journal
            if missing == "receipt":
                del document["runtime"]["local_steps"][name]
            elif missing == "boundary":
                document["boundaries"]["accepted"] = []
            elif missing == "matching_type":
                document["runtime"]["local_steps"][name]["result"]["tracks_added"] = True
            else:
                document["runtime"]["local_steps"][name] = {"result": "different receipt"}
            from sqlalchemy import update

            from shuffify.models.db import JobExecution

            # Core UPDATE deliberately persists even a bool/int-only JSON drift;
            # Python ORM equality considers True and 1 equal.
            with repository.engine.begin() as connection:
                connection.execute(update(JobExecution).where(JobExecution.id == operation_id).values(journal=document))
            raise outage()
        return result

    monkeypatch.setattr(repository, "local_step", interrupted)
    assert build(child).run_once(operation_id) is True
    row = repository.get(operation_id)
    assert row.state == "needs_attention"
    assert row.journal["result"]["error_code"] == "verified_step_publication_failed"
    assert world.uris == [BEFORE, COLLECTED]
    assert world.calls == 1
    assert ActivityLog.query.count() == 1


def test_proven_lost_ack_allows_a_follower_without_repeating_the_accepted_step(composite, monkeypatch):
    from tests.services.operations.test_composite_runtime import DRIPPED

    repository, operation_id, world, build, _expire = composite
    original, interrupted = repository.local_step, []

    def lost_reply(lease, name, callback, **options):
        result = original(lease, name, callback, **options)
        if name == "accept.verified" and not interrupted:
            interrupted.append(True)
            raise outage()
        return result

    monkeypatch.setattr(repository, "local_step", lost_reply)

    def handler(runtime):
        first = child(runtime)
        assert first["tracks_added"] == 1
        assert runtime.verified_step_failure is None
        return runtime.composite_step(
            "follower",
            lambda: runtime.step(
                "follower-write",
                lambda: {
                    "success": runtime.api.playlist_add_items(PLAYLIST, [DRIPPED]),
                },
            ),
            lambda storage, result: result,
        )

    assert build(handler).run_once(operation_id) is True
    assert repository.get(operation_id).state == "succeeded"
    assert world.uris == [BEFORE, COLLECTED, DRIPPED]
    assert world.calls == 2
    assert ActivityLog.query.count() == 1
