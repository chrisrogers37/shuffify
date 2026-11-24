"""Admission should fetch only execution rows that share an incoming playlist."""

from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event, inspect

from shuffify.models.db import JobExecution
from shuffify.services.operations.repository import BusyOperation, PlaylistNeedsReview
from tests.services.operations.test_repository import (
    postgres_repository as postgres_repository,
)
from tests.services.operations.test_repository import (
    repository as repository,
)


@pytest.fixture(params=[
    "repository", pytest.param("postgres_repository", marks=pytest.mark.integration),
])
def repo(request):
    return request.getfixturevalue(request.param)


@pytest.mark.parametrize("state", ["running", "needs_attention"])
def test_conflict_queries_return_only_overlapping_executions(repo, state):
    noise = [
        dict(id=i, user_id=i, kind="shuffle", state=status, targets=[f"unrelated-{i}"])
        for i, status in enumerate(["running", "needs_attention"] * 128, start=1)
    ]
    # An older attention row must lose to an active row. For a restore this
    # same row is its exempt parent, so the next unresolved match must win.
    matches = [
        dict(id=301, user_id=1, kind="shuffle", state="needs_attention", targets=["shared"]),
        dict(id=302, user_id=2, kind="shuffle", state=state, targets=["other", "shared"]),
        dict(id=303, user_id=3, kind="shuffle", state=state, targets=["shared", "third"]),
    ]
    for row in noise + matches:
        row["journal"] = {"plans": [{"resource": row["targets"][-1], "batches": [{"last_http_status": 201}]}]}
    with repo.engine.begin() as conn:
        conn.execute(JobExecution.__table__.insert(), noise + matches)

    selects = []

    def record_select(conn, cursor, sql, params, context, executemany):
        if sql.startswith("SELECT") and "job_executions" in sql:
            selects.append((sql, params))

    event.listen(repo.engine, "before_cursor_execute", record_select)
    try:
        with pytest.raises(BusyOperation) as blocked:
            repo.enqueue(kind="operation_restore", user_id=None, targets=["new", "shared"],
                         payload={"command": {"capture": {"operation_id": 301}}})
    finally:
        event.remove(repo.engine, "before_cursor_execute", record_select)
    assert type(blocked.value) is (BusyOperation if state == "running" else PlaylistNeedsReview)
    assert (blocked.value.operation_id, blocked.value.owner_id) == (302, 2)

    # Replay the actual SELECTs against the same rows. This measures what the
    # database returns even if the repository projects columns instead of ORM objects.
    with repo.engine.connect() as conn:
        returned = [row.id for sql, params in selects for row in conn.exec_driver_sql(sql, params)]
    assert 302 in returned
    assert set(returned) <= {301, 302, 303}, f"conflict queries returned {len(returned)} executions"


def test_upgrade_preserves_existing_blockers(repo):
    migrations = Path(__file__).resolve().parents[3] / "migrations"
    revision = ScriptDirectory(str(migrations)).get_revision("948c1d4ea673").module
    engine = repo.engine
    schema = engine.get_execution_options().get("schema_translate_map", {}).get(None)

    def target_index():
        indexes = inspect(engine).get_indexes("job_executions", schema=schema)
        return next((i for i in indexes if i["name"] == "ix_job_executions_blocking_targets"), None)

    # New installs use model DDL. Start from the previous schema and compare
    # the upgraded index with the one a fresh install received.
    fresh_index = target_index()
    assert (fresh_index is not None) == (engine.dialect.name == "postgresql")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        revision.downgrade()
    assert target_index() is None
    with engine.begin() as conn:
        conn.execute(JobExecution.__table__.insert(), [
            dict(id=1, user_id=6, kind="shuffle", state="running", targets=["a", "b"],
                 journal={"plans": [{"resource": "b", "batches": [{"last_http_status": 200}]}]}),
            dict(id=2, user_id=7, kind="shuffle", state="needs_attention", targets=["c", "d"],
                 journal={"plans": [{"resource": "d", "batches": [{"last_http_status": 200}]}]}),
        ])
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        revision.upgrade()
    assert target_index() == fresh_index
    for target, expected, blocker in [("b", BusyOperation, (1, 6)), ("d", PlaylistNeedsReview, (2, 7))]:
        with pytest.raises(expected) as blocked:
            repo.enqueue(kind="shuffle", user_id=9, targets=["new", target], payload={})
        assert type(blocked.value) is expected
        assert (blocked.value.operation_id, blocked.value.owner_id) == blocker


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("phase", ["plans", "compensation"])
def test_only_actual_write_responses_reserve_their_own_shared_target(repo, phase, terminal):
    active = repo.enqueue(kind="example", user_id=1, targets=["written", "other"], payload={})
    lease = repo.claim(operation_id=active.id)
    repo.checkpoint(lease, {phase: [
        {"resource": "written", "batches": [{"last_http_status": 204}]},
        {"resource": "other", "batches": [{"state": "acknowledged", "inferred": True}, {"last_http_status": 403}]},
        # The command created this playlist; it was never one of the reserved targets.
        {"resource": "created", "batches": [{"last_http_status": 200}]},
    ]}, state="running" if phase == "plans" else "compensating")
    if terminal:
        repo.finish(lease, "needs_attention")
    with pytest.raises(PlaylistNeedsReview if terminal else BusyOperation) as blocked:
        repo.enqueue(kind="example", user_id=2, targets=["written"], payload={})
    assert blocked.value.operation_id == active.id
    assert repo.enqueue(kind="example", user_id=2, targets=["other", "created"], payload={}).state == "queued"


def test_concurrent_acknowledgements_preserve_each_target_and_reject_stale_reviews(repo, app):
    from tests.services.operations.test_repository import race

    targets = ["first", "second"]
    plans = [{"resource": target, "batches": [{"last_http_status": 200}]} for target in targets]
    with repo.engine.begin() as conn:
        conn.execute(JobExecution.__table__.insert(), [
            dict(id=i, user_id=1, kind="example", state="needs_attention", targets=targets, journal={"plans": plans})
            for i in (1, 2)
        ])
    with app.app_context():
        tokens = [repo.attention_review(2, target, "snap") for target in targets]
        assert not repo.attention_review(3, targets[0], "snap", confirmation=tokens[0])
        assert not repo.attention_review(2, targets[1], "snap", confirmation=tokens[0])

    def acknowledge(i):
        with app.app_context():
            return repo.attention_review(2, targets[i], "snap", confirmation=tokens[i])

    assert race(acknowledge) == [True, True]
    with app.app_context():
        for i, target in enumerate(targets):
            assert not repo.attention_review(2, target, "snap", confirmation=tokens[i])
            assert repo.attention_review(2, target, "snap") != tokens[i]
    assert repo.get(1).journal == {
        "plans": plans, "runtime": {"acknowledgements": {
            target: {"snapshot_id": "snap", "user_id": 2} for target in targets
        }},
    }
    assert repo.get(2).journal == {"plans": plans}


def test_releasing_one_hold_preserves_the_same_owners_other_targets(repo, app):
    parent = repo.enqueue(kind="example", user_id=1, targets=["left", "right"], payload={})
    repo.finish(repo.claim(operation_id=parent.id), "needs_attention")
    with app.app_context():
        # An existing row without write evidence is only visible to its owner.
        assert repo.attention_review(2, "right", "snap") is None
        token = repo.attention_review(1, "left", "snap")
        assert repo.attention_review(1, "left", "snap", confirmation=token)
    assert repo.enqueue(kind="example", user_id=1, targets=["left"], payload={}).state == "queued"
    with pytest.raises(PlaylistNeedsReview) as blocked:
        repo.enqueue(kind="example", user_id=1, targets=["right"], payload={})
    assert blocked.value.playlist_id == "right"
