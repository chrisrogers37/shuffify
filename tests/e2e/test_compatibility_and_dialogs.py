"""Real-browser compatibility, retained write receipts and accessible confirmations."""

import json
import re
import time

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, pid
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.e2e.test_live_operation_outcomes import _collection, _create_schedule
from tests.e2e.test_pages import Visit
from tests.paths import api_path
from tests.routine_flow import list_schedules

TARGET = pid("alice-morning-mix")
ARCHIVE = pid("alice-morning-archive")
LONG_HAUL = pid("alice-long-haul")
SLOW = 20
SIZES = [(1440, 900), (768, 1024), (375, 812)]
WORKSHOP = f"{APP}/workshop/{TARGET}"


class Session:
    """One logged-in browser page at one viewport, recording its native dialogs, writes and navigations."""

    def __init__(self, browser, width, height):
        self.visit = Visit(browser, "desktop")
        # Names the width in anything the visit reports.
        self.visit.viewport = str(width)
        self.page = self.visit.page
        self.page.set_viewport_size({"width": width, "height": height})
        self.dialogs = []
        self.sent = []
        self.navigations = []
        self.page.on(
            "dialog",
            lambda dialog: (self.dialogs.append(dialog.type), dialog.dismiss()),
        )
        self.page.on("request", self._record)
        self.page.on("framenavigated", self._navigated)

    def _record(self, request):
        if request.method != "GET":
            self.sent.append((request.method, request.url.removeprefix(APP), request.post_data))

    def _navigated(self, frame):
        if frame == self.page.main_frame:
            self.navigations.append(frame.url)

    def writes(self, method, path):
        """The bodies of every request of this method to this path so far."""
        return [body for verb, where, body in self.sent if verb == method and where == path]

    def assert_clean(self):
        assert self.dialogs == [], f"native dialogs opened: {self.dialogs}"
        assert self.visit.problems == []


@pytest.fixture
def open_session(direct_browser):
    opened = []

    def open_(width, height):
        session = Session(direct_browser, width, height)
        opened.append(session)
        session.visit.log_in()
        return session

    yield open_
    for session in opened:
        session.visit.close()


def _sections(alice):
    """Where the server places each Library row now: playlist ID -> favorites, regular or hidden.

    The dict is in page order: Favorites, then All playlists, then Hidden."""
    context = alice.ok("get", "/api/library?refresh=1")
    return {
        row["id"]: "regular" if section == "playlists" else section
        for section in ("favorites", "playlists", "hidden")
        for row in context["sections"][section]
    }


def _panel(page):
    return page.locator("section.sh-task-panel:visible").last


def _close_all_panels(page):
    while page.locator("section.sh-task-panel:visible").count():
        _panel(page).get_by_role("button", name="Close", exact=True).click()


def _open_tool(page, tool):
    names = {
        "inbox": "Inbox",
        "archive": "Archive",
        "routines": "Routines",
        "locks": "Track locks",
        "details": "Playlist details",
    }
    _close_all_panels(page)
    page.get_by_role("button", name="Tools", exact=True).click()
    _panel(page).get_by_role("button", name=names.get(tool, tool), exact=True).click()
    expect(_panel(page).get_by_role("heading", name=names.get(tool, tool), exact=True)).to_be_visible()
    return _panel(page)


def _row_actions(page, uri=None):
    _close_all_panels(page)
    rows = page.locator(".sh-workshop-track")
    row = (
        rows.filter(has=page.locator(f'a[href="https://open.spotify.com/track/{uri.split(":")[-1]}"]')).first
        if uri
        else rows.first
    )
    action = row.locator("[data-row-actions]")
    if action.get_attribute("aria-expanded") != "true":
        action.click()
    return row.get_by_role("region")


def _remove_row(page, index=0):
    _close_all_panels(page)
    row = page.locator(".sh-workshop-track").nth(index)
    row.locator("[data-row-actions]").click()
    row.get_by_role("button", name="Remove from draft", exact=True).click()


def _described(locator):
    return locator.evaluate(r"""el => (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean)
        .map(id => document.getElementById(id)?.textContent.trim() || '').join(' ').trim()""")


def _no_sideways(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def _running(alice, operation_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        operation = alice.ok("get", f"/operations/{operation_id}")["operation"]
        if operation["state"] == "running":
            return operation
        assert operation["state"] == "queued", operation
        time.sleep(0.025)
    raise AssertionError(f"Operation {operation_id} did not start its held write")


def _start_run(page, alice, trigger, path):
    trigger.click()
    expect(_panel(page).get_by_role("button", name="Run reviewed action", exact=True)).to_be_visible()
    with page.expect_response(
        lambda response: response.request.method == "POST" and response.url == APP + path
    ) as response:
        _panel(page).get_by_role("button", name="Run reviewed action", exact=True).click()
    assert response.value.status == 202, response.value.text()
    body = response.value.json()
    operation_id = body["operation_id"]
    assert body["operation"]["id"] == operation_id
    _running(alice, operation_id)
    _close_all_panels(page)
    return operation_id


def _locks(alice):
    return [lock["track_uri"] for lock in alice.ok("get", f"/workshop/{TARGET}/locks")["locks"]]


def _routine(alice, target, schedule_id):
    return next(row for row in list_schedules(alice, target) if row["id"] == schedule_id)


def _paired(alice):
    return alice.ok("get", f"/playlist/{TARGET}/pair")["paired"]


def _pair(alice):
    alice.write(
        f"/playlist/{TARGET}/pair",
        json={
            "archive_playlist_id": ARCHIVE,
            "archive_playlist_name": "Browser-supplied name is not authoritative",
            "production_playlist_name": "Morning Mix",
        },
    )


def _save(alice, playlist, before, desired):
    return alice.write(
        f"/workshop/{playlist}/commit",
        json={
            "baseline_track_uris": before,
            "track_uris": desired,
        },
    )


def _library_row(page, playlist):
    return page.locator(f'.sh-library-row[data-playlist-id="{playlist}"]')


def _choose_shuffle(page, playlist):
    row = _library_row(page, playlist)
    row.locator(".sh-library-playlist-actions > summary").click()
    row.locator("details details > summary").click()
    row.get_by_label("Shuffle method", exact=True).select_option("BasicShuffle")
    # The options disclosure may remain open: the reviewed action freezes its values.
    return row


def _shuffle(page, playlist):
    row = _choose_shuffle(page, playlist)
    row.get_by_role("button", name=re.compile(r"^Shuffle now")).click()
    expect(_panel(page).get_by_role("button", name="Run shuffle", exact=True)).to_be_visible()
    return row, _panel(page).get_by_role("button", name="Run shuffle", exact=True)


def _lose_reply(posted):
    def handle(route):
        posted.append(route.request.url)
        handle.requests.append(
            {
                "key": route.request.headers.get("idempotency-key"),
                "body": route.request.post_data,
            }
        )
        response = route.fetch()
        assert response.status == 202, response.text()
        operation_id = response.json()["operation_id"]
        if len(posted) == 1:
            handle.operation_id = operation_id
            handle.location = response.headers["location"]
            route.fulfill(status=202, content_type="application/json", body="")
        else:
            assert operation_id == handle.operation_id
            route.fulfill(response=response)

    handle.requests = []
    return handle


@pytest.mark.parametrize("width,height", SIZES)
def test_a_lock_is_saved_while_collect_runs_but_refused_while_drip_runs(alice, fake, open_session, width, height):
    collection = _collection(alice)
    before = fake.uris(TARGET)
    uri = before[0]
    session = open_session(width, height)
    page = session.page
    page.goto(WORKSHOP, wait_until="networkidle")
    toggle_path = api_path(f"/workshop/{TARGET}/locks/toggle")
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="slow", delay=SLOW, playlist_id=collection)
    inbox = _open_tool(page, "inbox")
    collect = _start_run(
        page,
        alice,
        inbox.get_by_role("button", name="Collect for review", exact=True),
        api_path(f"/playlist/{TARGET}/raid-now"),
    )
    lock = _row_actions(page, uri).get_by_role("button", name="Lock for 30 days", exact=True)
    expect(lock).to_be_enabled()
    with page.expect_response(APP + toggle_path) as reply:
        lock.click()
    assert reply.value.status == 200, reply.value.text()
    assert _locks(alice) == [uri]
    assert len(session.writes("POST", toggle_path)) == 1
    assert alice.ok("get", f"/operations/{collect}")["operation"]["state"] == "running"
    fake.release()
    assert alice.wait_operation(api_path(f"/operations/{collect}"), collect)["state"] == "succeeded"
    assert len(fake.uris(collection)) == 5 and fake.uris(TARGET) == before

    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="slow", delay=SLOW, playlist_id=TARGET)
    inbox = _open_tool(page, "inbox")
    collect_receipt = inbox.get_by_role("region", name="Collect for review", exact=True)
    expect(collect_receipt.locator(":scope > p").first).to_have_text("complete", timeout=15000)
    drip = _start_run(
        page,
        alice,
        inbox.get_by_role("button", name="Add collected tracks now", exact=True),
        api_path(f"/playlist/{TARGET}/drip-now"),
    )
    lock = _row_actions(page, uri).get_by_role("button", name="Lock permanently", exact=True)
    expect(lock).to_be_disabled()
    assert "target tracks" in _described(lock)
    lock.dispatch_event("click")
    locks = _open_tool(page, "locks")
    unlock = locks.get_by_role("button", name="Unlock all tracks now", exact=True)
    expect(unlock).to_be_disabled()
    unlock.dispatch_event("click")
    assert len(session.writes("POST", toggle_path)) == 1
    assert session.writes("POST", api_path(f"/workshop/{TARGET}/locks/unlock-all")) == []
    assert _locks(alice) == [uri]
    assert alice.ok("get", f"/operations/{drip}")["operation"]["state"] == "running"
    fake.release()
    assert alice.wait_operation(api_path(f"/operations/{drip}"), drip)["state"] == "succeeded"
    expect(unlock).to_be_enabled()
    after = fake.uris(TARGET)
    assert len(after) > len(before) and after[-len(before) :] == before
    assert _locks(alice) == [uri]
    assert [fault["fired"] for fault in fake.faults()] == [1, 1]
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_raid_routine_pauses_and_resumes_while_collect_runs(alice, fake, open_session, width, height):
    collection = _collection(alice)
    sid = _create_schedule(alice, "raid", TARGET)["id"]
    session = open_session(width, height)
    page = session.page
    page.goto(WORKSHOP, wait_until="networkidle")
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="slow", delay=SLOW, playlist_id=collection)
    inbox = _open_tool(page, "inbox")
    operation_id = _start_run(
        page,
        alice,
        inbox.get_by_role("button", name="Collect for review", exact=True),
        api_path(f"/playlist/{TARGET}/raid-now"),
    )
    routines = _open_tool(page, "routines")
    card = routines.locator(f'[data-routine-id="{sid}"]')
    # Read-only saved configuration remains available; the conflicting run is refused at review/admission.
    for label, enabled in (("Pause", False), ("Resume", True)):
        toggle = card.get_by_role("button", name=label, exact=True)
        expect(toggle).to_be_enabled()
        with page.expect_response(APP + api_path(f"/routines/{sid}/state")) as reply:
            toggle.click()
        assert reply.value.status == 200, reply.value.text()
        assert _routine(alice, TARGET, sid)["is_enabled"] is enabled
        expect(card.get_by_role("button", name="Resume" if label == "Pause" else "Pause", exact=True)).to_be_visible()
        assert alice.ok("get", f"/operations/{operation_id}")["operation"]["state"] == "running"
    card.get_by_role("button", name="Run now", exact=True).click()
    _panel(page).get_by_role("button", name="Run reviewed action", exact=True).click()
    expect(_panel(page)).to_contain_text("is not reconciled. Open its original review")
    assert session.writes("POST", api_path(f"/schedules/{sid}/run")) == []
    _close_all_panels(page)
    fake.release()
    assert alice.wait_operation(api_path(f"/operations/{operation_id}"), operation_id)["state"] == "succeeded"
    assert len(fake.uris(collection)) == 5
    assert [fault["fired"] for fault in fake.faults()] == [1]
    states = session.writes("PUT", api_path(f"/routines/{sid}/state"))
    assert len(states) == 2 and [json.loads(body)["desired_enabled"] for body in states] == [False, True]
    assert session.writes("POST", api_path(f"/schedules/{sid}/run")) == []
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_archive_unlink_waits_for_staged_tracks_and_then_asks_before_it_unlinks(
    alice, fake, open_session, tmp_path, width, height
):
    _pair(alice)
    archived = fake.uris(LONG_HAUL)[:3]
    _save(alice, ARCHIVE, [], archived)
    original = fake.uris(TARGET)
    session = open_session(width, height)
    page = session.page
    page.goto(WORKSHOP, wait_until="networkidle")
    archive = _open_tool(page, "archive")
    expect(archive.locator("[data-archive-row]")).to_have_count(len(archived))
    archive.get_by_role("button", name="Add to draft", exact=True).first.click()
    expect(archive).to_contain_text("1 archive rows staged for restoration.")
    archive.get_by_role("button", name="Archive setup", exact=True).click()
    unlink = _panel(page).get_by_role("button", name="Unlink archive playlist", exact=True)
    expect(unlink).to_be_disabled()
    unlink.dispatch_event("click")
    expect(page.get_by_role("dialog")).to_have_count(0)
    pair_path = api_path(f"/playlist/{TARGET}/pair")
    assert session.writes("DELETE", pair_path) == [] and _paired(alice) is True
    _close_all_panels(page)
    page.get_by_role("button", name="Discard draft changes", exact=True).click()
    page.get_by_role("dialog", name="Discard local draft changes?", exact=True).get_by_role(
        "button", name="Discard draft", exact=True
    ).click()
    expect(page.locator(".sh-workshop-draft-footer")).to_contain_text("Confirmed baseline")
    assert fake.uris(TARGET) == original and fake.uris(ARCHIVE) == archived
    archive = _open_tool(page, "archive")
    expect(archive.locator("[data-archive-row]")).to_have_count(len(archived))
    archive.get_by_role("button", name="Archive setup", exact=True).click()
    unlink = _panel(page).get_by_role("button", name="Unlink archive playlist", exact=True)
    expect(unlink).to_be_enabled()
    unlink.click()
    dialog = page.get_by_role("dialog", name="Unlink the archive playlist?", exact=True)
    expect(dialog).to_contain_text(fake.playlist(ARCHIVE)["name"])
    _no_sideways(page)
    for button in dialog.get_by_role("button").all():
        box = button.bounding_box()
        assert box and 0 <= box["x"] and box["x"] + box["width"] <= width and box["y"] + box["height"] <= height
    page.screenshot(path=tmp_path / f"archive-unlink-confirm-{width}.png", full_page=True)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(unlink).to_be_focused()
    assert session.writes("DELETE", pair_path) == [] and _paired(alice) is True
    unlink.click()
    with page.expect_response(APP + pair_path) as reply:
        page.get_by_role("dialog").get_by_role("button", name="Unlink archive", exact=True).click()
    assert reply.value.status == 200, reply.value.text()
    expect(_panel(page)).to_contain_text("No archive linked.")
    assert _paired(alice) is False and len(session.writes("DELETE", pair_path)) == 1
    assert fake.uris(ARCHIVE) == archived and fake.uris(TARGET) == original
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_dismiss_all_asks_first_and_dismisses_only_the_unstaged_rows(alice, fake, open_session, width, height):
    collection = _collection(alice)
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
    assert len(pending) == 5 and len(fake.uris(collection)) == 5
    session = open_session(width, height)
    page = session.page
    page.goto(WORKSHOP, wait_until="networkidle")
    inbox = _open_tool(page, "inbox")
    rows = inbox.locator("[data-pending-id]")
    expect(rows).to_have_count(5)
    staged_id = int(rows.first.get_attribute("data-pending-id"))
    staged = next(track for track in pending if track["id"] == staged_id)
    rows.first.get_by_role("button", name="Add to draft", exact=True).click()
    expect(rows).to_have_count(4)
    others = sorted(track["id"] for track in pending if track["id"] != staged_id)
    dismiss = inbox.get_by_role("button", name="Dismiss all now", exact=True)
    dismiss.click()
    dialog = page.get_by_role("dialog", name="Dismiss all pending tracks?", exact=True)
    expect(dialog).to_contain_text("4 currently shown pending tracks")
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(dismiss).to_be_focused()
    dismiss_path = api_path(f"/playlist/{TARGET}/pending-raids/dismiss")
    assert session.writes("POST", dismiss_path) == []
    assert len(alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]) == 5
    dismiss.click()
    with page.expect_response(APP + dismiss_path) as reply:
        page.get_by_role("dialog").get_by_role("button", name="Dismiss 4 pending tracks", exact=True).click()
    assert reply.value.status == 202, reply.value.text()
    operation_id = reply.value.json()["operation_id"]
    assert alice.wait_operation(api_path(f"/operations/{operation_id}"), operation_id)["state"] == "succeeded"
    expect(rows).to_have_count(0)
    expect(inbox).to_contain_text("1 pending IDs staged for the next reviewed save.")
    sent = session.writes("POST", dismiss_path)
    assert len(sent) == 1 and sorted(json.loads(sent[0])["track_ids"]) == others
    assert session.writes("POST", api_path(f"/playlist/{TARGET}/pending-raids/dismiss-all")) == []
    assert [track["id"] for track in alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]] == [staged_id]
    assert fake.uris(collection) == [staged["track_uri"]]
    _close_all_panels(page)
    expect(page.locator(".sh-workshop-draft-footer")).to_contain_text("Local changes waiting for review")
    assert (
        page.locator(".sh-workshop-track")
        .filter(has=page.locator(f'a[href="https://open.spotify.com/track/{staged["track_uri"].split(":")[-1]}"]'))
        .count()
        > 0
    )
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_library_favorite_and_hide_update_in_place_beside_a_lost_reply_result(
    alice, fake, open_session, tmp_path, width, height
):
    session = open_session(width, height)
    page = session.page
    posted = []
    shuffle_path = api_path(f"/shuffle/{TARGET}")
    lost = _lose_reply(posted)
    page.route(APP + shuffle_path, lost)
    row, run = _shuffle(page, TARGET)
    run.click()
    expect(_panel(page).get_by_role("button", name="Retry this exact request", exact=True)).to_be_visible()
    assert alice.wait_operation(lost.location, lost.operation_id)["state"] == "succeeded"
    _close_all_panels(page)
    page.evaluate("window.__sameDocument = true")
    navigations = list(session.navigations)
    assert _sections(alice)[TARGET] == _sections(alice)[LONG_HAUL] == "regular"
    page.get_by_role("button", name="Organize", exact=True).click()
    row.get_by_role("button", name=re.compile(r"^Add favorite")).click()
    expect(
        page.locator("section[aria-labelledby='library-favorites-heading']").locator(f'[data-playlist-id="{TARGET}"]')
    ).to_have_count(1)
    expect(row.get_by_role("button", name=re.compile(r"^Remove favorite"))).to_be_visible()
    row.locator(".sh-library-playlist-actions > summary").click()
    expect(row.get_by_role("button", name=re.compile(r"^Shuffle now"))).to_be_disabled()
    other = _library_row(page, LONG_HAUL)
    other.get_by_role("button", name=re.compile(r"^Hide playlist")).click()
    expect(page.locator("#library-hidden-content").locator(f'[data-playlist-id="{LONG_HAUL}"]')).to_have_count(1)
    assert _sections(alice)[TARGET] == "favorites" and _sections(alice)[LONG_HAUL] == "hidden"
    _no_sideways(page)
    page.screenshot(path=tmp_path / f"library-in-place-{width}.png", full_page=True)
    assert page.url == f"{APP}/library" and session.navigations == navigations
    assert page.evaluate("window.__sameDocument") is True
    reopen = page.get_by_role("button", name="View action results", exact=True)
    reopen.click()
    expect(_panel(page).get_by_role("button", name="Retry this exact request", exact=True)).to_be_visible()
    _close_all_panels(page)
    expect(reopen).to_be_focused()
    assert posted == [APP + shuffle_path]
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_routines_state_stays_in_place_and_edit_guards_a_lost_run_reply(
    alice, fake, open_session, tmp_path, width, height
):
    first = _create_schedule(alice)["id"]
    second = _create_schedule(alice, "shuffle", TARGET)["id"]
    session = open_session(width, height)
    page = session.page
    posted = []
    run_path = api_path(f"/schedules/{first}/run")
    lost = _lose_reply(posted)
    page.route(APP + run_path, lost)
    page.goto(f"{APP}/routines", wait_until="networkidle")
    first_card, other = page.locator(f"#routine-{first}"), page.locator(f"#routine-{second}")
    first_card.get_by_role("button", name="Run now", exact=True).click()
    _panel(page).get_by_role("button", name="Run reviewed routine", exact=True).click()
    expect(_panel(page).get_by_role("button", name="Check the same run request", exact=True)).to_be_visible()
    assert alice.wait_operation(lost.location, lost.operation_id)["state"] == "succeeded"
    _close_all_panels(page)
    expect(first_card.get_by_role("button", name="Edit this routine", exact=True)).to_be_disabled()
    expect(first_card.get_by_role("button", name="Delete routine", exact=True)).to_be_disabled()
    page.evaluate("window.__sameDocument = true")
    navigations = list(session.navigations)
    for label, enabled in (("Pause", False), ("Resume", True)):
        other.get_by_role("button", name=label, exact=True).click()
        expect(other.locator(".sh-badge")).to_have_text("Paused" if not enabled else "Active")
        assert _routine(alice, TARGET, second)["is_enabled"] is enabled
    states = session.writes("PUT", api_path(f"/routines/{second}/state"))
    assert len(states) == 2 and [json.loads(body)["desired_enabled"] for body in states] == [False, True]
    assert page.url == f"{APP}/routines" and session.navigations == navigations
    _no_sideways(page)
    page.screenshot(path=tmp_path / f"routines-in-place-{width}.png", full_page=True)
    expected_edit = f"/playlist/{TARGET}/routines/new?schedule_id={second}&origin=routines"
    edit = other.get_by_role("link", name="Edit this routine", exact=True)
    expect(edit).to_have_attribute("href", expected_edit)
    edit.click()
    dialog = page.get_by_role("dialog", name="Leave this page?", exact=True)
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Stay", exact=True).click()
    expect(edit).to_be_focused()
    assert page.url == f"{APP}/routines" and session.navigations == navigations
    assert page.evaluate("window.__sameDocument") is True
    expect(first_card.get_by_role("button", name="Check run request", exact=True)).to_be_visible()
    assert posted == [APP + run_path]
    edit.click()
    page.get_by_role("dialog").get_by_role("button", name="Leave page", exact=True).click()
    expect(page.get_by_role("heading", name=f"Edit routine #{second}", exact=True)).to_be_visible()
    assert page.evaluate("window.__sameDocument") is True
    page.get_by_role("link", name="Back to routines", exact=True).click()
    first_card.get_by_role("button", name="Check run request", exact=True).click()
    expect(_panel(page).get_by_role("button", name="Check the same run request", exact=True)).to_be_visible()
    _panel(page).get_by_role("button", name="Check the same run request", exact=True).click()
    expect(_panel(page).get_by_role("heading", name=re.compile(rf"^Operation #{lost.operation_id} ·"))).to_be_visible()
    assert len(lost.requests) == 2 and lost.requests[0] == lost.requests[1]
    assert lost.requests[0]["key"]
    _close_all_panels(page)
    assert posted == [APP + run_path, APP + run_path]
    assert session.writes("PUT", api_path(f"/schedules/{second}")) == []
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_routine_delete_asks_first_and_moves_focus_to_what_remains(alice, fake, open_session, width, height):
    first = _create_schedule(alice)["id"]
    second = _create_schedule(alice, "shuffle", TARGET)["id"]
    session = open_session(width, height)
    page = session.page
    page.goto(f"{APP}/routines", wait_until="networkidle")
    expect(page.get_by_text("2 of 2 routines shown", exact=True)).to_be_visible()
    for sid, target, remaining in ((first, LONG_HAUL, 1), (second, TARGET, 0)):
        card = page.locator(f"#routine-{sid}")
        delete = card.get_by_role("button", name="Delete routine", exact=True)
        delete.click()
        dialog = page.get_by_role("dialog", name=f"Delete routine #{sid}?", exact=True)
        _no_sideways(page)
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        expect(delete).to_be_focused()
        assert session.writes("DELETE", api_path(f"/schedules/{sid}")) == []
        assert [row["id"] for row in list_schedules(alice, target)] == [sid]
        delete.click()
        with page.expect_response(APP + api_path(f"/schedules/{sid}")) as reply:
            page.get_by_role("dialog").get_by_role("button", name="Delete routine", exact=True).click()
        assert reply.value.status == 200, reply.value.text()
        expect(card).to_have_count(0)
        expect(page.get_by_text(f"{remaining} of {remaining} routines shown", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="Routines", exact=True)).to_be_focused()
        assert list_schedules(alice, target) == []
        assert len(session.writes("DELETE", api_path(f"/schedules/{sid}"))) == 1
    expect(page.get_by_role("heading", name="No routines yet", exact=True)).to_be_visible()
    assert page.url == f"{APP}/routines"
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_the_announcer_reads_a_verified_library_shuffle_once(alice, fake, open_session, width, height):
    before = fake.uris(TARGET)
    session = open_session(width, height)
    page = session.page
    announcer = page.locator('[aria-live="polite"]')
    expect(announcer).to_have_count(1)
    assert announcer.get_attribute("aria-atomic") == "true"
    assert announcer.locator("xpath=ancestor::main | ancestor::section[contains(@class, 'sh-task-panel')]").count() == 0
    page.evaluate("""() => {
        window.__announced = [];
        const region = document.querySelector('[aria-live="polite"]');
        new MutationObserver(() => { if (region.textContent) window.__announced.push(region.textContent); })
          .observe(region, {childList: true, characterData: true, subtree: true});
    }""")
    _, run = _shuffle(page, TARGET)
    with page.expect_response(APP + api_path(f"/shuffle/{TARGET}")) as reply:
        run.click()
    assert reply.value.status == 202, reply.value.text()
    operation_id = reply.value.json()["operation_id"]
    assert alice.wait_operation(api_path(f"/operations/{operation_id}"), operation_id)["state"] == "succeeded"
    terminal = f"Operation {operation_id}: succeeded."
    expect(announcer).to_have_text(terminal)
    assert fake.uris(TARGET) != before
    assert page.evaluate("text => window.__announced.filter(value => value === text).length", terminal) == 1
    _close_all_panels(page)
    page.get_by_role("button", name="View action results", exact=True).click()
    expect(_panel(page).get_by_role("heading", name=f"Operation #{operation_id}", exact=True)).to_be_visible()
    _close_all_panels(page)
    expect(announcer).to_have_text(terminal)
    assert page.evaluate("text => window.__announced.filter(value => value === text).length", terminal) == 1
    assert page.locator(".sh-toast").count() == 0
    session.assert_clean()
