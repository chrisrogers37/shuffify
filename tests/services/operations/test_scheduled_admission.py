"""A scheduled fire has one immutable UTC queue identity and no Spotify request work."""

import importlib
from datetime import datetime, timedelta, timezone

import pytest

from shuffify.models.db import JobExecution, Schedule, User, db
from shuffify.services.operations.repository import OperationRepository

# Nine hours behind the real clock, on the minute. The code under test reads
# this clock but APScheduler's own loop reads the real one, so a slot due at NOW
# has already passed for APScheduler and the next daily slot has not.
NOW = (datetime.now(timezone.utc) - timedelta(hours=9)).replace(second=0, microsecond=0)


def module():
    return importlib.import_module("shuffify.services.scheduled_admission")


@pytest.fixture
def schedule(db_app):
    user = User(spotify_id="scheduler-owner")
    db.session.add(user)
    db.session.flush()
    row = Schedule(
        user_id=user.id,
        job_type="shuffle",
        target_playlist_id="target",
        algorithm_name="BasicShuffle",
        schedule_type="interval",
        schedule_value="daily",
        timezone="UTC",
        created_at=NOW - timedelta(days=2),
    )
    db.session.add(row)
    db.session.commit()
    return row


def submit(repository, row, due=NOW, **kwargs):
    return module().admit_scheduled(repository, row.id, module().build_cadence(row).fingerprint, due, **kwargs)


def test_fire_admits_existing_closed_command_without_auth_or_legacy_executor(schedule, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No provider or legacy executor inside admission")

    monkeypatch.setattr("shuffify.spotify.api.SpotifyAPI.__init__", forbidden)
    monkeypatch.setattr("shuffify.services.auth_service.AuthService._auth_manager", forbidden)
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    repository = OperationRepository()
    result = submit(repository, schedule)
    assert result.state == "admitted"
    row = repository.get(result.operation_id)
    assert row.kind == "schedule_run"
    assert row.scheduled_for == NOW
    assert row.targets == ["target"]
    assert row.payload["command"]["triggered_by"] == "scheduler"
    assert row.payload["command"]["definition"]["schedule_id"] == schedule.id
    assert JobExecution.query.count() == 1


def test_accepted_fire_replays_before_volatile_definition_capture(schedule, monkeypatch):
    repository = OperationRepository()
    first = submit(repository, schedule)
    schedule.algorithm_params = {"keep_first": 2}
    schedule.is_enabled = False
    db.session.commit()
    monkeypatch.setattr(module(), "capture_schedule", lambda *args, **kwargs: pytest.fail("Replay recaptured"))
    replay = submit(repository, schedule)
    assert replay.operation_id == first.operation_id
    assert JobExecution.query.count() == 1


@pytest.mark.parametrize(
    "change,code", [("paused", "schedule_disabled"), ("deleted", "schedule_missing"), ("cadence", "cadence_changed")]
)
def test_actual_fire_freshly_rechecks_owned_enabled_timing(schedule, change, code):
    repository = OperationRepository()
    expected, schedule_id = module().build_cadence(schedule).fingerprint, schedule.id
    if change == "paused":
        schedule.is_enabled = False
    elif change == "deleted":
        db.session.delete(schedule)
    else:
        schedule.schedule_value = "every_6h"
    db.session.commit()
    result = module().admit_scheduled(repository, schedule_id, expected, NOW)
    assert result.state == "skipped" and result.code == code
    assert JobExecution.query.count() == 0


def test_busy_fire_is_authored_skip_without_new_row_or_new_now_identity(schedule):
    repository = OperationRepository()
    first = submit(repository, schedule)
    second = submit(repository, schedule, NOW + timedelta(days=1))
    assert second.state == "skipped" and second.code == "busy"
    assert second.blocking_operation_id == first.operation_id
    assert second.scheduled_for == NOW + timedelta(days=1)
    assert JobExecution.query.count() == 1


def test_nonleader_never_reads_captures_or_admits(schedule, monkeypatch):
    repository = OperationRepository()
    expected = module().build_cadence(schedule).fingerprint
    monkeypatch.setattr(module(), "Session", lambda *args, **kwargs: pytest.fail("Old leader read database"))
    result = module().admit_scheduled(repository, schedule.id, expected, NOW, is_leader=lambda: False)
    assert result.code == "leadership_lost"
    assert JobExecution.query.count() == 0


def test_leadership_loss_inside_factory_prevents_commit(schedule):
    repository = OperationRepository()
    calls = []

    def leader():
        calls.append(True)
        return len(calls) < 3

    result = submit(repository, schedule, is_leader=leader)
    assert result.state == "skipped" and result.code == "leadership_lost"
    assert JobExecution.query.count() == 0


@pytest.mark.parametrize("due", [NOW.replace(tzinfo=None), "2026-10-15", True])
def test_naive_or_non_datetime_fire_identity_is_rejected(schedule, due):
    with pytest.raises(ValueError):
        submit(OperationRepository(), schedule, due)


def test_non_utc_due_is_canonicalized_without_losing_microseconds(schedule):
    due = NOW.replace(microsecond=123456).astimezone(timezone(timedelta(hours=-4)))
    result = submit(OperationRepository(), schedule, due)
    assert result.scheduled_for == NOW.replace(microsecond=123456)
    assert result.scheduled_for.tzinfo == timezone.utc


def test_cadence_uses_created_anchor_and_ignores_execution_bookkeeping(schedule):
    first = module().build_cadence(schedule)
    schedule.updated_at = NOW + timedelta(days=10)
    schedule.last_run_at = NOW
    schedule.last_status = "success"
    assert module().build_cadence(schedule).fingerprint == first.fingerprint
    assert first.trigger.start_date == schedule.created_at
    assert first.trigger.get_next_fire_time(None, NOW + timedelta(microseconds=1)) == NOW + timedelta(days=1)


def test_dst_fall_back_due_instants_are_distinct_utc_keys(schedule):
    schedule.schedule_type = "cron"
    schedule.schedule_value = "30 1 * * *"
    schedule.timezone = "America/New_York"
    cadence = module().build_cadence(schedule)
    first = cadence.trigger.get_next_fire_time(None, datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc))
    second = cadence.trigger.get_next_fire_time(first, first + timedelta(microseconds=1))
    assert module().utc(first) == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    assert module().utc(second) == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    assert module().utc(first) != module().utc(second)


def test_worker_default_timezone_is_explicit_only_for_null_stored_zone(schedule):
    schedule.timezone = None
    assert module().build_cadence(schedule).timezone == "UTC"
    assert module().build_cadence(schedule, "America/New_York").timezone == "America/New_York"
    schedule.timezone = "Europe/London"
    assert module().build_cadence(schedule, "America/New_York").timezone == "Europe/London"


def test_recreated_cron_incarnation_cannot_consume_an_old_registration_fire(schedule):
    schedule.schedule_type = "cron"
    schedule.schedule_value = "0 1 * * *"
    db.session.commit()
    expected = module().build_cadence(schedule).fingerprint
    schedule.created_at = NOW
    db.session.commit()
    result = module().admit_scheduled(OperationRepository(), schedule.id, expected, NOW)
    assert result.code == "cadence_changed"
    assert JobExecution.query.count() == 0
