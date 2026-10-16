"""Routine admissions wake the idle worker and keep one execution identity over real HTTP."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from config import HarnessConfig
from tests.e2e.test_live_operation_outcomes import TARGET, _collection
from tests.e2e.test_queued_direct_operations import complete
from tests.routine_flow import create_routine, list_schedules, set_routine_enabled


def _executions():
    HarnessConfig.assert_safe()
    engine = create_engine(HarnessConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT id, kind, schedule_id, payload -> 'command' ->> 'triggered_by' AS triggered_by "
                "FROM job_executions ORDER BY id"
            )).mappings().all()
            return {row["id"]: dict(row) for row in rows}
    finally:
        engine.dispose()


def _execution(operation_id, previous):
    rows = _executions()
    assert rows.keys() - previous.keys() == {operation_id}, rows
    return rows[operation_id]


def _assert_result_identity(operation):
    result = operation["result"]
    assert result["execution_id"] == operation["id"]
    assert result["execution_state"] == "recorded"
    assert result["triggered_by"] == "manual"
    assert result["result_detail"]["execution_id"] == operation["id"]
    for index, step in enumerate(result["operation"]["steps"]):
        assert step["execution_id"] == operation["id"]
        assert step["step_index"] == index
    return result


@pytest.mark.parametrize("job_type", ["shuffle", "rotate", "raid", "drip", "raid_and_shuffle", "raid_and_drip"])
def test_saved_routine_uses_one_accepted_row_and_replays_its_key(alice, fake, job_type):
    collection = _collection(alice)
    alice.ok("put", f"/playlist/{TARGET}/raid-link", json={"drip_enabled": True, "drip_count": 2})
    pair = alice.write(f"/playlist/{TARGET}/pair", json={
        "create_new": True, "production_playlist_name": "Morning Mix",
    })
    assert pair["success"] is True
    before = fake.uris(TARGET)
    options = {"rotation_mode": "swap", "rotation_count": 2, "target_size": 10} if job_type == "rotate" else None
    schedule = create_routine(alice, TARGET, job_type=job_type, algorithm_params=options)
    schedule_id = schedule["id"]
    set_routine_enabled(alice, schedule_id, False)
    previous = _executions()
    try:
        operation = complete(alice, f"/api/schedules/{schedule_id}/run", key="saved-routine")
        result = _assert_result_identity(operation)
        assert result["result"]["execution_id"] == operation["id"]
        row = _execution(operation["id"], previous)
        assert (row["kind"], row["schedule_id"], row["triggered_by"]) == (
            "schedule_run", schedule_id, "manual",
        )
        history = alice.ok("get", f"/schedules/{schedule_id}/history")["history"]
        assert len(history) == 1
        assert history[0]["id"] == operation["id"]
        assert history[0]["result_detail"] == result["result_detail"]
        current = next(row for row in list_schedules(alice, TARGET)
                       if row["id"] == schedule_id)
        assert current["is_enabled"] is False
        if job_type == "raid":
            assert fake.uris(TARGET) == before
            assert len(fake.uris(collection)) == 5
        elif job_type in {"shuffle", "raid_and_shuffle"}:
            assert sorted(fake.uris(TARGET)) == sorted(before)
            assert fake.uris(TARGET) != before
        elif job_type == "raid_and_drip":
            assert len(fake.uris(TARGET)) == len(before) + 2
            assert len(fake.uris(collection)) == 3
    finally:
        alice.ok("delete", f"/schedules/{schedule_id}")


@pytest.mark.parametrize("action,kind", [
    ("raid-now", "raid_now"), ("drip-now", "drip_now"), ("raid-and-drip", "raid_and_drip"),
])
def test_explicit_action_keeps_one_standalone_execution(alice, fake, action, kind):
    collection = _collection(alice)
    before = fake.uris(TARGET)
    previous = _executions()
    operation = complete(alice, f"/api/playlist/{TARGET}/{action}", key="explicit-routine", json={})
    _assert_result_identity(operation)
    row = _execution(operation["id"], previous)
    assert (row["kind"], row["schedule_id"], row["triggered_by"]) == (kind, None, "manual")
    if action == "raid-now":
        assert fake.uris(TARGET) == before
        assert len(fake.uris(collection)) == 5
    elif action == "raid-and-drip":
        assert len(fake.uris(TARGET)) > len(before)
    else:
        assert fake.uris(TARGET) == before
        assert fake.uris(collection) == []
