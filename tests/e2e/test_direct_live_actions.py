"""Direct actions observed through the app and the isolated Spotify fake."""

import re
import time
from collections import Counter

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, App, hold_playlist_lock, pid
from tests.e2e.test_pages import CHROMIUM_ARGS, Visit
from tests.paths import api_path

TARGET = pid("alice-morning-mix")
LONG_HAUL = pid("alice-long-haul")


def _direct(body, kind):
    assert body["operation"]["kind"] == kind
    assert not {"execution_id", "started_at", "execution_state", "tracks_added", "tracks_total"}.intersection(body)
    return body["operation"]


def _snapshot(alice, uris):
    return alice.ok(
        "post",
        f"/playlist/{TARGET}/snapshots",
        json={
            "playlist_name": "Morning Mix",
            "track_uris": uris,
            "trigger_description": "Before rearranging",
        },
    )["snapshot"]["id"]


def test_direct_shuffle_reads_external_changes_and_undo_preserves_occurrences(alice, fake):
    alice.write(f"/shuffle/{LONG_HAUL}", data={"algorithm": "BasicShuffle"})
    stale = fake.uris(LONG_HAUL)
    other = App("alice", fake.display_name("alice"))
    external = [stale[-1]] + stale[1:] + [stale[-1]]
    other.write(
        f"/workshop/{LONG_HAUL}/commit",
        json={
            "baseline_track_uris": stale,
            "track_uris": external,
        },
    )
    body = alice.write(
        f"/shuffle/{LONG_HAUL}",
        data={
            "algorithm": "BasicShuffle",
        },
    )
    operation = _direct(body, "shuffle")
    assert operation["aggregate_status"] == "complete"
    resource = operation["resources"][0]
    assert resource["baseline_track_uris"] == external
    assert resource["current_track_uris"] == resource["desired_track_uris"] == fake.uris(LONG_HAUL)
    assert Counter(fake.uris(LONG_HAUL)) == Counter(external)
    assert fake.uris(LONG_HAUL) != external
    undo = alice.write(f"/undo/{LONG_HAUL}")
    assert _direct(undo, "undo")["aggregate_status"] == "complete"
    assert fake.uris(LONG_HAUL) == external


def test_snapshot_restore_records_the_actual_before_order_for_undo(alice, fake):
    saved = fake.uris(TARGET)
    snapshot_id = _snapshot(alice, saved)
    alice.write(f"/shuffle/{TARGET}", data={"algorithm": "BasicShuffle"})
    shuffled = fake.uris(TARGET)
    other = App("alice", fake.display_name("alice"))
    external = [shuffled[-1]] + shuffled[:-2]
    other.write(
        f"/workshop/{TARGET}/commit",
        json={
            "baseline_track_uris": shuffled,
            "track_uris": external,
        },
    )
    restored = alice.write(f"/snapshots/{snapshot_id}/restore")
    operation = _direct(restored, "snapshot_restore")
    assert operation["aggregate_status"] == "complete"
    assert operation["resources"][0]["baseline_track_uris"] == external
    assert operation["resources"][0]["current_track_uris"] == saved
    assert restored["playlist_id"] == TARGET and fake.uris(TARGET) == saved
    alice.write(f"/undo/{TARGET}")
    assert fake.uris(TARGET) == external


def test_direct_later_batch_rate_limit_resumes_the_same_frozen_operation(alice, fake):
    before = fake.uris(LONG_HAUL)
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="rate_limit", retry_after=1, playlist_id=LONG_HAUL)
    completed = alice.queued(f"/shuffle/{LONG_HAUL}", data={"algorithm": "BasicShuffle"})
    assert completed["state"] == "succeeded", completed
    operation = _direct(completed["result"], "shuffle")
    assert operation["aggregate_status"] == "complete"
    resource = operation["resources"][0]
    assert resource["baseline_track_uris"] == before
    assert Counter(resource["desired_track_uris"]) == Counter(before)
    assert fake.uris(LONG_HAUL) == resource["desired_track_uris"] == resource["current_track_uris"]
    assert [fault["fired"] for fault in fake.faults()] == [1]


def test_privacy_uses_strict_boolean_and_changes_only_metadata(alice, fake):
    before = fake.playlist(TARGET)
    desired = not before["public"]
    for invalid in ("false", 0, None, [], {}):
        response = alice.post(api_path(f"/playlist/{TARGET}/toggle-visibility"), json={"public": invalid})
        assert response.status_code == 400
        assert _direct(response.json(), "privacy")["resources"] == []
        assert fake.playlist(TARGET) == before
    changed = alice.write(f"/playlist/{TARGET}/toggle-visibility", json={"public": desired})
    operation = _direct(changed, "privacy")
    assert operation["aggregate_status"] == "complete" and changed["public"] is desired
    assert operation["resources"][0]["current_metadata"]["public"] is desired
    assert fake.playlist(TARGET) == {**before, "public": desired}
    repeated = alice.write(f"/playlist/{TARGET}/toggle-visibility", json={"public": desired})
    resource = _direct(repeated, "privacy")["resources"][0]
    assert resource["status"] == "not_required" and not resource["dispatched"]
    assert fake.playlist(TARGET) == {**before, "public": desired}


@pytest.mark.parametrize("kind", ["shuffle", "undo", "snapshot_restore"])
def test_direct_target_actions_honor_the_shared_postgres_lock(alice, fake, kind):
    baseline = fake.uris(TARGET)
    snapshot_id = _snapshot(alice, baseline)
    alice.write(f"/shuffle/{TARGET}", data={"algorithm": "BasicShuffle"})
    before = fake.playlist(TARGET)
    path = api_path(f"/snapshots/{snapshot_id}/restore" if kind == "snapshot_restore" else f"/{kind}/{TARGET}")
    with hold_playlist_lock(TARGET):
        response = alice.post(path, data={"algorithm": "BasicShuffle"} if kind == "shuffle" else None)
        assert response.status_code == 202, response.text
        operation_id = response.json()["operation_id"]
        time.sleep(2.1)  # The worker must defer after its bounded target-lock wait.
        assert fake.playlist(TARGET) == before
        active = alice.get(response.headers["Location"]).json()["operation"]
        assert active["id"] == operation_id and active["state"] in {"queued", "running"}
        busy = alice.post(path, data={"algorithm": "BasicShuffle"} if kind == "shuffle" else None)
        assert busy.status_code == 409
        assert busy.json()["operation_id"] == operation_id
        assert fake.playlist(TARGET) == before
    completed = alice.wait_operation(response.headers["Location"], operation_id)
    assert completed["state"] == "succeeded", completed


@pytest.fixture(scope="module")
def direct_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        yield browser
        browser.close()


def _panel(page):
    return page.locator("section.sh-task-panel:not([hidden])")


def _choose_shuffle(page, playlist_id):
    row = page.locator(f'.sh-library-row[data-playlist-id="{playlist_id}"]')
    details = row.locator(":scope > details")
    if details.get_attribute("open") is None:
        details.locator(":scope > summary").click()
    options = row.locator("details details")
    if options.get_attribute("open") is None:
        options.locator(":scope > summary").click()
    row.get_by_role("combobox", name="Shuffle method", exact=True).select_option("BasicShuffle")
    return row


def _close_result(page):
    _panel(page).get_by_role("button", name="Close", exact=True).click()


def _open_review(page, trigger, label=None):
    trigger.click()
    run = _panel(page).get_by_role(
        "button", name=re.compile(r"^(Run (shuffle|restore|visibility change)|Change visibility|Restore snapshot now)$")
    )
    expect(run).to_be_visible()
    return run


def _heading(page, heading):
    expect(_panel(page).get_by_role("heading", name=heading, exact=True)).to_be_visible()


def _open_row_actions(page, row):
    row.get_by_role("button", name=re.compile("^Actions for ")).click()
    return row


def _settled(page, count=None):
    status = _panel(page).locator('section[aria-label="Retained operation result"] [role="status"]')
    expect(status).to_have_text(re.compile(r"^(succeeded|failed|rolled back|needs attention)$"), timeout=150000)


def _open_tool(page, tool):
    from tests.e2e.test_playlist_workspace import _open_tool as open_tool

    open_tool(page, {"details": "Playlist details", "routines": "Routines"}.get(tool, tool))


def _close_tool(page, tool=None):
    from tests.e2e.test_playlist_workspace import _close_tool as close_tool

    close_tool(page)


def _open_inbox(page):
    _open_tool(page, "inbox")


def _remove_row(page, index=0):
    from tests.e2e.test_playlist_workspace import _remove_row as remove_row

    remove_row(page, index)


def _lose_reply(posted):
    def handle(route):
        posted.append(route.request.url)
        response = route.fetch()
        assert response.status == 202
        handle.operation_id = response.json()["operation_id"]
        handle.location = response.headers["location"]
        route.fulfill(status=202, content_type="application/json", body="")

    return handle


def _run_accepted(page, run, path, alice, *, state="succeeded"):
    with page.expect_response(lambda r: r.url == APP + path and r.request.method == "POST") as pending:
        run.click()
    response = pending.value
    assert response.status == 202, response.text()
    accepted = response.json()
    assert accepted["operation_id"] == accepted["operation"]["id"]
    operation = alice.wait_operation(response.headers["location"], accepted["operation_id"])
    assert operation["state"] == state, operation
    expect(_panel(page)).to_contain_text(state.replace("_", " "), timeout=45000)
    return operation


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (360, 800)])
def test_library_shuffle_and_undo_show_verified_results_without_navigation(
    alice,
    fake,
    direct_browser,
    tmp_path,
    width,
    height,
):
    before = fake.uris(TARGET)
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": width, "height": height})
    posts = []
    page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        assert posts == [] and fake.uris(TARGET) == before
        run = _open_review(page, card.get_by_role("button", name=re.compile("^Shuffle now")))
        first = _run_accepted(page, run, api_path(f"/shuffle/{TARGET}"), alice)
        assert first["result"]["operation"]["aggregate_status"] == "complete"
        assert fake.uris(TARGET) != before and Counter(fake.uris(TARGET)) == Counter(before)
        assert page.url == f"{APP}/library"
        page.screenshot(path=tmp_path / f"library-direct-{width}.png", full_page=True)
        _close_result(page)
        run = _open_review(page, card.get_by_role("button", name="Restore previous saved version now", exact=True))
        second = _run_accepted(page, run, api_path(f"/undo/{TARGET}"), alice)
        assert second["id"] != first["id"] and fake.uris(TARGET) == before
        assert posts.count(APP + api_path(f"/shuffle/{TARGET}")) == 1
        assert posts.count(APP + api_path(f"/undo/{TARGET}")) == 1
        _close_result(page)
        assert card.get_by_role("combobox", name="Shuffle method", exact=True).input_value() == "BasicShuffle"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert visit.problems == []
    finally:
        visit.close()


def test_lost_library_shuffle_response_blocks_random_replay(alice, fake, direct_browser):
    before = fake.uris(TARGET)
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    posted = []
    shuffle_url = APP + api_path(f"/shuffle/{TARGET}")
    lost = _lose_reply(posted)
    page.route(shuffle_url, lost)
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        _open_review(page, card.get_by_role("button", name=re.compile("^Shuffle now"))).click()
        expect(_panel(page).get_by_role("button", name="Retry this exact request", exact=True)).to_be_visible()
        assert alice.wait_operation(lost.location, lost.operation_id)["state"] == "succeeded"
        assert fake.uris(TARGET) != before
        _close_result(page)
        shuffle = card.get_by_role("button", name=re.compile("^Shuffle now"))
        expect(shuffle).to_have_attribute("aria-disabled", "true")
        shuffle.dispatch_event("click")
        assert posted == [shuffle_url]
        assert visit.problems == []
    finally:
        visit.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (360, 800)])
def test_workshop_privacy_preserves_draft_and_snapshot_requires_resolution(
    alice,
    fake,
    direct_browser,
    tmp_path,
    width,
    height,
):
    from tests.e2e.test_playlist_workspace import _uris

    original = fake.uris(TARGET)
    desired = list(reversed(original))
    snapshot_id = _snapshot(alice, desired)
    was_public = fake.playlist(TARGET)["public"]
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": width, "height": height})
    posts = []
    page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
    restore_path = api_path(f"/snapshots/{snapshot_id}/restore")
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
        _remove_row(page)
        draft = _uris(page)
        _open_tool(page, "details")
        run = _open_review(page, _panel(page).get_by_role("button", name=re.compile("^Review making playlist ")))
        _run_accepted(page, run, api_path(f"/playlist/{TARGET}/toggle-visibility"), alice)
        assert fake.playlist(TARGET)["public"] is not was_public
        assert fake.uris(TARGET) == original and _uris(page) == draft
        _close_tool(page)
        _open_tool(page, "history")
        _panel(page).locator(f'[data-snapshot-id="{snapshot_id}"]').get_by_role(
            "button", name="Restore now", exact=True
        ).click()
        dialog = page.get_by_role("dialog", name="Keep your local draft?")
        expect(dialog.get_by_role("button", name="Review & save", exact=True)).to_be_visible()
        page.screenshot(path=tmp_path / f"restore-draft-conflict-{width}.png", full_page=True)
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        assert APP + restore_path not in posts and _uris(page) == draft
        _panel(page).locator(f'[data-snapshot-id="{snapshot_id}"]').get_by_role(
            "button", name="Restore now", exact=True
        ).click()
        dialog.get_by_role("button", name="Discard", exact=True).click()
        expect(_panel(page).get_by_role("heading", name="Review snapshot restore", exact=True)).to_be_visible()
        assert _uris(page) == original and APP + restore_path not in posts
        run = _panel(page).get_by_role("button", name="Restore snapshot now", exact=True)
        _run_accepted(page, run, restore_path, alice)
        assert fake.uris(TARGET) == desired and _uris(page) == desired
        assert posts.count(APP + restore_path) == 1
        assert visit.problems == []
    finally:
        visit.close()


def test_snapshot_restore_is_reviewed_against_fresh_spotify_and_refuses_a_later_change(alice, fake, direct_browser):
    original = fake.uris(TARGET)
    desired = list(reversed(original))
    snapshot_id = _snapshot(alice, desired)
    restore_path = api_path(f"/snapshots/{snapshot_id}/restore")
    other = App("alice", fake.display_name("alice"))
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
        other.write(f"/workshop/{TARGET}/commit", json={"baseline_track_uris": original, "track_uris": original[1:]})
        _open_tool(page, "history")
        restore = (
            _panel(page)
            .locator(f'[data-snapshot-id="{snapshot_id}"]')
            .get_by_role("button", name="Restore now", exact=True)
        )
        run = _open_review(page, restore)
        expect(_panel(page)).to_contain_text("Review snapshot restore")
        other.write(
            f"/workshop/{TARGET}/commit", json={"baseline_track_uris": original[1:], "track_uris": original[2:]}
        )
        failed = _run_accepted(page, run, restore_path, alice, state="failed")
        assert failed["result"]["operation"]["error_code"] == "resource_changed"
        assert fake.uris(TARGET) == original[2:]
        _close_tool(page)
        _open_tool(page, "history")
        restore = _panel(page).locator(f'[data-snapshot-id="{snapshot_id}"]').get_by_role(
            "button", name="Restore now", exact=True
        )
        expect(restore).to_have_attribute("aria-disabled", "true")
        expect(_panel(page)).to_contain_text("Reconcile the retained operation")
        # The failed receipt remains authoritative in this document. A fresh visit reads
        # the externally changed order before the user reviews a new restoration.
        page.once("dialog", lambda dialog: dialog.accept())
        page.reload(wait_until="networkidle")
        from tests.e2e.test_playlist_workspace import _uris

        assert _uris(page) == original[2:]
        _open_tool(page, "history")
        run = _open_review(
            page,
            _panel(page)
            .locator(f'[data-snapshot-id="{snapshot_id}"]')
            .get_by_role("button", name="Restore now", exact=True),
        )
        _run_accepted(page, run, restore_path, alice)
        assert fake.uris(TARGET) == desired
        assert visit.problems == []
    finally:
        visit.close()


@pytest.mark.parametrize("kind", ["shuffle", "privacy"])
def test_first_write_rate_limit_defers_one_browser_operation_until_provider_hint(alice, fake, direct_browser, kind):
    before = fake.playlist(TARGET)
    operation = "PUT /playlists/{playlist_id}/items" if kind == "shuffle" else "PUT /playlists/{playlist_id}"
    fake.arm(operation=operation, kind="rate_limit", retry_after=2, playlist_id=TARGET)
    path = api_path(f"/shuffle/{TARGET}" if kind == "shuffle" else f"/playlist/{TARGET}/toggle-visibility")
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    replies = []
    page.on("response", lambda r: replies.append(r.status) if r.url == APP + path else None)
    try:
        visit.log_in()
        row = _choose_shuffle(page, TARGET)
        trigger = (
            row.get_by_role("button", name=re.compile("^Shuffle now"))
            if kind == "shuffle"
            else row.get_by_role("button", name=re.compile("^Make (private|public) now"))
        )
        with page.expect_response(APP + path) as reply:
            _open_review(page, trigger).click()
        accepted = reply.value.json()
        deadline = time.monotonic() + 10
        while fake.faults()[0]["fired"] == 0 and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert fake.faults()[0]["fired"] == 1 and replies == [202]
        assert fake.playlist(TARGET) == before
        active = alice.get(reply.value.headers["location"]).json()["operation"]
        assert active["id"] == accepted["operation_id"] and active["state"] in {"queued", "running"}
        assert alice.wait_operation(reply.value.headers["location"], accepted["operation_id"])["state"] == "succeeded"
        _settled(page)
        assert replies == [202] and [x["fired"] for x in fake.faults()] == [1]
        if kind == "privacy":
            assert fake.playlist(TARGET) == {**before, "public": not before["public"]}
        else:
            assert Counter(fake.uris(TARGET)) == Counter(before["uris"]) and fake.uris(TARGET) != before["uris"]
        assert visit.problems == []
    finally:
        visit.close()
