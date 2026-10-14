"""Contract checks for reviewed exact-ID routine activation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from unittest.mock import patch

import pytest
from flask import current_app

from shuffify.models.db import RoutineActivationReceipt, Schedule, User, db
from shuffify.services import signed_digest
from shuffify.services.playlist_lock import playlist_configuration_lock, playlist_lock
from shuffify.services.playlist_setup_service import configuration_revision, read_state
from shuffify.services.routine_activation_service import (
    INTENT_PURPOSE,
    RoutineActivationService,
    _intent_payload,
)
from shuffify.services.schedule_utils import schedule_revision
from shuffify.services.scheduler_service import ScheduleBusyError, SchedulerService
from tests.playlist_setup_fixtures import SetupSpotify, add_link, add_pair, add_routine, add_source


def _other_thread_can_lock(target):
    """Probe contention without sharing the caller's reentrant lock owner."""
    app = current_app._get_current_object()
    observed = []

    def probe():
        with app.app_context():
            with playlist_configuration_lock(target, timeout_s=0) as acquired:
                observed.append(acquired)

    thread = Thread(target=probe)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    return observed[0]


@contextmanager
def _execution_held(target):
    app = current_app._get_current_object()
    entered = Event()
    release = Event()

    def hold_execution():
        with app.app_context():
            with playlist_lock(target) as acquired:
                assert acquired
                entered.set()
                release.wait(timeout=5)

    holder = Thread(target=hold_execution)
    holder.start()
    assert entered.wait(timeout=2)
    try:
        yield
    finally:
        release.set()
        holder.join(timeout=2)
        assert not holder.is_alive()


@pytest.fixture
def world(app_ctx):
    user = User(spotify_id="user123", display_name="Test")
    db.session.add(user)
    db.session.commit()
    api = SetupSpotify()
    with ExitStack() as stack:
        sync = stack.enter_context(patch("shuffify.scheduler.sync_schedule_registration", create=True))
        status = stack.enter_context(patch("shuffify.scheduler.get_schedule_registration", create=True))
        status.return_value = {"status": "unavailable", "next_run_at": None, "effective_timezone": None}
        yield user, api, sync


@pytest.fixture
def file_db_app(tmp_path, monkeypatch):
    """Separate SQLite connections for threaded ordering tests."""
    from config import TestConfig
    from shuffify import create_app

    monkeypatch.setattr(TestConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{tmp_path / 'routines.db'}")
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.engine.dispose()


def _request(key, job_type="shuffle", **options):
    definition = {"job_type": job_type, "timing": {"kind": "weekly", "time": "09:00", "weekday": "mon"}}
    if job_type in ("shuffle", "raid_and_shuffle"):
        definition["algorithm_name"] = "BasicShuffle"
    if job_type == "rotate":
        definition["algorithm_params"] = {"target_size": 100}
    return {"request_key": key, "definition": definition, **options}


def _review(user, api, request):
    payload, status = RoutineActivationService.review(api, user, "target", request)
    assert status == 200, payload
    return payload


def _activate(user, api, request, review):
    return RoutineActivationService.activate(api, user, "target", {
        **{key: value for key, value in request.items() if key != "definition"},
        "definition": review["definition"], "intent": review["intent"],
        "dependency_revision": review["dependency_revision"],
    })


@pytest.mark.parametrize("job_type", ["raid", "shuffle", "raid_and_shuffle", "raid_and_drip", "rotate", "drip"])
def test_review_six_operations_with_saved_dependencies(world, job_type):
    user, api, _ = world
    add_source(user, query="ambient", kind="search_query")
    add_link(user, enabled=True)
    add_pair(user)
    before = (Schedule.query.count(), RoutineActivationReceipt.query.count())
    review = _review(user, api, _request(f"key-{job_type}", job_type))
    assert review["definition"]["source_playlist_ids"] == []
    assert review["resource_scope"]["source_ids"]
    assert before == (Schedule.query.count(), RoutineActivationReceipt.query.count())


def test_replay_keeps_exact_row_and_later_pause(world):
    user, api, sync = world
    request = _request("once")
    review = _review(user, api, request)
    accepted, status = _activate(user, api, request, review)
    assert status == 200, accepted
    assert Schedule.query.count() == RoutineActivationReceipt.query.count() == 1
    schedule = Schedule.query.get(accepted["schedule_id"])
    pause, status = RoutineActivationService.set_state(api, user, schedule.id, {
        "desired_enabled": False, "expected_revision": schedule_revision(schedule),
    })
    assert status == 200, pause
    replay, status = _activate(user, api, request, review)
    assert status == 200 and replay["replayed"]
    assert replay["schedule_id"] == accepted["schedule_id"]
    assert schedule.is_enabled is False
    assert Schedule.query.count() == 1
    assert sync.call_count >= 2
    assert replay["dependency_revision"] == configuration_revision(read_state(user.id, "target"))


@pytest.mark.parametrize(("initial_enabled", "desired_enabled"), [(True, False), (False, True)])
def test_state_change_can_commit_during_execution_lock(world, initial_enabled, desired_enabled):
    user, api, sync = world
    routine = add_routine(user, "shuffle", enabled=initial_enabled)
    routine.algorithm_name = "BasicShuffle"
    db.session.commit()
    with _execution_held("target"):
        answer, status = RoutineActivationService.set_state(api, user, routine.id, {
            "desired_enabled": desired_enabled,
            "expected_revision": schedule_revision(routine),
        })
        assert status == 200, answer
        assert answer["schedule"]["is_enabled"] is desired_enabled
    db.session.expire_all()
    assert db.session.get(Schedule, routine.id).is_enabled is desired_enabled
    sync.assert_called_once()


def test_new_activation_can_commit_during_execution_but_exact_edit_waits(world):
    user, api, _ = world
    new_request = _request("new-during-execution")
    new_review = _review(user, api, new_request)
    with _execution_held("target"):
        accepted, status = _activate(user, api, new_request, new_review)
        assert status == 200, accepted

    schedule = db.session.get(Schedule, accepted["schedule_id"])
    original_revision = schedule_revision(schedule)
    edit_request = _request(
        "edit-during-execution", schedule_id=schedule.id,
        expected_revision=original_revision,
    )
    edit_review = _review(user, api, edit_request)
    with patch("shuffify.services.routine_activation_service.LOCK_WAIT_S", 0):
        with _execution_held("target"):
            answer, status = _activate(user, api, edit_request, edit_review)
    assert status == 409 and answer["error_code"] == "playlist_busy"
    db.session.expire_all()
    assert schedule_revision(db.session.get(Schedule, schedule.id)) == original_revision


def test_initial_acceptance_and_receipt_replay_sync_under_target_lock(world):
    user, api, sync = world
    observed = []

    def observe(_schedule):
        observed.append(_other_thread_can_lock("target"))

    sync.side_effect = observe
    request = _request("lock-replay")
    review = _review(user, api, request)
    accepted, status = _activate(user, api, request, review)
    assert status == 200, accepted
    assert accepted["dependency_revision"] == configuration_revision(read_state(user.id, "target"))
    replay, status = _activate(user, api, request, review)
    assert status == 200 and replay["replayed"]
    assert observed == [False, False]


def test_changed_payload_conflicts_before_dependency_reads(world):
    user, api, _ = world
    request = _request("same")
    review = _review(user, api, request)
    accepted, status = _activate(user, api, request, review)
    assert status == 200, accepted
    changed = {**request, "create_another": True}
    answer, status = _activate(user, api, changed, review)
    assert status == 409 and answer["error_code"] == "request_key_conflict"


def test_expired_intent_cannot_create_after_receipt_cleanup(world):
    user, api, _ = world
    request = _request("expired")
    review = _review(user, api, request)
    body = {
        **{key: value for key, value in request.items() if key != "definition"},
        "definition": review["definition"], "dependency_revision": review["dependency_revision"],
    }
    old = dict(review)
    old["intent"] = signed_digest.issue(
        INTENT_PURPOSE, _intent_payload(user.id, "target", body), now=1
    )
    answer, status = _activate(user, api, request, old)
    assert status == 409 and answer["error_code"] == "intent_expired"
    assert Schedule.query.count() == RoutineActivationReceipt.query.count() == 0


def test_expired_receipt_and_original_intent_cannot_recreate(world):
    user, api, _ = world
    request = _request("old-key")
    review = _review(user, api, request)
    accepted, status = _activate(user, api, request, review)
    assert status == 200, accepted
    receipt = RoutineActivationReceipt.query.one()
    receipt.accepted_at = datetime.now(timezone.utc) - timedelta(days=8)
    db.session.delete(Schedule.query.get(accepted["schedule_id"]))
    db.session.commit()
    body = {
        "request_key": request["request_key"], "definition": review["definition"],
        "dependency_revision": review["dependency_revision"],
    }
    expired = {**review, "intent": signed_digest.issue(INTENT_PURPOSE, _intent_payload(user.id, "target", body), now=1)}
    refused, status = _activate(user, api, request, expired)
    assert status == 409 and refused["error_code"] == "intent_expired"
    assert Schedule.query.count() == 0


def test_invalid_dependencies_and_pause(world):
    user, api, _ = world
    answer, status = RoutineActivationService.review(api, user, "target", _request("need-source", "raid"))
    assert status == 422 and answer["field_errors"][0]["path"] == "definition.job_type"
    request = _request("pause")
    review = _review(user, api, request)
    accepted, _ = _activate(user, api, request, review)
    schedule = Schedule.query.get(accepted["schedule_id"])
    # No saved source or collection is needed to pause an owned exact row.
    answer, status = RoutineActivationService.set_state(api, user, schedule.id, {
        "desired_enabled": False, "expected_revision": schedule_revision(schedule),
    })
    assert status == 200 and answer["schedule_id"] == schedule.id


def test_existing_candidates_and_explicit_additional_creation(world):
    user, api, _ = world
    first = _request("first")
    first_review = _review(user, api, first)
    accepted, status = _activate(user, api, first, first_review)
    assert status == 200, accepted
    second = _request("second")
    second_review = _review(user, api, second)
    assert second_review["candidate_schedule_ids"] == [accepted["schedule_id"]]
    refused, status = _activate(user, api, second, second_review)
    assert status == 409 and refused["error_code"] == "existing_candidates"
    deliberate = _request("third", create_another=True)
    deliberate_review = _review(user, api, deliberate)
    added, status = _activate(user, api, deliberate, deliberate_review)
    assert status == 200 and added["schedule_id"] != accepted["schedule_id"]
    assert Schedule.query.count() == 2


def test_deleted_replay_keeps_original_identity(world):
    user, api, _ = world
    request = _request("deleted")
    review = _review(user, api, request)
    accepted, status = _activate(user, api, request, review)
    assert status == 200, accepted
    db.session.delete(Schedule.query.get(accepted["schedule_id"]))
    db.session.commit()
    replay, status = _activate(user, api, request, review)
    assert status == 200 and replay["state"] == "Deleted"
    assert replay["schedule_id"] == accepted["schedule_id"]
    assert replay["dependency_revision"] == configuration_revision(read_state(user.id, "target"))
    assert Schedule.query.count() == 0


def test_stale_edit_and_final_merged_validation(world):
    user, api, _ = world
    request = _request("create")
    review = _review(user, api, request)
    accepted, _ = _activate(user, api, request, review)
    schedule = Schedule.query.get(accepted["schedule_id"])
    edit = {
        "request_key": "edit", "schedule_id": schedule.id,
        "expected_revision": schedule_revision(schedule),
        "definition": {"timing": {"kind": "daily", "time": "08:00"}},
    }
    edited = _review(user, api, edit)
    assert edited["definition"]["algorithm_name"] == "BasicShuffle"
    schedule.algorithm_name = None
    db.session.commit()
    refused, status = _activate(user, api, edit, edited)
    assert status == 409 and refused["error_code"] == "routine_changed"
    fresh_edit = {**edit, "request_key": "bad-edit", "expected_revision": schedule_revision(schedule)}
    bad, status = RoutineActivationService.review(api, user, "target", fresh_edit)
    assert status == 422 and bad["field_errors"][0]["path"] == "definition.algorithm_name"


def test_receipt_and_schedule_rollback_together(world):
    user, api, _ = world
    request = _request("rollback")
    review = _review(user, api, request)
    with patch(
        "shuffify.services.routine_activation_service.ActivityLogService.log",
        side_effect=RuntimeError("failed"),
    ):
        failed, status = _activate(user, api, request, review)
    assert status == 503 and failed["error_code"] == "activation_failed"
    assert Schedule.query.count() == RoutineActivationReceipt.query.count() == 0


def test_cross_user_exact_id_is_not_accepted(world):
    user, api, _ = world
    other = User(spotify_id="another")
    db.session.add(other)
    db.session.commit()
    schedule = Schedule(
        user_id=other.id, job_type="shuffle", target_playlist_id="target",
        target_playlist_name="Morning Mix", algorithm_name="BasicShuffle",
        schedule_type="interval", schedule_value="daily", is_enabled=True,
    )
    db.session.add(schedule)
    db.session.commit()
    request = _request("foreign", schedule_id=schedule.id, expected_revision=schedule_revision(schedule))
    answer, status = RoutineActivationService.review(api, user, "target", request)
    assert status == 404 and answer["error_code"] == "routine_not_found"


@pytest.mark.parametrize("job_type", ["raid", "raid_and_shuffle", "raid_and_drip"])
def test_timing_edit_preserves_legacy_routine_only_source(world, job_type):
    user, api, _ = world
    add_source(user, "ext1")
    if job_type == "raid_and_drip":
        add_link(user, enabled=True)
    routine = add_routine(user, job_type, ids=["legacy1"], enabled=False)
    if job_type == "raid_and_shuffle":
        routine.algorithm_name = "BasicShuffle"
        db.session.commit()
    request = {
        "request_key": f"edit-{job_type}", "schedule_id": routine.id,
        "expected_revision": schedule_revision(routine),
        "definition": {"timing": {"kind": "daily", "time": "08:00"}},
    }
    reviewed = _review(user, api, request)
    assert reviewed["definition"]["source_playlist_ids"] == ["legacy1"]
    assert reviewed["resource_scope"]["source_ids"]
    assert reviewed["resource_scope"]["routine_only_source_playlist_ids"] == ["legacy1"]
    accepted, status = _activate(user, api, request, reviewed)
    assert status == 200, accepted
    assert accepted["schedule_id"] == routine.id
    assert accepted["definition"]["source_playlist_ids"] == ["legacy1"]
    assert routine.source_playlist_ids == ["legacy1"]
    assert routine.is_enabled is False
    assert routine.schedule_value == "0 8 * * *"


def test_review_cannot_change_existing_legacy_sources(world):
    user, api, _ = world
    routine = add_routine(user, "raid", ids=["legacy1"])
    request = {
        "request_key": "bad-source-edit", "schedule_id": routine.id,
        "expected_revision": schedule_revision(routine),
        "definition": {"source_playlist_ids": ["other"]},
    }
    refused, status = RoutineActivationService.review(api, user, "target", request)
    assert status == 422 and refused["field_errors"][0]["path"] == "definition"
    assert routine.source_playlist_ids == ["legacy1"]


def test_legacy_only_source_remains_sufficient_for_edit(world):
    user, api, _ = world
    routine = add_routine(user, "raid", ids=["legacy1"])
    request = {
        "request_key": "only-legacy", "schedule_id": routine.id,
        "expected_revision": schedule_revision(routine),
        "definition": {"timing": {"kind": "interval", "interval": "weekly"}},
    }
    reviewed = _review(user, api, request)
    assert reviewed["definition"]["source_playlist_ids"] == ["legacy1"]


def test_collection_is_checked_once_per_request(world):
    user, api, _ = world
    add_source(user, query="ambient", kind="search_query")
    add_link(user, enabled=True)
    api.calls.clear()
    context, status = RoutineActivationService.context(api, user, "target")
    assert status == 200, context
    assert api.calls.count(("get_playlist_summary", "col1")) == 1
    api.calls.clear()
    reviewed = _review(user, api, _request("memo", "raid_and_drip"))
    assert api.calls.count(("get_playlist_summary", "col1")) == 1
    api.calls.clear()
    accepted, status = _activate(user, api, _request("memo", "raid_and_drip"), reviewed)
    assert status == 200, accepted
    assert api.calls.count(("get_playlist_summary", "col1")) == 1


def test_resume_pause_interleaving_cannot_leave_a_registered_job(file_db_app):
    app = current_app._get_current_object()
    user = User(spotify_id="user123")
    db.session.add(user)
    db.session.commit()
    routine = add_routine(user, "shuffle", enabled=False)
    routine.algorithm_name = "BasicShuffle"
    db.session.commit()
    user_id, routine_id = user.id, routine.id
    revision = schedule_revision(routine)
    api = SetupSpotify()
    resume_entered = Event()
    release_resume = Event()
    registered = set()
    registered_guard = Lock()
    lock_observations = []

    def fake_sync(row):
        lock_observations.append(_other_thread_can_lock("target"))
        if row.is_enabled:
            resume_entered.set()
            assert release_resume.wait(5)
        with registered_guard:
            if row.is_enabled:
                registered.add(row.id)
            else:
                registered.discard(row.id)

    def fake_registration(row):
        with registered_guard:
            present = row.id in registered
        return {
            "status": "registered" if present and row.is_enabled else "paused" if not present else "needs_attention",
            "next_run_at": None, "effective_timezone": "UTC",
        }

    def resume():
        with app.app_context():
            actor = db.session.get(User, user_id)
            return RoutineActivationService.set_state(api, actor, routine_id, {
                "desired_enabled": True, "expected_revision": revision,
            })

    def pause():
        with app.app_context():
            actor = db.session.get(User, user_id)
            row = db.session.get(Schedule, routine_id)
            return RoutineActivationService.set_state(api, actor, routine_id, {
                "desired_enabled": False, "expected_revision": schedule_revision(row),
            })

    with patch("shuffify.scheduler.sync_schedule_registration", side_effect=fake_sync), patch(
        "shuffify.scheduler.get_schedule_registration", side_effect=fake_registration
    ), ThreadPoolExecutor(max_workers=2) as pool:
        resumed = pool.submit(resume)
        assert resume_entered.wait(5)
        paused = pool.submit(pause)
        # The pause must wait for the resume's synchronized registration.
        assert not paused.done()
        release_resume.set()
        resume_answer, resume_status = resumed.result(timeout=5)
        pause_answer, pause_status = paused.result(timeout=5)
    assert resume_status == pause_status == 200, (resume_answer, pause_answer)
    assert lock_observations == [False, False]
    db.session.expire_all()
    assert db.session.get(Schedule, routine_id).is_enabled is False
    assert registered == set()


def test_legacy_edit_waits_for_activation_target_lock(file_db_app):
    app = file_db_app
    user = User(spotify_id="user123")
    db.session.add(user)
    db.session.commit()
    routine = add_routine(user, "shuffle", enabled=True)
    routine.algorithm_name = "BasicShuffle"
    db.session.commit()
    user_id, routine_id = user.id, routine.id
    began = Event()
    committed = Event()

    def legacy_update():
        with app.app_context():
            began.set()
            SchedulerService.update_schedule(
                routine_id, user_id, algorithm_params={"keep_first": 2}
            )

    with (
        patch.object(SchedulerService, "_sync_apscheduler_job", side_effect=lambda _row: committed.set()),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        with playlist_lock("target", timeout_s=2) as acquired:
            assert acquired
            reviewed_revision = configuration_revision(read_state(user_id, "target"))
            db.session.rollback()  # release SQLite's read transaction before the other connection writes
            pending = pool.submit(legacy_update)
            assert began.wait(5)
            assert not committed.wait(0.1)
            assert not pending.done()
        pending.result(timeout=5)
    assert committed.is_set()
    db.session.expire_all()
    assert db.session.get(Schedule, routine_id).algorithm_params == {"keep_first": 2}
    assert configuration_revision(read_state(user_id, "target")) != reviewed_revision


def test_legacy_mutators_hold_target_locks_through_registration(world):
    user, _, _ = world
    observed = []

    def sync(row):
        observed.append(_other_thread_can_lock(row.target_playlist_id))

    def removed(_schedule_id):
        observed.append(_other_thread_can_lock("mine1"))

    def sync_move(row):
        sync(row)
        observed.append(_other_thread_can_lock("target"))

    with patch.object(SchedulerService, "_sync_apscheduler_job", side_effect=sync):
        schedule = SchedulerService.create_schedule(
            user.id, "shuffle", "target", "Morning Mix", "interval", "daily", algorithm_name="BasicShuffle"
        )
        SchedulerService.update_schedule(schedule.id, user.id, schedule_value="weekly")
        SchedulerService.toggle_schedule(schedule.id, user.id)
        with patch.object(SchedulerService, "_sync_apscheduler_job", side_effect=sync_move):
            SchedulerService.update_schedule(schedule.id, user.id, target_playlist_id="mine1")
    with patch.object(SchedulerService, "_remove_apscheduler_job", side_effect=removed):
        SchedulerService.delete_schedule(schedule.id, user.id)
    assert observed == [False] * 6


def test_legacy_mutators_return_busy_without_changing_row(file_db_app):
    app = file_db_app
    user = User(spotify_id="user123")
    db.session.add(user)
    db.session.commit()
    routine = add_routine(user, "shuffle", enabled=True)
    before = schedule_revision(routine)
    user_id, routine_id = user.id, routine.id

    def try_mutations():
        with app.app_context():
            actions = [
                lambda: SchedulerService.update_schedule(routine_id, user_id, schedule_value="weekly"),
                lambda: SchedulerService.toggle_schedule(routine_id, user_id),
                lambda: SchedulerService.delete_schedule(routine_id, user_id),
                lambda: SchedulerService.create_schedule(
                    user_id, "shuffle", "target", "Morning Mix", "interval", "daily",
                    algorithm_name="BasicShuffle",
                ),
            ]
            for action in actions:
                with pytest.raises(ScheduleBusyError):
                    action()

    with (
        patch("shuffify.services.scheduler_service.LOCK_WAIT_S", 0),
        playlist_configuration_lock("target", timeout_s=0) as held,
    ):
        assert held
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(try_mutations).result(timeout=5)
    db.session.expire_all()
    assert Schedule.query.count() == 1
    assert schedule_revision(db.session.get(Schedule, routine.id)) == before
