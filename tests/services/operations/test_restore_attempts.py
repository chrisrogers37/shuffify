"""A later restore never races an earlier provider intent or loses its owned parent."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution
from shuffify.services.operations.restore_attempts import conclusively_no_dispatch
from tests.services.operations.test_history import stores as stores


def refused():
    return SimpleNamespace(
        state="failed",
        completed_at=datetime.now(UTC),
        journal={
            "version": 1,
            "status": "failed",
            "plans": [],
            "runtime": {"terminal_finalization": {"state": "failed", "done": True}},
        },
    )


def test_only_a_finished_zero_intent_refusal_allows_a_later_child():
    assert conclusively_no_dispatch(refused())


@pytest.mark.parametrize(
    "damage",
    [
        "running",
        "unfinished",
        "wrong_version",
        "runtime_shape",
        "terminal_shape",
        "boundaries_shape",
        "accepted",
        "forward_dispatched",
        "compensation_dispatched",
        "acknowledged",
        "previous_attempt",
    ],
)
def test_terminal_status_never_excuses_an_attempted_or_unproved_child(damage):
    row = refused()
    batch = {"state": "prepared", "attempts": 0}
    plan = {"kind": "replace", "batches": [batch]}
    if damage == "running":
        row.state = "running"
    elif damage == "unfinished":
        row.completed_at = None
    elif damage == "wrong_version":
        row.journal["version"] = True
    elif damage == "runtime_shape":
        row.journal["runtime"] = None
    elif damage == "terminal_shape":
        row.journal["runtime"]["terminal_finalization"] = []
    elif damage == "boundaries_shape":
        row.journal["boundaries"] = []
    elif damage == "accepted":
        row.journal["boundaries"] = {"accepted": [{"name": "restore"}]}
    else:
        if damage == "previous_attempt":
            batch["attempts"] = 1
        else:
            batch.update(state="acknowledged" if damage == "acknowledged" else "dispatched", attempts=1)
        row.journal["compensation" if damage == "compensation_dispatched" else "plans"] = [plan]
    assert not conclusively_no_dispatch(row)


@pytest.mark.parametrize("state", ["queued", "running", "needs_attention", "rolled_back", "failed"])
def test_retention_protects_owned_parent_and_unresolved_child(stores, state):
    repository, history = stores
    with Session(repository.engine) as storage, storage.begin():
        parent = JobExecution(kind="shuffle", user_id=1, targets=["target"], state="needs_attention", journal={})
        storage.add(parent)
        storage.flush()
        child = JobExecution(
            kind="operation_restore",
            user_id=1,
            targets=["target"],
            state=state,
            journal={},
            payload={"command": {"capture": {"operation_id": parent.id}}},
        )
        storage.add(child)
        storage.flush()
        identities = {parent.id, child.id}
    assert identities.issubset(history.protected_operation_ids())


def test_retention_never_protects_a_foreign_guessed_parent_or_a_conclusive_refusal(stores):
    repository, history = stores
    with Session(repository.engine) as storage, storage.begin():
        foreign = JobExecution(kind="shuffle", user_id=2, targets=["target"], state="needs_attention", journal={})
        own = JobExecution(kind="shuffle", user_id=1, targets=["target"], state="needs_attention", journal={})
        storage.add_all([foreign, own])
        storage.flush()
        guessed = JobExecution(
            kind="operation_restore",
            user_id=1,
            targets=["target"],
            state="queued",
            journal={},
            payload={"command": {"capture": {"operation_id": foreign.id}}},
        )
        declined = JobExecution(
            kind="operation_restore",
            user_id=1,
            targets=["target"],
            state="failed",
            completed_at=datetime.now(UTC),
            journal=deepcopy(refused().journal),
            payload={"command": {"capture": {"operation_id": own.id}}},
        )
        storage.add_all([guessed, declined])
        storage.flush()
        foreign_id, own_id, guessed_id, declined_id = foreign.id, own.id, guessed.id, declined.id
    protected = history.protected_operation_ids()
    assert foreign_id not in protected and own_id not in protected
    assert guessed_id in protected and declined_id not in protected


@pytest.mark.parametrize("child_state,protected", [("succeeded", False), ("needs_attention", True)])
def test_confirmed_resolution_releases_retention_only_while_child_remains_confirmed(stores, child_state, protected):
    repository, history = stores
    with Session(repository.engine) as storage, storage.begin():
        parent = JobExecution(kind="shuffle", user_id=1, targets=["target"], state="needs_attention", journal={})
        storage.add(parent)
        storage.flush()
        child = JobExecution(
            kind="operation_restore",
            user_id=1,
            targets=["target"],
            state=child_state,
            journal={},
            payload={"command": {"capture": {"operation_id": parent.id}}},
        )
        storage.add(child)
        storage.flush()
        parent.journal = {"runtime": {"restoration": {"operation_id": child.id}}}
        parent_id, child_id = parent.id, child.id
    retained = history.protected_operation_ids()
    assert (parent_id in retained) is protected and (child_id in retained) is protected
