"""Queued executor results match the isolated fake's actual playlists."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from config import HarnessConfig
from tests.e2e.conftest import pid
from tests.e2e.test_direct_live_actions import (
    _close_result,
    _heading,
    _open_inbox,
    _open_review,
    _remove_row,
    _settled,
)
from tests.paths import api_path

TARGET = pid("alice-morning-mix")
LONG_HAUL = pid("alice-long-haul")
SOURCE = pid("bob-finds")


def _collection(alice):
    link = alice.ok("post", f"/playlist/{TARGET}/raid-link", json={
        "create_new": True, "target_playlist_name": "Morning Mix",
    })["raid_link"]
    alice.ok("post", f"/playlist/{TARGET}/raid-watch", json={
        "source_playlist_id": SOURCE, "source_playlist_name": "Bob's Finds",
        "auto_schedule": False,
    })
    return link["raid_playlist_id"]


def _create_schedule(alice, kind="shuffle", target=LONG_HAUL):
    payload = {
        "job_type": kind, "target_playlist_id": target,
        "target_playlist_name": "Name supplied by browser",
        "schedule_value": "daily",
    }
    if kind in ("shuffle", "raid_and_shuffle"):
        payload["algorithm_name"] = "BasicShuffle"
    if kind.startswith("raid"):
        payload["source_playlist_ids"] = [SOURCE]
    return alice.ok("post", "/schedules/create", json=payload)["schedule"]


def _nav_to_settings(page):
    """Click Settings in whichever navigation this viewport shows."""
    desktop = page.locator('.sh-app-nav-desktop a[data-nav-key="settings"]')
    if desktop.is_visible():
        desktop.click()
        return
    link = page.locator('.sh-app-menu a[data-nav-key="settings"]')
    if not link.is_visible():
        page.locator(".sh-app-menu summary").click()
    link.click()


def _registration(schedule_id):
    """The guarded harness DB's exact trigger/next-fire state, never unpickled."""
    HarnessConfig.assert_safe()
    engine = create_engine(HarnessConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT next_run_time, job_state FROM apscheduler_jobs WHERE id = :job_id"
            ), {"job_id": f"schedule_{schedule_id}"}).one_or_none()
    finally:
        engine.dispose()


@pytest.mark.parametrize("paused", [False, True])
def test_manual_run_identifies_this_invocation_without_changing_registration(alice, fake, paused):
    schedule = _create_schedule(alice)
    sid = schedule["id"]
    try:
        if paused:
            assert alice.ok("post", f"/schedules/{sid}/toggle")["schedule"]["is_enabled"] is False
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
        current = next(s for s in alice.ok("get", f"/playlist/{LONG_HAUL}/schedules")["schedules"] if s["id"] == sid)
        assert current["is_enabled"] is (not paused)
        assert current["schedule_value"] == "daily"
    finally:
        alice.ok("delete", f"/schedules/{sid}")


@pytest.mark.parametrize("kind", ["raid_and_shuffle", "raid_and_drip"])
@pytest.mark.parametrize("paused", [False, True])
def test_collect_for_review_ignores_saved_composite_and_pause_state(alice, fake, kind, paused):
    collection = _collection(alice)
    schedule = _create_schedule(alice, kind, TARGET)
    sid = schedule["id"]
    try:
        if paused:
            alice.ok("post", f"/schedules/{sid}/toggle")
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
        current = next(s for s in alice.ok("get", f"/playlist/{TARGET}/schedules")["schedules"] if s["id"] == sid)
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


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (375, 812)])
def test_workshop_collect_keeps_a_draft_and_drip_saves_it_before_a_fresh_review(
    alice, fake, live_browser, tmp_path, width, height,
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
    drip_url = (APP + api_path(f'/playlist/{TARGET}/drip-now'))
    commit_url = (APP + api_path(f'/workshop/{TARGET}/commit'))
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
        _remove_row(page)
        _open_inbox(page)
        page.locator("#raid-now-btn").wait_for(state="visible")
        # Collect writes only the collection and review inbox, so it runs beside the track draft.
        _open_review(page, page.locator("#raid-now-btn"), "Collect for review").click()
        _settled(page, 1)
        assert page.evaluate("PlaylistOperations.getRecords()[0].complete") is True
        assert page.evaluate("workshopState.workingUris") == original[1:]
        assert fake.uris(TARGET) == original
        assert fake.uris(collection)
        _close_result(page)
        # Drip writes the drafted playlist: Review & save runs the real Save review first. The review
        # replaced Inbox, so Inbox is opened again for it.
        _open_inbox(page)
        page.locator("#drip-now-btn").wait_for(state="visible")
        page.click("#drip-now-btn")
        _heading(page, "Save or discard your draft first")
        assert drip_url not in posts and fake.uris(TARGET) == original
        page.screenshot(path=tmp_path / f"drip-draft-conflict-{width}.png", full_page=True)
        page.click("#live-operation-run")
        page.locator("#workshop-save-panel").wait_for(state="visible")
        page.click("#workshop-review-save")
        _heading(page, "Review: Add collected tracks now")
        assert fake.uris(TARGET) == original[1:]
        assert posts.count(commit_url) == 1 and drip_url not in posts
        page.click("#live-operation-run")
        _settled(page, 2)
        assert page.evaluate("PlaylistOperations.getRecords()[1].complete") is True
        assert page.evaluate("PlaylistOperations.hasAttention()") is False
        assert page.evaluate("workshopState.workingUris") == fake.uris(TARGET)
        assert page.evaluate("workshopState.savedUris") == fake.uris(TARGET)
        assert len(fake.uris(TARGET)) > len(original) - 1
        assert posts.count(drip_url) == 1
        results = page.locator("#live-operation-results").inner_text()
        assert "Morning Mix" in results and "Morning Mix [Raids]" in results
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        page.screenshot(path=tmp_path / f"live-drip-complete-{width}.png", full_page=True)
        assert visit.problems == []
    finally:
        visit.close()


def test_collect_refuses_a_source_unwatched_after_review_and_collects_nothing(
    alice, fake, live_browser,
):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    collection = _collection(alice)
    source_id = alice.ok("get", f"/playlist/{TARGET}/raid-status")["raid_status"]["sources"][0]["id"]
    raid_path = api_path(f"/playlist/{TARGET}/raid-now")
    visit = Visit(live_browser, "desktop", allowed=[(raid_path, 409)])
    page = visit.page
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
        _open_inbox(page)
        page.locator("#raid-now-btn").wait_for(state="visible")
        run = _open_review(page, page.locator("#raid-now-btn"), "Collect for review")
        assert "Bob's Finds" in page.locator("#live-operation-review").inner_text()
        alice.ok("post", f"/playlist/{TARGET}/raid-unwatch", json={"source_id": source_id})
        run.click()
        _settled(page, 1)
        record = page.evaluate("PlaylistOperations.getRecords()[0]")
        assert record["noEffect"] is True
        assert record["operation"]["error_code"] == "review_changed"
        assert record["response"]["execution_state"] == "not_started"
        assert "Review it again" in page.locator("#live-operation-results").inner_text()
        assert fake.uris(collection) == []
        assert alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"] == []
        assert visit.problems == []
    finally:
        visit.close()


def test_collect_only_routine_is_reviewed_as_collect_only_and_stays_paused(
    alice, fake, live_browser,
):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    collection = _collection(alice)
    schedule = _create_schedule(alice, "raid", TARGET)
    sid = schedule["id"]
    alice.ok("post", f"/schedules/{sid}/toggle")
    before = fake.uris(TARGET)
    visit = Visit(live_browser, "desktop")
    page = visit.page
    try:
        visit.log_in()
        page.goto(f"{APP}/schedules", wait_until="networkidle")
        button = page.locator(f'[data-live-operation-key="schedule:{sid}"]')
        run = _open_review(page, button, "Run routine now")
        review = page.locator("#live-operation-review").inner_text()
        assert "Runs this routine once now: Collect for review." in review
        assert "A paused routine stays paused" in review
        assert "Collecting adds nothing to" in review
        assert "Adds up to" not in review and "Shuffles" not in review and "Swaps" not in review
        run.click()
        _settled(page, 1)
        assert page.evaluate("PlaylistOperations.getRecords()[0].complete") is True
        assert fake.uris(TARGET) == before
        assert fake.uris(collection)
        assert alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
        current = next(s for s in alice.ok("get", f"/playlist/{TARGET}/schedules")["schedules"] if s["id"] == sid)
        assert current["is_enabled"] is False
        assert visit.problems == []
    finally:
        visit.close()
        alice.ok("delete", f"/schedules/{sid}")


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (375, 812)])
def test_workshop_draft_leave_offers_save_discard_or_stay(
    alice, fake, live_browser, tmp_path, width, height,
):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    original = fake.uris(TARGET)
    visit = Visit(live_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": width, "height": height})
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    workshop = f"{APP}/workshop/{TARGET}"
    leave = page.locator("#live-operation-leave")
    try:
        visit.log_in()
        page.goto(workshop, wait_until="networkidle")
        _remove_row(page)
        _nav_to_settings(page)
        leave.wait_for(state="visible")
        assert page.evaluate("document.activeElement.id") == "live-operation-leave-heading"
        assert page.locator("#live-operation-leave-save").is_visible()
        assert page.locator("#live-operation-leave-discard").is_visible()
        assert page.locator("#live-operation-leave-confirm").is_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        page.screenshot(path=tmp_path / f"workshop-draft-leave-{width}.png", full_page=True)
        page.click("#live-operation-leave-stay")
        leave.wait_for(state="hidden")
        assert page.url == workshop
        assert page.evaluate("workshopState.workingUris") == original[1:]
        # Review & save stays here and opens the Save review; nothing is saved until Save.
        _nav_to_settings(page)
        page.click("#live-operation-leave-save")
        page.locator("#workshop-save-panel").wait_for(state="visible")
        assert page.url == workshop and fake.uris(TARGET) == original
        page.click("#workshop-review-back")
        _nav_to_settings(page)
        page.click("#live-operation-leave-discard")
        page.wait_for_url(f"{APP}/settings")
        assert fake.uris(TARGET) == original
        assert dialogs == []
        assert visit.problems == []
    finally:
        visit.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (375, 812)])
def test_lost_manual_run_response_retains_unknown_and_blocks_repeat(
    alice, fake, live_browser, tmp_path, width, height,
):
    from tests.e2e.conftest import APP
    from tests.e2e.test_pages import Visit

    schedule = _create_schedule(alice)
    sid = schedule["id"]
    alice.ok("post", f"/schedules/{sid}/toggle")
    before = fake.uris(LONG_HAUL)
    visit = Visit(live_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": width, "height": height})
    posted, receipts = [], []
    run_url = (APP + api_path(f'/schedules/{sid}/run'))

    def lose_response_body(route):
        posted.append(route.request.url)
        response = route.fetch()
        assert response.status == 202
        receipt = response.json()
        receipts.append((response.headers["location"], receipt["operation_id"]))
        # Only this test observer retains the receipt; the browser loses it.
        route.fulfill(status=202, content_type="application/json", body="")

    page.route(run_url, lose_response_body)
    try:
        visit.log_in()
        page.goto(f"{APP}/schedules", wait_until="networkidle")
        button = page.locator(f'[data-live-operation-key="schedule:{sid}"]')
        # The saved routine is reviewed first; Escape cancels before anything is sent.
        _open_review(page, button, "Run routine now")
        page.keyboard.press("Escape")
        page.wait_for_function("() => !PlaylistOperations.isPreparing()")
        assert page.evaluate("PlaylistOperations.getRecords().length") == 0
        assert posted == [] and fake.uris(LONG_HAUL) == before
        _open_review(page, button, "Run routine now").click()
        page.wait_for_function("() => PlaylistOperations.getRecords().length === 1 && !PlaylistOperations.isBusy()")
        assert page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "unknown"
        assert page.evaluate("PlaylistOperations.hasAttention()") is True
        assert button.is_disabled()
        assert "Do not repeat this action" in page.locator("#live-operation-results").inner_text()
        assert len(receipts) == 1
        accepted = alice.wait_operation(*receipts[0])
        assert accepted["state"] == "succeeded", accepted
        assert fake.uris(LONG_HAUL) != before
        assert len(alice.ok("get", f"/schedules/{sid}/history")["history"]) == 1
        assert posted == [run_url]
        assert page.url == f"{APP}/schedules"
        # Even a direct activation of this routine's delegated handler cannot
        # bypass the blocked control after an unknown result.
        assert button.get_attribute("data-schedule-id") == str(sid)
        button.evaluate("control => control.dispatchEvent(new MouseEvent('click', {bubbles: true}))")
        assert posted == [run_url]
        assert page.evaluate("PlaylistOperations.getRecords().length") == 1
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        page.screenshot(path=tmp_path / f"live-unknown-{width}.png", full_page=True)
        # Leaving with an unknown result needs an explicit decision; Stay keeps the details.
        if page.locator("#live-operation-panel").is_visible():
            _close_result(page)
        _nav_to_settings(page)
        page.locator("#live-operation-leave").wait_for(state="visible")
        assert page.locator("#live-operation-leave-confirm").is_visible()
        assert page.locator("#live-operation-leave-discard").is_hidden()
        page.click("#live-operation-leave-stay")
        page.locator("#live-operation-leave").wait_for(state="hidden")
        assert page.url == f"{APP}/schedules"
        current = next(s for s in alice.ok("get", f"/playlist/{LONG_HAUL}/schedules")["schedules"] if s["id"] == sid)
        assert current["is_enabled"] is False
        assert visit.problems == []
    finally:
        visit.close()
        alice.ok("delete", f"/schedules/{sid}")


def test_composite_restoration_retains_completed_collection_and_named_results(
    alice, fake, live_browser,
):
    from tests.e2e.conftest import APP
    from tests.e2e.test_workshop_source_recovery import RecoveryVisit

    collection = _collection(alice)
    alice.ok("put", f"/playlist/{TARGET}/raid-link", json={"drip_enabled": True})
    schedule = _create_schedule(alice, "raid_and_drip", TARGET)
    sid = schedule["id"]
    alice.ok("post", f"/schedules/{sid}/toggle")
    before = fake.uris(TARGET)
    assert fake.uris(collection) == []
    assert alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"] == []
    fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=collection)
    visit = RecoveryVisit(live_browser, "desktop")
    visit.source_urls = {(APP + api_path(f'/schedules/{sid}/run'))}
    page = visit.page
    page.set_viewport_size({"width": 375, "height": 812})
    try:
        visit.log_in()
        page.goto(f"{APP}/schedules", wait_until="networkidle")
        button = page.locator(f'[data-live-operation-key="schedule:{sid}"]')
        _open_review(page, button, "Run routine now").click()
        page.wait_for_function("() => PlaylistOperations.getRecords().length === 1 && !PlaylistOperations.isBusy()")
        record = page.evaluate("PlaylistOperations.getRecords()[0]")
        assert record["aggregate"] == "partial"
        # Every effect is known (the verified restoration included): nothing stays claimed, and the run
        # stays in Attention.
        assert record["blocked"] is False and record["attention"] is True
        assert record["operation"]["retryable"] is False
        assert any(b["kind"] == "pending_stage" and b["status"] == "complete"
                   for b in record["operation"]["bookkeeping"])
        effects = {(r["step"], r["resource"]): r["status"] for r in record["operation"]["resources"]}
        assert effects[("raid", collection)] == "complete"
        assert effects[("drip", TARGET)] == "restored"
        assert effects[("drip", collection)] == "failed"
        assert fake.uris(TARGET) == before
        raid = next(r for r in record["operation"]["resources"] if r["step"] == "raid")
        assert fake.uris(collection) == raid["desired_track_uris"]
        pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
        assert len(pending) == len(fake.uris(collection)) == 5
        assert {p["track_uri"] for p in pending} == set(fake.uris(collection))
        text = page.locator("#live-operation-results").inner_text()
        assert "Morning Mix" in text and "Morning Mix [Raids]" in text
        assert "Restored — baseline verified" in text
        page.locator("#live-operation-results summary").first.click()
        assert "Review inbox: saved" in page.locator("#live-operation-results").inner_text()
        assert "The outcome or response could not be established" not in text
        # Running again is allowed, but only through a fresh review; Escape sends nothing.
        _close_result(page)
        _open_review(page, button, "Run routine now")
        page.keyboard.press("Escape")
        page.wait_for_function("() => !PlaylistOperations.isPreparing()")
        assert len(alice.ok("get", f"/schedules/{sid}/history")["history"]) == 1
        assert [fault["fired"] for fault in fake.faults()] == [1]
        assert visit.problems == []
    finally:
        visit.close()
        alice.ok("delete", f"/schedules/{sid}")


def test_saved_drip_gate_and_direct_one_off_are_separate(alice, fake):
    collection = _collection(alice)
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    schedule = _create_schedule(alice, "drip", TARGET)
    sid = schedule["id"]
    alice.ok("post", f"/schedules/{sid}/toggle")
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
        current = next(s for s in alice.ok("get", f"/playlist/{TARGET}/schedules")["schedules"] if s["id"] == sid)
        assert current["is_enabled"] is False
        assert _registration(sid) == registration
    finally:
        alice.ok("delete", f"/schedules/{sid}")


def test_missing_source_retains_healthy_collection_and_stops_composite(alice, fake):
    collection = _collection(alice)
    missing = pid("a-source-that-does-not-exist")
    alice.ok("post", f"/playlist/{TARGET}/raid-watch", json={
        "source_playlist_id": missing, "source_playlist_name": "Unavailable source",
        "auto_schedule": False,
    })
    schedule = alice.ok("post", "/schedules/create", json={
        "job_type": "raid_and_shuffle", "target_playlist_id": TARGET,
        "target_playlist_name": "Morning Mix", "schedule_value": "daily",
        "algorithm_name": "BasicShuffle", "source_playlist_ids": [SOURCE, missing],
    })["schedule"]
    sid = schedule["id"]
    alice.ok("post", f"/schedules/{sid}/toggle")
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
    schedule = _create_schedule(alice, "raid_and_drip", TARGET)
    sid = schedule["id"]
    alice.ok("post", f"/schedules/{sid}/toggle")
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
            "raid": "complete", "drip": "not_started",
        }
        assert len(fake.uris(collection)) == 5
        pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
        assert {p["track_uri"] for p in pending} == set(fake.uris(collection))
        assert fake.playlist(TARGET) == target_before
        history = alice.ok("get", f"/schedules/{sid}/history")["history"]
        assert len(history) == 1
        assert history[0]["id"] == body["result"]["execution_id"]
        assert history[0]["status"] == "failed"
        current = next(s for s in alice.ok("get", f"/playlist/{TARGET}/schedules")["schedules"]
                       if s["id"] == sid)
        assert current["last_status"] == "failed"
        assert current["is_enabled"] is False
    finally:
        alice.ok("delete", f"/schedules/{sid}")
