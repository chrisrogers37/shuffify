"""Saved routine policy through the same reviewed, state and raid HTTP routes as the UI."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from apscheduler.triggers.cron import CronTrigger

from shuffify.models.db import ActivityLog, RoutineActivationReceipt, Schedule, UpstreamSource, User, db
from shuffify.services.schedule_utils import schedule_revision
from tests.playlist_setup_fixtures import SetupSpotify

SOURCE = "s" * 22
HOURLY = {"kind": "advanced", "expression": "0 * * * *", "timezone": "UTC"}


@pytest.fixture
def provider(monkeypatch):
    spotify = SetupSpotify(playlists={
        **{f"mix{i}": {"name": f"Mix {i}", "collaborative": True} for i in range(24)},
        SOURCE: {"name": "External", "owner": "external"},
    })
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: spotify)
    pokes = []
    monkeypatch.setattr("shuffify.services.worker_control.WorkerControlClient.poke", lambda self: pokes.append(True))
    monkeypatch.setattr("shuffify.services.worker_control.WorkerControlClient.registrations", lambda *args: None)
    yield pokes
    assert spotify.writes == []


def sign_in(app, spotify_id, token):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_data={"id": spotify_id}, spotify_token=token)
    return client


@pytest.fixture
def accounts(limiter_app, sample_token, provider):
    # Temporary session files, in-memory SQLite and no shared Redis or worker.
    with limiter_app.app_context():
        db.session.add_all([User(spotify_id=name, display_name=name) for name in ("user123", "bob")])
        db.session.commit()
        yield [sign_in(limiter_app, name, sample_token) for name in ("user123", "bob")]
        db.session.remove()
        db.engine.dispose()


def review(client, target, definition=None, **options):
    body = {"request_key": uuid4().hex, "definition": definition if definition is not None else {
        "job_type": "shuffle", "algorithm_name": "BasicShuffle", "timing": HOURLY,
    }, **options}
    response = client.post(f"/api/playlist/{target}/routines/review", json=body)
    if response.status_code == 200:
        for field in ("definition", "intent", "dependency_revision"):
            body[field] = response.json[field]
    return response, body


def activate(client, target, body):
    return client.post(f"/api/playlist/{target}/routines/activate", json=body)


def create(client, target, **options):
    response, body = review(client, target, **options)
    assert response.status_code == 200, response.json
    return activate(client, target, body), body


def state(client, row, enabled):
    return client.put(f"/api/routines/{row.id}/state", json={
        "desired_enabled": enabled, "expected_revision": schedule_revision(row),
    })


def inventory(pokes):
    return (Schedule.query.count(), RoutineActivationReceipt.query.count(), UpstreamSource.query.count(),
            ActivityLog.query.count(), len(pokes))


def raid_watch(client, target):
    return client.post(f"/api/playlist/{target}/raid-add-url", json={
        "url": f"https://open.spotify.com/playlist/{SOURCE}", "auto_schedule": True,
    })


def test_irregular_cron_after_a_long_first_gap_refused(accounts, provider):
    alice, _ = accounts
    # The first two slots from just after Monday's first slot are a week apart; the third is five minutes later.
    expression = "0,5 9 * * mon"
    trigger = CronTrigger.from_crontab(expression, timezone="UTC")
    start = datetime(2026, 11, 23, 9, 1, tzinfo=timezone.utc)
    first = trigger.get_next_fire_time(None, start)
    second = trigger.get_next_fire_time(first, first + timedelta(seconds=1))
    third = trigger.get_next_fire_time(second, second + timedelta(seconds=1))
    assert second - first > timedelta(days=6) and third - second == timedelta(minutes=5)
    response, _ = review(alice, "mix0", {
        "job_type": "shuffle", "algorithm_name": "BasicShuffle", "timing": {**HOURLY, "expression": expression},
    })
    assert response.status_code == 422 and "hour" in response.json["message"]
    assert inventory(provider) == (0, 0, 0, 0, 0)


def test_hourly_accepted_half_hour_refused(accounts, provider):
    alice, _ = accounts
    accepted, _ = create(alice, "mix0")
    assert accepted.status_code == 200 and accepted.json["schedule"]["schedule_value"] == "0 * * * *"
    before = inventory(provider)
    response, _ = review(alice, "mix1", {
        "job_type": "shuffle", "algorithm_name": "BasicShuffle", "timing": {**HOURLY, "expression": "*/30 * * * *"},
    })
    assert response.status_code == 422 and "hour" in response.json["message"]
    assert inventory(provider) == before


def test_presets_still_activate_with_their_original_timing(accounts):
    alice, _ = accounts
    presets = [
        ({"kind": "interval", "interval": value}, "interval", value)
        for value in ("every_6h", "every_12h", "daily", "every_3d", "weekly")
    ]
    presets.extend([
        ({"kind": "daily", "time": "09:05"}, "cron", "5 9 * * *"),
        ({"kind": "weekly", "weekday": "sun", "time": "09:05"}, "cron", "5 9 * * sun"),
    ])
    for i, (timing, kind, value) in enumerate(presets):
        response, _ = create(alice, f"mix{i}", definition={
            "job_type": "shuffle", "algorithm_name": "BasicShuffle", "timing": timing,
        })
        assert response.status_code == 200, response.json
        schedule = response.json["schedule"]
        assert (schedule["schedule_type"], schedule["schedule_value"]) == (kind, value)


@pytest.mark.parametrize("scope,limit", [("user", 20), ("target", 6)])
def test_exact_capacity_replay_pause_one_over_and_other_owner(accounts, scope, limit, provider):
    alice, bob = accounts
    for i in range(limit):
        target = f"mix{i}" if scope == "user" else "mix0"
        accepted, body = create(alice, target, create_another=i > 0)
        assert accepted.status_code == 200, accepted.json
    row = db.session.get(Schedule, accepted.json["schedule_id"])
    assert state(alice, row, False).status_code == 200
    replay = activate(alice, target, body)
    assert replay.status_code == 200 and replay.json["replayed"]
    assert replay.json["schedule_id"] == row.id and not replay.json["schedule"]["is_enabled"]
    # Editing or resuming the existing slot at capacity consumes no additional slot.
    assert state(alice, row, True).status_code == 200
    response, _ = create(alice, target, schedule_id=row.id, expected_revision=schedule_revision(row), definition={})
    assert response.status_code == 200, response.json
    assert state(alice, row, False).status_code == 200
    before = inventory(provider)
    full = "mix20" if scope == "user" else target
    for client in ([alice, bob] if scope == "target" else [alice]):
        refused, _ = create(client, full, create_another=True)
        assert refused.status_code == 409, refused.json
        assert refused.json["error_code"] == f"routine_{scope}_limit"
        refused = raid_watch(client, full)
        assert refused.status_code == 409, refused.json
        assert refused.json["error_code"] == f"routine_{scope}_limit"
        assert inventory(provider) == before
    assert create(bob, "mix21")[0].status_code == 200
    if scope == "user":
        assert create(bob, full)[0].status_code == 200
    else:
        assert create(alice, "mix22")[0].status_code == 200
    assert alice.delete(f"/api/schedules/{row.id}").status_code == 200
    assert raid_watch(alice, full).status_code == 200
    assert Schedule.query.count() == limit + 2 and UpstreamSource.query.count() == 1


def test_legacy_subhour_edit_resume_replay_and_cleanup(accounts, provider):
    alice, _ = accounts
    created, original = create(alice, "mix0")
    row = db.session.get(Schedule, created.json["schedule_id"])
    row.schedule_value = "*/10 * * * *"
    db.session.commit()
    assert state(alice, row, False).status_code == 200
    replay = activate(alice, "mix0", original)
    assert replay.status_code == 200 and replay.json["replayed"] and not replay.json["schedule"]["is_enabled"]
    before = inventory(provider)
    refused = state(alice, row, True)
    assert refused.status_code == 422 and "hour" in refused.json["message"]
    options = {"schedule_id": row.id, "expected_revision": schedule_revision(row)}
    refused, _ = review(alice, "mix0", {}, **options)
    assert refused.status_code == 422 and inventory(provider) == before
    stale, _ = review(alice, "mix0", {}, **{**options, "expected_revision": "0" * 64})
    assert stale.status_code == 409 and stale.json["error_code"] == "routine_changed"
    corrected, _ = create(alice, "mix0", definition={"timing": HOURLY}, **options)
    assert corrected.status_code == 200 and not corrected.json["schedule"]["is_enabled"]
    assert state(alice, row, True).status_code == 200
    assert alice.delete(f"/api/schedules/{row.id}").status_code == 200


def test_raid_auto_edit_refusal_rolls_back_staged_source(accounts, provider):
    alice, _ = accounts
    row = Schedule(user_id=User.query.filter_by(spotify_id="user123").one().id, target_playlist_id="mix0",
                   job_type="raid", schedule_type="cron", schedule_value="*/20 * * * *", source_playlist_ids=[])
    db.session.add(row)
    db.session.commit()
    before = inventory(provider)
    refused = raid_watch(alice, "mix0")
    assert refused.status_code == 422 and "hour" in refused.json["message"]
    db.session.expire_all()
    assert row.source_playlist_ids == [] and inventory(provider) == before


def test_valid_prepolicy_intent_rechecked_at_activation(accounts, provider):
    from shuffify.services import signed_digest
    from shuffify.services.routine_activation_service import INTENT_PURPOSE, _intent_payload

    alice, _ = accounts
    response, body = review(alice, "mix0")
    assert response.status_code == 200
    # A genuine review issued before the minimum was introduced can still be unexpired.
    body["definition"].pop("advanced_cron", None)
    body["definition"]["schedule_value"] = "0,30 * * * *"
    owner = User.query.filter_by(spotify_id="user123").one()
    body["intent"] = signed_digest.issue(INTENT_PURPOSE, _intent_payload(owner.id, "mix0", body))
    refused = activate(alice, "mix0", body)
    assert refused.status_code == 422 and "hour" in refused.json["message"]
    assert inventory(provider) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("scope,limit", [("user", 20), ("target", 6)])
def test_legacy_over_capacity_fires_but_edits_and_resume_require_room(accounts, scope, limit, provider):
    from shuffify.services.operations.repository import OperationRepository
    from shuffify.services.scheduled_admission import admit_scheduled, build_cadence

    alice, _ = accounts
    accepted, _ = create(alice, "mix0")
    row = db.session.get(Schedule, accepted.json["schedule_id"])
    db.session.add_all(Schedule(user_id=row.user_id, job_type="shuffle", is_enabled=False,
                                target_playlist_id=f"mix{i + 1}" if scope == "user" else "mix0") for i in range(limit))
    db.session.commit()
    fire = admit_scheduled(OperationRepository(), row.id, build_cadence(row).fingerprint,
                           datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0))
    assert fire.state == "admitted"
    assert state(alice, row, False).status_code == 200
    before = inventory(provider)
    refused, _ = create(alice, "mix0", schedule_id=row.id, expected_revision=schedule_revision(row), definition={})
    assert refused.status_code == 409 and refused.json["error_code"] == f"routine_{scope}_limit"
    refused = state(alice, row, True)
    assert refused.status_code == 409 and refused.json["error_code"] == f"routine_{scope}_limit"
    assert inventory(provider) == before and not row.is_enabled
    other = Schedule.query.filter(Schedule.id != row.id).first()
    assert alice.delete(f"/api/schedules/{other.id}").status_code == 200
    assert state(alice, row, True).status_code == 200


def test_setup_save_refuses_legacy_routine_edit_and_preserves_source(accounts, provider):
    alice, _ = accounts
    owner = User.query.filter_by(spotify_id="user123").one()
    source = UpstreamSource(user_id=owner.id, target_playlist_id="mix0", source_playlist_id=SOURCE, source_type="own")
    row = Schedule(user_id=owner.id, job_type="raid", target_playlist_id="mix0", schedule_type="cron",
                   schedule_value="*/5 * * * *", source_playlist_ids=[SOURCE])
    db.session.add_all([row, source])
    db.session.commit()
    base = "/api/playlist/mix0/setup"
    context = alice.get(f"{base}/context").json["context"]
    body = {"changes": [{"op": "remove_source", "source_id": source.id}],
            "configuration_revision": context["configuration_revision"]}
    impact = alice.post(f"{base}/impact", json=body)
    assert impact.status_code == 200, impact.json
    before = inventory(provider)
    saved = alice.put(f"{base}/settings", json={**body, "acknowledgment": impact.json["acknowledgment"]})
    assert saved.status_code == 422 and "hour" in saved.json["message"]
    db.session.expire_all()
    assert row.source_playlist_ids == [SOURCE] and inventory(provider) == before


def test_scheduler_move_counts_the_destination_and_legacy_pause_still_works(accounts, provider):
    from shuffify.services.scheduler_service import SchedulerService

    alice, _ = accounts
    accepted, _ = create(alice, "mix0")
    row = db.session.get(Schedule, accepted.json["schedule_id"])
    for _ in range(6):
        assert create(alice, "mix1", create_another=True)[0].status_code == 200
    before = inventory(provider)
    with pytest.raises(ValueError, match="playlist"):
        SchedulerService.update_schedule(row.id, row.user_id, target_playlist_id="mix1")
    assert row.target_playlist_id == "mix0" and inventory(provider) == before
    row.schedule_value = "*/5 * * * *"
    db.session.commit()
    assert not SchedulerService.update_schedule(row.id, row.user_id, is_enabled=False).is_enabled
    before = inventory(provider)
    with pytest.raises(ValueError, match="one hour"):
        SchedulerService.update_schedule(row.id, row.user_id, is_enabled=True)
    assert not row.is_enabled and inventory(provider) == before


@pytest.mark.parametrize("preset,expression", [
    ({"kind": "daily", "time": "01:30"}, "30 1 * * *"),
    ({"kind": "weekly", "time": "01:30", "weekday": "sun"}, "30 1 * * sun"),
])
def test_advanced_and_preset_same_expression_keep_distinct_fold_behavior(accounts, preset, expression):
    from shuffify.services.scheduled_admission import build_cadence

    alice, _ = accounts
    zone = "Australia/Lord_Howe"
    before_fold = datetime(2027, 4, 3, 14, 30, tzinfo=timezone.utc)

    def next_two(row):
        trigger = build_cadence(row).trigger
        first = trigger.get_next_fire_time(None, before_fold)
        second = trigger.get_next_fire_time(first, first.astimezone(timezone.utc) + timedelta(seconds=1))
        return first.astimezone(timezone.utc), second.astimezone(timezone.utc)

    response, _ = create(alice, "mix0", definition={
        "job_type": "shuffle", "algorithm_name": "BasicShuffle", "timing": {**preset, "timezone": zone},
    })
    assert response.status_code == 200, response.json
    row = db.session.get(Schedule, response.json["schedule_id"])
    assert next_two(row) == (before_fold, before_fold + timedelta(minutes=30))
    original_revision = response.json["schedule_revision"]
    original_cadence = build_cadence(row).fingerprint
    advanced, body = create(alice, "mix0", schedule_id=row.id, expected_revision=original_revision,
                            definition={"timing": {"kind": "advanced", "expression": expression, "timezone": zone}})
    assert advanced.status_code == 200 and advanced.json["timing"]["kind"] == "advanced"
    assert row.schedule_value == expression and build_cadence(row).fingerprint != original_cadence
    assert next_two(row)[1] - before_fold >= timedelta(hours=1)
    stale = alice.put(f"/api/routines/{row.id}/state", json={
        "expected_revision": original_revision, "desired_enabled": False,
    })
    assert stale.status_code == 409 and stale.json["error_code"] == "routine_changed"
    restored, _ = create(alice, "mix0", schedule_id=row.id, expected_revision=advanced.json["schedule_revision"],
                         definition={"timing": {**preset, "timezone": zone}})
    assert restored.status_code == 200 and restored.json["timing"]["kind"] == preset["kind"]
    assert next_two(row) == (before_fold, before_fold + timedelta(minutes=30))
    replay = activate(alice, "mix0", body)
    assert replay.status_code == 200 and replay.json["replayed"] and replay.json["timing"]["kind"] == preset["kind"]


def test_raid_clock_preset_and_raw_scheduler_cron_keep_distinct_fold_behavior(accounts):
    from shuffify.services.scheduled_admission import build_cadence
    from shuffify.services.scheduler_service import SchedulerService

    alice, _ = accounts
    response = alice.post("/api/playlist/mix0/raid-add-url", json={
        "url": f"https://open.spotify.com/playlist/{SOURCE}", "auto_schedule": True,
        "schedule_value": "daily", "schedule_time": "01:30",
    })
    assert response.status_code == 200, response.json
    preset = db.session.get(Schedule, response.json["schedule"]["id"])
    raw = SchedulerService.create_schedule(preset.user_id, "shuffle", "mix1", "Mix 1", "cron", preset.schedule_value)
    before_fold = datetime(2027, 4, 3, 14, 30, tzinfo=timezone.utc)
    for row, expected in [(preset, timedelta(minutes=30)), (raw, timedelta(days=1, minutes=30))]:
        trigger = build_cadence(row, "Australia/Lord_Howe").trigger
        first = trigger.get_next_fire_time(None, before_fold)
        second = trigger.get_next_fire_time(first, before_fold + timedelta(seconds=1))
        assert second.astimezone(timezone.utc) - before_fold == expected
