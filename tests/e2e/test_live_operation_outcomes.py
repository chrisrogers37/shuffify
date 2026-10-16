"""Queued executor results match the isolated fake's actual playlists."""

import os
import time
from datetime import datetime

import pytest
from playwright.sync_api import expect
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from config import HarnessConfig
from shuffify.models.db import Schedule
from shuffify.services.schedule_utils import schedule_revision
from shuffify.services.worker_control import WorkerControlClient
from tests.e2e.conftest import pid
from tests.e2e.legacy_fixtures import seed_unavailable_legacy_source
from tests.e2e.test_direct_live_actions import _lose_reply, _panel
from tests.paths import api_path
from tests.routine_flow import add_playlist_source, create_routine, list_schedules, save_setup, set_routine_enabled

TARGET = pid("alice-morning-mix")
LONG_HAUL = pid("alice-long-haul")
SOURCE = pid("bob-finds")


def _collection(alice):
    link = alice.write(
        f"/playlist/{TARGET}/raid-link",
        json={
            "create_new": True,
            "target_playlist_name": "Morning Mix",
        },
    )["raid_link"]
    add_playlist_source(alice, TARGET, SOURCE)
    return link["raid_playlist_id"]


def _create_schedule(alice, kind="shuffle", target=LONG_HAUL):
    return create_routine(alice, target, job_type=kind)


def _nav_to_settings(page):
    """Open Settings through the visible React navigation."""
    menu = page.locator(".sh-app-menu")
    if menu.is_visible() and menu.get_attribute("open") is None:
        menu.locator("summary").click()
    page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role(
        "link",
        name="Settings",
        exact=True,
    ).click()


def _registration(schedule_id):
    """Wait for the private memory observation to match the guarded stored definition."""
    HarnessConfig.assert_safe()
    engine = create_engine(HarnessConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    try:
        with Session(engine) as storage:
            row = storage.get(Schedule, schedule_id)
            enabled = row is not None and row.is_enabled
            revision = schedule_revision(row) if row is not None else None
    finally:
        engine.dispose()
    client = WorkerControlClient(url=os.environ["WORKER_CONTROL_URL"], token=os.environ["WORKER_CONTROL_TOKEN"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = client.registrations([schedule_id])
        if snapshot and snapshot.get("leader") is True and snapshot.get("scheduler_running") is True:
            job = snapshot["registrations"][str(schedule_id)]
            if not enabled and job is None:
                return None
            if enabled and isinstance(job, dict) and job.get("installed_revision") == revision:
                assert datetime.fromisoformat(job["next_run_at"]).utcoffset() is not None
                return {
                    key: job[key] for key in ("schedule_id", "cadence_fingerprint", "next_run_at", "effective_timezone")
                }
        time.sleep(0.02)
    raise AssertionError("The worker did not observe the current routine definition within five seconds")


@pytest.mark.parametrize("paused", [False, True])
def test_manual_run_identifies_this_invocation_without_changing_registration(alice, fake, paused):
    schedule = _create_schedule(alice)
    sid = schedule["id"]
    try:
        if paused:
            assert set_routine_enabled(alice, sid, False)["schedule"]["is_enabled"] is False
        registration = _registration(sid)
        assert (registration is None) is paused
        older = alice.write(f"/schedules/{sid}/run")
        before = fake.uris(LONG_HAUL)
        run = alice.write(f"/schedules/{sid}/run")
        after = fake.uris(LONG_HAUL)
        history = alice.ok("get", f"/schedules/{sid}/history")["history"]
        assert len(history) == 2
        assert run["result"]["execution_id"] != older["result"]["execution_id"]
        assert run["result"]["execution_id"] == history[0]["id"]
        assert run["result"]["started_at"] == history[0]["started_at"]
        assert run["result"]["execution_state"] == "recorded"
        assert run["operation"]["aggregate_status"] == "complete"
        resource = next(r for r in run["operation"]["resources"] if r["resource"] == LONG_HAUL)
        assert resource["resource_name"] == "Long Haul"
        assert resource["baseline_track_uris"] == before
        assert resource["desired_track_uris"] == resource["current_track_uris"] == after
        assert sorted(after) == sorted(before) and after != before
        assert _registration(sid) == registration
        current = next(s for s in list_schedules(alice, LONG_HAUL) if s["id"] == sid)
        assert current["is_enabled"] is (not paused)
        assert current["schedule_value"] == "daily"
    finally:
        alice.ok("delete", f"/schedules/{sid}")


@pytest.mark.parametrize("kind", ["raid_and_shuffle", "raid_and_drip"])
@pytest.mark.parametrize("paused", [False, True])
def test_collect_for_review_ignores_saved_composite_and_pause_state(alice, fake, kind, paused):
    collection = _collection(alice)
    if kind == "raid_and_drip":
        save_setup(alice, TARGET, [{"op": "set_drip", "drip_enabled": True}])
    schedule = _create_schedule(alice, kind, TARGET)
    sid = schedule["id"]
    try:
        if paused:
            set_routine_enabled(alice, sid, False)
        registration = _registration(sid)
        before = fake.playlist(TARGET)
        run = alice.write(f"/playlist/{TARGET}/raid-now", json={})
        assert run["execution_id"] is not None
        assert run["operation"]["kind"] == "raid"
        assert run["operation"]["aggregate_status"] == "complete"
        assert run["tracks_added"] == 5
        assert fake.playlist(TARGET) == before
        assert len(fake.uris(collection)) == 5
        assert all(r["resource"] != TARGET or not r["dispatched"] for r in run["operation"]["resources"])
        assert alice.ok("get", f"/schedules/{sid}/history")["history"] == []
        assert _registration(sid) == registration
        current = next(s for s in list_schedules(alice, TARGET) if s["id"] == sid)
        assert current["job_type"] == kind and current["is_enabled"] is (not paused)
    finally:
        alice.ok("delete", f"/schedules/{sid}")


def test_drip_reports_verified_compensation_when_collection_removal_is_rejected(alice, fake):
    collection = _collection(alice)
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    before = fake.uris(TARGET)
    collected = fake.uris(collection)
    fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=collection)
    queued = alice.queued(f"/playlist/{TARGET}/drip-now", json={})
    assert queued["state"] == "rolled_back", queued
    body = queued["result"]
    assert body["success"] is False
    operation = body["operation"]
    assert operation["aggregate_status"] == "partial"
    assert operation["retryable"] is False
    resources = {r["resource"]: r for r in operation["resources"]}
    # The real API wraps the rejection as a zero-completed-batch failure.
    # Existing compensation restores the earlier target prepend, but never
    # rewrites the rejected collection. Both facts must remain distinguishable.
    assert resources[TARGET]["status"] == "restored"
    assert resources[TARGET]["resource_name"] == "Morning Mix"
    assert resources[TARGET]["current_track_uris"] == fake.uris(TARGET) == before
    assert resources[TARGET]["desired_track_uris"] != before
    assert resources[collection]["status"] == "failed"
    assert fake.uris(collection) == collected
    assert resources[collection]["resource_name"] == "Morning Mix [Raids]"
    assert [f["fired"] for f in fake.faults()] == [1]


@pytest.fixture(scope="module")
def live_browser():
    from playwright.sync_api import sync_playwright

    from tests.e2e.test_pages import CHROMIUM_ARGS

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        yield browser
        browser.close()


def _workshop():
    from tests.e2e import test_playlist_workspace

    return test_playlist_workspace


def _source_run(page, label):
    _panel(page).get_by_role("button", name=label, exact=True).click()
    run = _panel(page).get_by_role("button", name="Run reviewed action", exact=True)
    expect(run).to_be_visible()
    return run


def _routine_run(page, schedule_id):
    page.locator(f"#routine-{schedule_id}").get_by_role("button", name="Run now", exact=True).click()
    run = _panel(page).get_by_role("button", name="Run reviewed routine", exact=True)
    expect(run).to_be_visible()
    return run


def _run_operation(page, run, path, alice, *, state="succeeded"):
    from tests.e2e.conftest import APP

    with page.expect_response(lambda reply: reply.url == APP + path and reply.request.method == "POST") as pending:
        run.click()
    reply = pending.value
    assert reply.status == 202, reply.text()
    accepted = reply.json()
    operation = alice.wait_operation(reply.headers["location"], accepted["operation_id"])
    assert operation["state"] == state, operation
    expect(
        _panel(page).get_by_role(
            "heading", name=f"Operation #{operation['id']} · {state.replace('_', ' ')}", exact=True
        )
    ).to_be_visible(timeout=45000)
    return operation


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (375, 812)])
def test_workshop_collect_keeps_a_draft_and_drip_saves_it_before_a_fresh_review(
    alice,
    fake,
    live_browser,
    tmp_path,
    width,
    height,
):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    collection = _collection(alice)
    original = fake.uris(TARGET)
    visit = Visit(live_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": width, "height": height})
    posts = []
    page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
    drip_url = APP + api_path(f"/playlist/{TARGET}/drip-now")
    commit_url = APP + api_path(f"/workshop/{TARGET}/commit")
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
        _workshop()._remove_row(page)
        _workshop()._open_inbox(page)
        collected = _run_operation(
            page, _source_run(page, "Collect for review"), api_path(f"/playlist/{TARGET}/raid-now"), alice
        )
        assert collected["result"]["operation"]["aggregate_status"] == "complete"
        assert _workshop()._uris(page) == original[1:] and fake.uris(TARGET) == original
        assert fake.uris(collection)
        _workshop()._close_tool(page)
        _workshop()._open_inbox(page)
        _panel(page).get_by_role("button", name="Add collected tracks now", exact=True).click()
        dialog = page.get_by_role("dialog", name="Keep your local draft?", exact=True)
        expect(dialog.get_by_role("button", name="Review & save", exact=True)).to_be_visible()
        assert drip_url not in posts and fake.uris(TARGET) == original
        page.screenshot(path=tmp_path / f"drip-draft-conflict-{width}.png", full_page=True)
        dialog.get_by_role("button", name="Review & save", exact=True).click()
        expect(_panel(page).get_by_role("heading", name="Review save", exact=True)).to_be_visible()
        _workshop()._save_to_spotify(page, TARGET)
        assert fake.uris(TARGET) == original[1:]
        assert posts.count(commit_url) == 1 and drip_url not in posts
        _workshop()._close_tool(page)
        _workshop()._open_inbox(page)
        added = _run_operation(
            page, _source_run(page, "Add collected tracks now"), api_path(f"/playlist/{TARGET}/drip-now"), alice
        )
        assert added["result"]["operation"]["aggregate_status"] == "complete"
        assert len(fake.uris(TARGET)) > len(original) - 1 and posts.count(drip_url) == 1
        expect(_panel(page)).to_contain_text("Morning Mix")
        expect(_panel(page)).to_contain_text("Add collected tracks")
        _workshop()._close_tool(page)
        # A confirmed live target write must update the displayed saved baseline, preserving its exact order.
        expect(_workshop()._rows(page)).to_have_count(len(fake.uris(TARGET)))
        assert _workshop()._uris(page) == fake.uris(TARGET)
        assert _workshop()._no_sideways_scroll(page)
        page.screenshot(path=tmp_path / f"live-drip-complete-{width}.png", full_page=True)
        assert visit.problems == []
    finally:
        visit.close()


def test_collect_refuses_a_source_unwatched_after_review_and_collects_nothing(alice, fake, live_browser):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    collection = _collection(alice)
    source_id = alice.ok("get", f"/playlist/{TARGET}/raid-status")["raid_status"]["sources"][0]["id"]
    raid_path = api_path(f"/playlist/{TARGET}/raid-now")
    visit = Visit(live_browser, "desktop")
    page = visit.page
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
        _workshop()._open_inbox(page)
        run = _source_run(page, "Collect for review")
        expect(_panel(page)).to_contain_text("Bob's Finds")
        alice.ok("post", f"/playlist/{TARGET}/raid-unwatch", json={"source_id": source_id})
        rejected = _run_operation(page, run, raid_path, alice, state="failed")
        result = rejected["result"]
        assert result["operation"]["error_code"] == "review_changed"
        assert result["execution_state"] == "not_started"
        assert all(not resource["dispatched"] for resource in result["operation"]["resources"])
        assert fake.uris(collection) == []
        assert alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"] == []
        assert visit.problems == []
    finally:
        visit.close()


def test_collect_only_routine_is_reviewed_as_collect_only_and_stays_paused(alice, fake, live_browser):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    collection = _collection(alice)
    sid = _create_schedule(alice, "raid", TARGET)["id"]
    set_routine_enabled(alice, sid, False)
    before = fake.uris(TARGET)
    visit = Visit(live_browser, "desktop")
    page = visit.page
    try:
        visit.log_in()
        page.goto(f"{APP}/routines", wait_until="networkidle")
        with page.expect_response(APP + api_path(f"/schedules/{sid}/run-context")) as review:
            run = _routine_run(page, sid)
        context = review.value.json()["context"]
        assert context["steps"] == ["raid"]
        assert not any(
            resource["resource"] == TARGET
            and resource["domain"] == "spotify"
            and resource["access"] == "write"
            for resource in context["resources"]
        )
        assert any(
            resource["resource"] == TARGET
            and resource["domain"] == "inbox"
            and resource["access"] == "write"
            and resource["mutation_type"] == "inbox_state"
            for resource in context["resources"]
        )
        expect(_panel(page)).to_contain_text(f"Collect · Routine #{sid}")
        completed = _run_operation(page, run, api_path(f"/schedules/{sid}/run"), alice)
        assert completed["result"]["operation"]["aggregate_status"] == "complete"
        assert fake.uris(TARGET) == before and fake.uris(collection)
        assert alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
        current = next(row for row in list_schedules(alice, TARGET) if row["id"] == sid)
        assert current["is_enabled"] is False and visit.problems == []
    finally:
        visit.close()
        alice.ok("delete", f"/schedules/{sid}")


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (375, 812)])
def test_workshop_draft_leave_offers_save_discard_or_stay(alice, fake, live_browser, tmp_path, width, height):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    original = fake.uris(TARGET)
    visit = Visit(live_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": width, "height": height})
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    workshop = f"{APP}/workshop/{TARGET}"
    try:
        visit.log_in()
        page.goto(workshop, wait_until="networkidle")
        _workshop()._remove_row(page)
        _nav_to_settings(page)
        leave = page.get_by_role("dialog", name="Keep your local draft?", exact=True)
        expect(leave.get_by_role("button", name="Stay", exact=True)).to_be_focused()
        expect(leave.get_by_role("button", name="Review & save", exact=True)).to_be_visible()
        expect(leave.get_by_role("button", name="Discard", exact=True)).to_be_visible()
        assert _workshop()._no_sideways_scroll(page)
        page.screenshot(path=tmp_path / f"workshop-draft-leave-{width}.png", full_page=True)
        leave.get_by_role("button", name="Stay", exact=True).click()
        expect(leave).to_be_hidden()
        assert page.url == workshop and _workshop()._uris(page) == original[1:]
        _nav_to_settings(page)
        leave.get_by_role("button", name="Review & save", exact=True).click()
        expect(_panel(page).get_by_role("heading", name="Review save", exact=True)).to_be_visible()
        assert page.url == workshop and fake.uris(TARGET) == original
        _workshop()._close_tool(page)
        _nav_to_settings(page)
        leave.get_by_role("button", name="Discard", exact=True).click()
        expect(page).to_have_url(f"{APP}/settings")
        assert fake.uris(TARGET) == original and dialogs == [] and visit.problems == []
    finally:
        visit.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (375, 812)])
def test_lost_manual_run_response_retains_unknown_and_blocks_repeat(alice, fake, live_browser, tmp_path, width, height):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    sid = _create_schedule(alice)["id"]
    set_routine_enabled(alice, sid, False)
    before = fake.uris(LONG_HAUL)
    visit = Visit(live_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": width, "height": height})
    posted = []
    run_url = APP + api_path(f"/schedules/{sid}/run")
    lost = _lose_reply(posted)
    page.route(run_url, lost)
    try:
        visit.log_in()
        page.goto(f"{APP}/routines", wait_until="networkidle")
        _routine_run(page, sid)
        page.keyboard.press("Escape")
        expect(_panel(page)).to_have_count(0)
        assert posted == [] and fake.uris(LONG_HAUL) == before
        _routine_run(page, sid).click()
        expect(_panel(page).get_by_role("button", name="Check the same run request", exact=True)).to_be_visible()
        settled = alice.wait_operation(lost.location, lost.operation_id)
        assert settled["state"] == "succeeded" and fake.uris(LONG_HAUL) != before
        assert len(alice.ok("get", f"/schedules/{sid}/history")["history"]) == 1 and posted == [run_url]
        _workshop()._close_tool(page)
        card = page.locator(f"#routine-{sid}")
        expect(card.get_by_role("button", name="Delete routine", exact=True)).to_be_disabled()
        expect(card.get_by_role("button", name="Run now", exact=True)).to_have_count(0)
        card.get_by_role("button", name="Check run request", exact=True).click()
        expect(_panel(page).get_by_role("button", name="Check the same run request", exact=True)).to_be_visible()
        assert posted == [run_url], "opening the retained review replayed its mutation"
        _workshop()._close_tool(page)
        page.screenshot(path=tmp_path / f"live-unknown-{width}.png", full_page=True)
        _nav_to_settings(page)
        leave = page.get_by_role("dialog", name="Leave this page?", exact=True)
        expect(leave).to_contain_text("unconfirmed")
        leave.get_by_role("button", name="Stay", exact=True).click()
        expect(page).to_have_url(f"{APP}/routines")
        assert posted == [run_url]
        current = next(row for row in list_schedules(alice, LONG_HAUL) if row["id"] == sid)
        assert current["is_enabled"] is False and visit.problems == []
    finally:
        visit.close()
        alice.ok("delete", f"/schedules/{sid}")


def test_composite_restoration_retains_completed_collection_and_named_results(alice, fake, live_browser):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    collection = _collection(alice)
    alice.ok("put", f"/playlist/{TARGET}/raid-link", json={"drip_enabled": True})
    sid = _create_schedule(alice, "raid_and_drip", TARGET)["id"]
    set_routine_enabled(alice, sid, False)
    before = fake.uris(TARGET)
    fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=collection)
    visit = Visit(live_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": 375, "height": 812})
    try:
        visit.log_in()
        page.goto(f"{APP}/routines", wait_until="networkidle")
        operation = _run_operation(
            page, _routine_run(page, sid), api_path(f"/schedules/{sid}/run"), alice, state="rolled_back"
        )
        rich = operation["result"]["operation"]
        assert rich["aggregate_status"] == "partial" and rich["retryable"] is False
        assert any(item["kind"] == "pending_stage" and item["status"] == "complete" for item in rich["bookkeeping"])
        effects = {(row["step"], row["resource"]): row["status"] for row in rich["resources"]}
        assert effects[("raid", collection)] == "complete"
        assert effects[("drip", TARGET)] == "restored" and effects[("drip", collection)] == "failed"
        assert fake.uris(TARGET) == before
        raid = next(row for row in rich["resources"] if row["step"] == "raid")
        assert fake.uris(collection) == raid["desired_track_uris"]
        pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
        assert len(pending) == len(fake.uris(collection)) == 5
        assert {row["track_uri"] for row in pending} == set(fake.uris(collection))
        text = _panel(page).inner_text()
        assert "Target playlist: Restoration verified" in text
        assert "Review inbox staging: Verified" in text
        _workshop()._close_tool(page)
        expect(
            page.locator(f"#routine-{sid}").get_by_role("button", name="Run now", exact=True)
        ).to_be_disabled()
        page.reload(wait_until="networkidle")
        _routine_run(page, sid)
        page.keyboard.press("Escape")
        assert len(alice.ok("get", f"/schedules/{sid}/history")["history"]) == 1
        assert [fault["fired"] for fault in fake.faults()] == [1] and visit.problems == []
    finally:
        visit.close()
        alice.ok("delete", f"/schedules/{sid}")


def test_saved_drip_gate_and_direct_one_off_are_separate(alice, fake):
    collection = _collection(alice)
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    save_setup(alice, TARGET, [{"op": "set_drip", "drip_enabled": True}])
    schedule = _create_schedule(alice, "drip", TARGET)
    sid = schedule["id"]
    set_routine_enabled(alice, sid, False)
    save_setup(alice, TARGET, [{"op": "set_drip", "drip_enabled": False}])
    before = fake.uris(TARGET)
    collected = fake.uris(collection)
    try:
        registration = _registration(sid)
        run = alice.queued(f"/schedules/{sid}/run")
        assert run["state"] == "failed", run
        body = run["result"]
        assert body["success"] is False
        assert body["operation"]["aggregate_status"] == "partial"
        assert body["result"]["execution_id"] is not None
        assert body["operation"]["retryable"] is True
        assert not any(r["dispatched"] for r in body["operation"]["resources"])
        assert fake.uris(TARGET) == before and fake.uris(collection) == collected
        direct = alice.write(f"/playlist/{TARGET}/drip-now", json={})
        assert direct["execution_id"] != body["result"]["execution_id"]
        assert direct["operation"]["aggregate_status"] == "complete"
        assert len(fake.uris(TARGET)) > len(before)
        assert len(fake.uris(collection)) < len(collected)
        assert alice.ok("get", f"/playlist/{TARGET}/raid-status")["raid_status"]["raid_link"]["drip_enabled"] is False
        history = alice.ok("get", f"/schedules/{sid}/history")["history"]
        assert len(history) == 1 and history[0]["id"] == body["result"]["execution_id"]
        current = next(s for s in list_schedules(alice, TARGET) if s["id"] == sid)
        assert current["is_enabled"] is False
        assert _registration(sid) == registration
    finally:
        alice.ok("delete", f"/schedules/{sid}")


def test_missing_source_retains_healthy_collection_and_stops_composite(alice, fake):
    collection = _collection(alice)
    missing = pid("a-source-that-does-not-exist")
    schedule = _create_schedule(alice, "raid_and_shuffle", TARGET)
    sid = schedule["id"]
    set_routine_enabled(alice, sid, False)
    seed_unavailable_legacy_source(alice, TARGET, sid, missing, legacy_ids=[SOURCE, missing])
    before = fake.playlist(TARGET)
    try:
        queued = alice.queued(f"/schedules/{sid}/run")
        assert queued["state"] == "failed", queued
        body = queued["result"]
        assert body["success"] is False
        operation = body["operation"]
        assert operation["aggregate_status"] == "partial"
        assert operation["retryable"] is False
        failed = next(b for b in operation["bookkeeping"] if b["kind"] == "source_resolution")
        assert failed["status"] == "failed"
        assert any(s["source_playlist_id"] == missing for s in failed["failed_sources"])
        assert len(fake.uris(collection)) == 5
        pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
        assert len(pending) == 5
        assert {p["track_uri"] for p in pending} == set(fake.uris(collection))
        assert fake.playlist(TARGET) == before
        assert operation["steps"][-1]["step"] == "shuffle"
        assert operation["steps"][-1]["status"] == "not_started"
        assert all(r["resource"] != TARGET or not r["dispatched"] for r in operation["resources"])
    finally:
        alice.ok("delete", f"/schedules/{sid}")


def test_completed_collection_before_disabled_drip_is_recorded_as_partial(alice, fake):
    collection = _collection(alice)
    save_setup(alice, TARGET, [{"op": "set_drip", "drip_enabled": True}])
    schedule = _create_schedule(alice, "raid_and_drip", TARGET)
    sid = schedule["id"]
    set_routine_enabled(alice, sid, False)
    save_setup(alice, TARGET, [{"op": "set_drip", "drip_enabled": False}])
    target_before = fake.playlist(TARGET)
    try:
        queued = alice.queued(f"/schedules/{sid}/run")
        assert queued["state"] == "failed", queued
        body = queued["result"]
        operation = body["operation"]
        assert body["success"] is False
        assert operation["aggregate_status"] == "partial"
        assert operation["retryable"] is False
        assert {s["step"]: s["status"] for s in operation["steps"]} == {
            "raid": "complete",
            "drip": "not_started",
        }
        assert len(fake.uris(collection)) == 5
        pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
        assert {p["track_uri"] for p in pending} == set(fake.uris(collection))
        assert fake.playlist(TARGET) == target_before
        history = alice.ok("get", f"/schedules/{sid}/history")["history"]
        assert len(history) == 1
        assert history[0]["id"] == body["result"]["execution_id"]
        assert history[0]["status"] == "failed"
        current = next(s for s in list_schedules(alice, TARGET) if s["id"] == sid)
        assert current["last_status"] == "failed"
        assert current["is_enabled"] is False
    finally:
        alice.ok("delete", f"/schedules/{sid}")
