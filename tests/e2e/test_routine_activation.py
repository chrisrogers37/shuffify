"""Reviewed activation against real PostgreSQL and the fake-only harness scheduler."""

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from threading import Barrier

import pytest
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from config import HarnessConfig
from tests.e2e.conftest import APP, hold_playlist_lock, pid
from tests.e2e.test_live_operation_outcomes import _registration
from tests.paths import api_path

TARGET = pid("alice-morning-mix")
OTHER = pid("alice-long-haul")
SOURCE = pid("bob-finds")


@contextmanager
def _hold_configuration_lock(target):
    # Same independent PostgreSQL namespace as playlist_configuration_lock.
    with hold_playlist_lock(f"configuration:{target}"):
        yield


def _review(alice, key, target=TARGET, definition=None, **choices):
    definition = definition if definition is not None else {
        "job_type": "shuffle", "algorithm_name": "BasicShuffle",
        "algorithm_params": {"keep_first": 0},
        "timing": {"kind": "interval", "interval": "daily", "timezone": "America/New_York"},
    }
    review = alice.ok("post", f"/playlist/{target}/routines/review", json={
        "request_key": key, "definition": definition, **choices,
    })
    return {
        "request_key": key, "definition": review["definition"], "intent": review["intent"],
        "dependency_revision": review["dependency_revision"], **choices,
    }


def _activate(alice, body, target=TARGET):
    return alice.ok("post", f"/playlist/{target}/routines/activate", json=body)


def _counts():
    HarnessConfig.assert_safe()
    engine = create_engine(HarnessConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT (SELECT count(*) FROM schedules), "
                "(SELECT count(*) FROM routine_activation_receipts), "
                "(SELECT count(*) FROM activity_log WHERE activity_type = 'schedule_create')"
            )).one()
    finally:
        engine.dispose()


def test_review_is_read_only_and_replay_preserves_pause_edit_and_deleted_identity(alice, fake):
    spotify_before = fake.playlist(TARGET)
    before_counts = _counts()
    body = _review(alice, "harness-replay")
    assert _counts() == before_counts
    accepted = _activate(alice, body)
    sid = accepted["schedule_id"]
    assert accepted["accepted"] and not accepted["replayed"]
    assert accepted["state"] == "Active"
    assert accepted["registration"]["status"] == "registered"
    registration = _registration(sid)
    assert registration is not None
    replay = _activate(alice, body)
    assert replay["replayed"] and replay["schedule_id"] == sid
    assert _registration(sid) == registration
    assert _counts() == tuple(n + 1 for n in before_counts)
    paused = alice.ok("put", f"/routines/{sid}/state", json={
        "desired_enabled": False, "expected_revision": replay["schedule_revision"],
    })
    assert paused["state"] == "Paused" and _registration(sid) is None
    edit_body = _review(alice, "harness-edit", definition={"algorithm_params": {"keep_first": 2}},
                        schedule_id=sid, expected_revision=paused["schedule_revision"])
    edited = _activate(alice, edit_body)
    assert edited["state"] == "Paused"
    assert edited["definition"]["timezone"] == "America/New_York"
    replay = _activate(alice, body)
    assert replay["state"] == "Paused"
    assert replay["definition"]["algorithm_params"] == {"keep_first": 2}
    assert _registration(sid) is None
    alice.ok("delete", f"/schedules/{sid}")
    deleted = _activate(alice, body)
    assert deleted["state"] == "Deleted" and deleted["schedule"] is None
    assert deleted["schedule_id"] == sid and _registration(sid) is None
    assert fake.playlist(TARGET) == spotify_before


def _parallel_activations(alice, submissions):
    """Independent HTTP sessions share auth but not requests.Session mutable state."""
    barrier = Barrier(len(submissions))

    def submit(item):
        target, body = item
        with requests.Session() as http:
            http.cookies.update(alice.http.cookies)
            http.headers.update(alice.http.headers)
            barrier.wait(timeout=10)
            response = http.post((APP + api_path(f'/playlist/{target}/routines/activate')), json=body, timeout=30)
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=len(submissions)) as pool:
        return list(pool.map(submit, submissions))


def test_concurrent_identical_activation_accepts_one_schedule_and_one_receipt(alice, fake):
    body = _review(alice, "harness-concurrent")
    baseline = _counts()
    responses = _parallel_activations(alice, [(TARGET, body), (TARGET, body)])
    assert [status for status, _ in responses] == [200, 200], responses
    assert sorted(reply["replayed"] for _, reply in responses) == [False, True]
    ids = {reply["schedule_id"] for _, reply in responses}
    assert len(ids) == 1
    assert _counts() == tuple(n + 1 for n in baseline)
    alice.ok("delete", f"/schedules/{ids.pop()}")


def test_cross_target_same_key_loser_rolls_back_its_schedule(alice, fake):
    first = _review(alice, "harness-cross-target", TARGET)
    second = _review(alice, "harness-cross-target", OTHER)
    baseline = _counts()
    responses = _parallel_activations(alice, [(TARGET, first), (OTHER, second)])
    assert sorted(status for status, _ in responses) == [200, 409], responses
    conflict = next(reply for status, reply in responses if status == 409)
    assert conflict["error_code"] == "request_key_conflict"
    assert _counts() == tuple(n + 1 for n in baseline)
    winner = next(reply for status, reply in responses if status == 200)
    alice.ok("delete", f"/schedules/{winner['schedule_id']}")


def test_stale_dependencies_and_changed_key_payload_do_not_write(alice, fake):
    body = _review(alice, "harness-stale")
    alice.ok("post", f"/playlist/{TARGET}/raid-watch", json={
        "source_playlist_id": SOURCE, "source_playlist_name": "Bob's Finds", "auto_schedule": False,
    })
    baseline = _counts()
    response = alice.post(api_path(f"/playlist/{TARGET}/routines/activate"), json=body)
    assert response.status_code == 409
    assert response.json()["error_code"] == "prerequisites_changed"
    assert _counts() == baseline
    body = _review(alice, "harness-fresh")
    accepted = _activate(alice, body)
    changed = deepcopy(body)
    changed["definition"]["algorithm_params"]["keep_first"] = 1
    response = alice.post(api_path(f"/playlist/{TARGET}/routines/activate"), json=changed)
    assert response.status_code == 409
    assert response.json()["error_code"] == "request_key_conflict"
    assert _counts() == tuple(n + 1 for n in baseline)
    alice.ok("delete", f"/schedules/{accepted['schedule_id']}")


@pytest.mark.parametrize("method,suffix,body", [
    ("put", "", {"schedule_value": "weekly"}),
    ("post", "/toggle", {}),
    ("delete", "", {}),
])
def test_legacy_mutators_share_the_activation_target_lock(alice, fake, method, suffix, body):
    accepted = _activate(alice, _review(alice, "legacy-lock"))
    sid = accepted["schedule_id"]
    registered = _registration(sid)
    try:
        with _hold_configuration_lock(TARGET):
            response = alice.request(method.upper(), api_path(f"/schedules/{sid}{suffix}"), json=body)
        assert response.status_code == 409, response.text
        assert response.json()["error_code"] == "playlist_busy"
        current = alice.ok("get", f"/playlist/{TARGET}/routines/context")["routines"]
        exact = next(item for item in current if item["schedule_id"] == sid)
        assert exact["schedule_revision"] == accepted["schedule_revision"]
        assert _registration(sid) == registered
    finally:
        alice.ok("delete", f"/schedules/{sid}")


@pytest.mark.parametrize("suffix,body", [
    ("raid-watch", {"source_playlist_id": SOURCE, "auto_schedule": True}),
    ("raid-watch-search", {"search_query": "jazz", "auto_schedule": True}),
    ("raid-link", {"raid_playlist_id": OTHER, "create_new": False}),
    ("pair", {"archive_playlist_id": OTHER, "archive_playlist_name": "Archive", "create_new": False}),
    ("raid-link", {"create_new": True}),
    ("pair", {"create_new": True}),
])
def test_prerequisite_writes_wait_for_activation_lock(alice, fake, suffix, body):
    before = alice.ok("get", f"/playlist/{TARGET}/routines/context")
    counts = _counts()
    spotify = fake.playlist(TARGET)
    playlist_ids = {item["id"] for item in fake.playlists()}
    with _hold_configuration_lock(TARGET):
        response = alice.post(api_path(f"/playlist/{TARGET}/{suffix}"), json=body)
        if suffix in {"pair", "raid-link"}:
            assert response.status_code == 202, response.text
            operation_id = response.json()["operation_id"]
            # The worker must wait/defer before authentication or any creation.
            time.sleep(2.2)
            pending = alice.get(response.headers["Location"]).json()["operation"]
            assert pending["id"] == operation_id and pending["state"] in {"queued", "running"}
            assert pending["result"] is None
            assert {item["id"] for item in fake.playlists()} == playlist_ids
        else:
            assert response.status_code == 409, response.text
            assert response.json()["error_code"] == "playlist_busy"
    if suffix in {"pair", "raid-link"}:
        completed = alice.wait_operation(response.headers["Location"], operation_id)
        assert completed["state"] == "succeeded", completed
        assert completed["result"]["success"] is True
    else:
        after = alice.ok("get", f"/playlist/{TARGET}/routines/context")
        assert after["dependency_revision"] == before["dependency_revision"]
        assert {item["id"] for item in fake.playlists()} == playlist_ids
    assert _counts() == counts
    assert fake.playlist(TARGET) == spotify


def test_state_and_compatible_settings_remain_available_during_execution(alice, fake):
    accepted = _activate(alice, _review(alice, "execution-independent-state"))
    sid = accepted["schedule_id"]
    alice.write(f"/playlist/{TARGET}/raid-link", json={
        "raid_playlist_id": OTHER, "create_new": False,
    })
    alice.write(f"/playlist/{TARGET}/pair", json={
        "archive_playlist_id": pid("alice-morning-archive"),
        "archive_playlist_name": "Archive", "create_new": False,
    })
    spotify = fake.playlist(TARGET)
    try:
        with hold_playlist_lock(TARGET):
            paused = alice.ok("put", f"/routines/{sid}/state", json={
                "desired_enabled": False, "expected_revision": accepted["schedule_revision"],
            })
            assert paused["state"] == "Paused" and _registration(sid) is None
            resumed = alice.ok("put", f"/routines/{sid}/state", json={
                "desired_enabled": True, "expected_revision": paused["schedule_revision"],
            })
            assert resumed["state"] == "Active"
            for enabled in (False, True):
                legacy = alice.ok("put", f"/schedules/{sid}", json={"is_enabled": enabled})
                assert legacy["schedule"]["is_enabled"] is enabled
            link = alice.ok("put", f"/playlist/{TARGET}/raid-link", json={
                "drip_count": 7, "drip_enabled": True,
            })["raid_link"]
            assert link["drip_count"] == 7 and link["drip_enabled"]
            pair = alice.ok("patch", f"/playlist/{TARGET}/pair", json={
                "auto_archive_on_remove": False,
            })["pair"]
            assert pair["auto_archive_on_remove"] is False
        assert fake.playlist(TARGET) == spotify
    finally:
        alice.ok("delete", f"/schedules/{sid}")
