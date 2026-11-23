"""Exercise JSON reference lookups and the exact UTC cutoff against PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution
from shuffify.services.operations.history import HistoryStore
from tests.services.operations.test_repository import postgres_repository as postgres_repository


@pytest.mark.integration
def test_stale_pinned_revisions_and_restore_parents_survive_expiry(postgres_repository):
    from shuffify.services.operations.retention import cleanup_page

    engine = postgres_repository.engine
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=90)
    old = cutoff - timedelta(microseconds=1)
    scope = "a" * 32

    def terminal(document, completed_at=old):
        marker = {"state": "succeeded", "done": True}
        journal = {"version": 1, "runtime": {"terminal_finalization": marker}, **document}
        return JobExecution(user_id=7, kind="visibility", state="succeeded", status="success",
                            completed_at=completed_at, journal=journal)

    def history(playlist):
        return {"history": {"version": 1, "confirmed": True, "state": "succeeded", "user_id": 7, "scope": scope,
                            "entries": {playlist: {"entry": None}}}}

    with Session(engine) as storage, storage.begin():
        captured, staged, parent, at_cutoff = terminal(history("a")), terminal(history("b")), terminal({}), terminal({})
        at_cutoff.completed_at = cutoff
        storage.add_all([captured, staged, parent, at_cutoff])
        storage.flush()
        # A newer valid clear supersedes both old revisions. A pending worker still owns the old IDs.
        storage.add_all([terminal(history("a"), cutoff), terminal(history("b"), cutoff)])
        undo = JobExecution(user_id=7, kind="undo", state="running",
                            payload={"history": {"revisions": {"a": captured.id}}},
                            journal={"history": {"entries": {"b": {"parent_revision": staged.id}}}})
        child = JobExecution(user_id=7, kind="operation_restore", state="failed", completed_at=old,
                             payload={"command": {"capture": {"operation_id": parent.id}}})
        storage.add_all([undo, child])
        storage.flush()
        undo_id, parent_id, cutoff_id = undo.id, parent.id, at_cutoff.id
        revisions = [captured.id, staged.id]

    assert cleanup_page(engine, now=now)["compacted"] == 0
    with Session(engine) as storage, storage.begin():
        storage.get(JobExecution, undo_id).state = "failed"
    assert cleanup_page(engine, now=now)["compacted"] == 2
    assert cleanup_page(engine, now=now)["compacted"] == 0
    with Session(engine) as storage:
        assert all(storage.get(JobExecution, revision).journal == {} for revision in revisions)
        assert "runtime" in storage.get(JobExecution, parent_id).journal
        assert "runtime" in storage.get(JobExecution, cutoff_id).journal


@pytest.mark.integration
def test_cleanup_preserves_a_captured_revision_before_admission_insert(postgres_repository):
    from shuffify.services.operations.retention import cleanup_page

    repo, scope = postgres_repository, "b" * 32
    entry = {"entry": {"states": [["before"], ["after"]], "current_index": 1}}
    journal = {"version": 1, "runtime": {"terminal_finalization": {"state": "succeeded", "done": True}},
               "history": {"version": 1, "confirmed": True, "state": "succeeded", "user_id": 7,
                           "scope": scope, "entries": {"target": entry}}}
    with Session(repo.engine) as storage, storage.begin():
        old = JobExecution(user_id=7, kind="shuffle", state="succeeded", status="success", journal=journal,
                           completed_at=datetime.now(timezone.utc) - timedelta(days=91))
        storage.add(old)
        storage.flush()
        old_id = old.id
    newer = repo.enqueue(kind="shuffle", user_id=7, targets=["target"], payload={})
    lease = repo.claim(operation_id=newer.id)
    repo.checkpoint(lease, journal)

    captured, resume = Event(), Event()

    def capture(storage):
        binding = HistoryStore(repo.engine, session=storage).capture(7, scope, ["target"])
        assert binding["revisions"] == {"target": old_id}
        captured.set()
        assert resume.wait(10)
        return ["target"], {"history": binding}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(repo.enqueue, kind="undo", user_id=7, command_factory=capture, intent={"undo": "target"})
        try:
            assert captured.wait(5)
            repo.finish(lease, "succeeded")
            # The successor is visible, but the pending Undo's INSERT has not happened yet.
            during_admission = cleanup_page(repo.engine)
        finally:
            resume.set()
        undo = future.result()
    assert during_admission["compacted"] == 0 and repo.get(old_id).journal == journal
    assert cleanup_page(repo.engine)["compacted"] == 0  # Now the queued reference is visible.
    repo.finish(repo.claim(operation_id=undo.id), "failed")
    assert cleanup_page(repo.engine)["compacted"] == 1
