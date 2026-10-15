"""Large libraries load only scoped receipts in one bounded query."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, User, db
from shuffify.services.operations.history import HistoryStore, get_history_scope
from tests.services.operations.test_repository import postgres_repository as postgres_repository
from tests.services.operations.test_repository import postgres_url as postgres_url


def test_library_history_batches_targets_and_selects_no_full_payload_or_journal(db_app):
    user = User(spotify_id="history-batch-owner")
    db.session.add(user)
    db.session.commit()
    user_id = user.id
    scope = get_history_scope({})
    now = datetime.now(timezone.utc)
    for offset, entries in enumerate((
        {"first": {"entry": {"states": [["old"], ["new"]], "current_index": 1}}},
        {"first": {"entry": None}, "second": {"entry": {"states": [["a"], ["b"]], "current_index": 1}}},
    )):
        db.session.add(JobExecution(kind="shuffle", user_id=user_id, targets=list(entries), state="succeeded",
                                    status="success", completed_at=now + timedelta(seconds=offset),
                                    payload={"private_large_command": ["private"] * 1000},
                                    journal={"private_large_baseline": ["private"] * 1000, "history": {
                                        "version": 1, "user_id": user_id, "scope": scope, "state": "succeeded",
                                        "confirmed": True, "entries": entries,
                                    }}))
    db.session.commit()
    statements = []
    selected = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
        selected.extend(_context.compiled.statement.selected_columns.keys())

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        mapping = HistoryStore().session_for(user_id, scope, ["first", "second"] + [f"p-{i}" for i in range(98)],
                                             legacy={"first": {"states": [["stale"]], "current_index": 0}})
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    projection = statements[0].split("FROM", 1)[0].lower()
    assert "payload" not in projection
    assert "journal" not in selected and "payload" not in selected
    assert "json_extract" in projection or " -> " in projection
    assert "first" not in mapping["playlist_states"]
    assert mapping["playlist_states"]["second"]["states"] == [["a"], ["b"]]
    assert mapping.revisions["first"] is not None
    assert mapping.revisions["second"] is not None
    assert mapping.revisions["p-0"] is None


@pytest.mark.integration
def test_postgres_projects_scoped_receipts_without_decoding_full_journals(postgres_repository):
    repository = postgres_repository
    store, scope = HistoryStore(repository.engine), get_history_scope({})
    with Session(repository.engine) as session, session.begin():
        row = JobExecution(kind="shuffle", user_id=1, targets=["target"], state="succeeded", status="success",
                           completed_at=datetime.now(timezone.utc), payload={"private": "command"}, journal={
                               "private": "baseline", "history": {
                                   "version": 1, "user_id": 1, "scope": scope, "state": "succeeded", "confirmed": True,
                                   "entries": {"target": {"entry": {"states": [["a"], ["b"]], "current_index": 1}}},
                               },
                           })
        session.add(row)
        session.flush()
        operation_id = row.id
    mapping = store.session_for(1, scope, ["target"] + [f"p-{index}" for index in range(99)])
    assert mapping["playlist_states"]["target"]["states"] == [["a"], ["b"]]
    assert mapping.revisions["target"] == operation_id
    assert store.session_for(1, get_history_scope({}), ["target"])["playlist_states"] == {}
