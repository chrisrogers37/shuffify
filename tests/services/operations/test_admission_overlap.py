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


@pytest.mark.parametrize("state", ["queued", "needs_attention"])
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
    assert type(blocked.value) is (BusyOperation if state == "queued" else PlaylistNeedsReview)
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
            dict(id=1, user_id=6, kind="shuffle", state="running", targets=["a", "b"]),
            dict(id=2, user_id=7, kind="shuffle", state="needs_attention", targets=["c", "d"]),
        ])
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        revision.upgrade()
    assert target_index() == fresh_index
    for target, expected, blocker in [("b", BusyOperation, (1, 6)), ("d", PlaylistNeedsReview, (2, 7))]:
        with pytest.raises(expected) as blocked:
            repo.enqueue(kind="shuffle", user_id=9, targets=["new", target], payload={})
        assert type(blocked.value) is expected
        assert (blocked.value.operation_id, blocked.value.owner_id) == blocker
