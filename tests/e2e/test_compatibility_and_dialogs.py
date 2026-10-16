"""Compatibility and dialogs in a real browser: Workshop blocks only incompatible controls,
destructive choices ask through the shared confirmation, and the Library and Routines pages
update in place.

Each test drives the app against the isolated fake Spotify at a desktop
and a phone width, checks the outcome in the app's own API and in the fake's playlists, and fails on a native dialog,
a console error or any failed request.

A live action is held in flight with a slow fault on its Spotify write: the fake holds the write, so the page stays
Busy while the test acts on the controls around it. The test releases the hold (``fake.release()``) as soon as it has
checked the page, rather than waiting the delay out.
"""

import json
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, pid
from tests.e2e.test_direct_live_actions import (
    _choose_shuffle,
    _close_result,
    _close_tool,
    _lose_reply,
    _open_inbox,
    _open_review,
    _open_row_actions,
    _open_tool,
    _settled,
)
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.e2e.test_live_operation_outcomes import _collection, _create_schedule
from tests.e2e.test_pages import Visit
from tests.e2e.test_workshop_source_recovery import _pair, _save
from tests.paths import api_path
from tests.routine_flow import list_schedules

TARGET = pid("alice-morning-mix")
ARCHIVE = pid("alice-morning-archive")
LONG_HAUL = pid("alice-long-haul")
# The most seconds the fake holds a live action's Spotify write. Each test releases the hold as soon as it has
# checked the page while the action is in flight, so this only bounds a test that fails before it gets there.
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
            self.sent.append(
                (request.method, request.url.removeprefix(APP), request.post_data)
            )

    def _navigated(self, frame):
        if frame == self.page.main_frame:
            self.navigations.append(frame.url)

    def writes(self, method, path):
        """The bodies of every request of this method to this path so far."""
        return [
            body for verb, where, body in self.sent if verb == method and where == path
        ]

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


def _described(locator):
    """The text a control's aria-describedby names: why it is unavailable. Empty when it names nothing."""
    return locator.evaluate(
        r"""el => (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean)
            .map(id => { const node = document.getElementById(id); return node ? node.textContent.trim() : ''; })
            .join(' ').trim()"""
    )


def _start_run(page, trigger, label):
    """Review and run a live action, then close its results so the page behind it is reachable at every width.

    Returns while the action is still in flight: the caller holds its Spotify write with a slow fault."""
    _open_review(page, trigger, label).click()
    page.wait_for_function("() => PlaylistOperations.isBusy()")
    _close_result(page)


def _locks(alice):
    return [
        lock["track_uri"]
        for lock in alice.ok("get", f"/workshop/{TARGET}/locks")["locks"]
    ]


def _routine(alice, target, schedule_id):
    return next(
        s
        for s in list_schedules(alice, target)
        if s["id"] == schedule_id
    )


def _paired(alice):
    return alice.ok("get", f"/playlist/{TARGET}/pair")["paired"]


def _sections(alice):
    """Where the server places each Library row now: playlist ID -> favorites, regular or hidden.

    The dict is in page order: Favorites, then All playlists, then Hidden."""
    html = alice.get("/").text
    sections = {}
    for tile in re.findall(r"<li\b[^>]*\bcard-tile\b[^>]*>", html):
        playlist = re.search(r'data-playlist-id="([^"]+)"', tile)
        section = re.search(r'data-section="([^"]+)"', tile)
        sections[playlist.group(1)] = section.group(1)
    return sections


@pytest.mark.parametrize("width,height", SIZES)
def test_a_lock_is_saved_while_collect_runs_but_refused_while_drip_runs(
    alice, fake, open_session, width, height
):
    """Locks are saved configuration: allowed beside Collect for review, refused while Drip runs (and say why)."""
    collection = _collection(alice)
    before = fake.uris(TARGET)
    s = open_session(width, height)
    page = s.page
    toggle_path = api_path(f"/workshop/{TARGET}/locks/toggle")
    page.goto(WORKSHOP, wait_until="networkidle")
    first = page.locator("#track-list .track-item").first
    # A row has no lock button of its own: Row actions has one Lock control, shown in the row that opened it.
    lock, uri = page.locator("#track-actions-lock"), first.get_attribute("data-uri")
    assert lock.get_attribute("data-workshop-compat") == "locks"

    # Collect writes only the collection and the review inbox, so a lock may still be saved beside it.
    fake.arm(
        operation="POST /playlists/{playlist_id}/items",
        kind="slow",
        delay=SLOW,
        playlist_id=collection,
    )
    _open_inbox(page)
    page.locator("#raid-now-btn").wait_for(state="visible")
    _start_run(page, page.locator("#raid-now-btn"), "Collect for review")
    assert page.evaluate("workshopState.liveRun.kind") == "raid"
    _open_row_actions(page, first)
    # Only the control's own note describes it: no reason for being unavailable is attached.
    assert (
        lock.is_enabled()
        and lock.get_attribute("aria-describedby") == "track-actions-lock-note"
    )
    lock.click()
    page.wait_for_function("uri => Boolean(workshopState.locks[uri])", arg=uri)
    assert _locks(alice) == [uri]
    assert len(s.writes("POST", toggle_path)) == 1
    assert page.evaluate("PlaylistOperations.isBusy()") is True, (
        "the Collect finished before the lock was saved"
    )
    fake.release()
    _settled(page, 1)
    assert page.evaluate("PlaylistOperations.getRecords()[0].complete") is True
    assert len(fake.uris(collection)) == 5 and fake.uris(TARGET) == before

    # Adding collected tracks reads locks while it moves tracks, so a lock waits for it and says why.
    fake.arm(
        operation="POST /playlists/{playlist_id}/items",
        kind="slow",
        delay=SLOW,
        playlist_id=TARGET,
    )
    _open_inbox(page)
    page.locator("#drip-now-btn").wait_for(state="visible")
    _start_run(page, page.locator("#drip-now-btn"), "Add collected tracks now")
    assert page.evaluate("workshopState.liveRun.kind") == "drip"
    _open_row_actions(page, first)
    assert lock.is_disabled()
    assert "adding collected tracks" in _described(lock)
    assert page.locator("#track-actions-lock:not([disabled])").count() == 0
    # A click that reaches the control, or its handler, still sends nothing and changes no lock.
    lock.dispatch_event("click")
    page.evaluate("toggleTrackLock(workshopState.occurrences[0].key)")
    # Unlock all is in Playlist details, and waits for the run the same way.
    _open_tool(page, "details")
    unlock_all = page.locator("#unlock-all-btn")
    assert (
        unlock_all.is_visible() and unlock_all.is_disabled() and _described(unlock_all)
    )
    unlock_all.dispatch_event("click")
    assert (
        len(s.writes("POST", toggle_path)) == 1
        and s.writes("POST", api_path(f"/workshop/{TARGET}/locks/unlock-all")) == []
    )
    assert _locks(alice) == [uri] and page.evaluate(
        "Object.keys(workshopState.locks)"
    ) == [uri]
    assert page.evaluate("PlaylistOperations.isBusy()") is True
    fake.release()
    _settled(page, 2)
    assert page.evaluate("PlaylistOperations.getRecords()[1].complete") is True
    after = fake.uris(TARGET)
    assert len(after) > len(before) and after[-len(before) :] == before
    # The block lifts with the run, and the lock saved earlier is still there.
    assert page.locator("#track-actions-lock:not([disabled])").count() == 1
    assert _locks(alice) == [uri]
    assert [fault["fired"] for fault in fake.faults()] == [1, 1]
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_raid_routine_pauses_and_resumes_while_collect_runs(
    alice, fake, open_session, width, height
):
    """Pause and Resume are never blocked by a running live action; the routine's own Run waits."""
    collection = _collection(alice)
    sid = _create_schedule(alice, "raid", TARGET)["id"]
    s = open_session(width, height)
    page = s.page
    try:
        page.goto(WORKSHOP, wait_until="networkidle")
        # The compact Routines tool shows this exact saved routine, including its own Pause.
        _open_tool(page, "routines")
        card = page.locator(f'#workshop-routine-management .schedule-card[data-schedule-id="{sid}"]')
        toggle = card.locator(".routine-toggle")
        toggle.wait_for(state="visible")
        assert (
            toggle.inner_text().strip() == "Pause"
            and toggle.get_attribute("data-workshop-compat") == "pause"
        )
        assert _routine(alice, TARGET, sid)["is_enabled"] is True

        fake.arm(
            operation="POST /playlists/{playlist_id}/items",
            kind="slow",
            delay=SLOW,
            playlist_id=collection,
        )
        _close_tool(page, "routines")
        _open_inbox(page)
        _start_run(page, page.locator("#raid-now-btn"), "Collect for review")
        assert page.evaluate("workshopState.liveRun.kind") == "raid"
        # The routine's own Run waits for the Collect; Pause and Resume do not.
        if page.locator("#workshop-inbox-panel").is_visible():
            _close_tool(page, "inbox")
        _open_tool(page, "routines")
        card.locator(".routine-run").wait_for(state="visible")
        assert card.locator(".routine-run").is_disabled()
        for label, enabled in (("Resume", False), ("Pause", True)):
            assert (
                toggle.is_enabled() and toggle.get_attribute("aria-describedby") == f"routine-title-{sid}"
            )
            toggle.click()
            page.wait_for_function(
                "args => document.querySelector(`.schedule-card[data-schedule-id='${args.id}'] "
                ".routine-toggle`).textContent.trim() === args.label",
                arg={"id": sid, "label": label},
            )
            assert _routine(alice, TARGET, sid)["is_enabled"] is enabled
            assert page.evaluate("PlaylistOperations.isBusy()") is True, (
                "the Collect finished too soon"
            )
        fake.release()
        _settled(page, 1)
        assert page.evaluate("PlaylistOperations.getRecords()[0].complete") is True
        assert len(fake.uris(collection)) == 5
        assert [fault["fired"] for fault in fake.faults()] == [1]
        assert len(s.writes("PUT", api_path(f"/routines/{sid}/state"))) == 2
        assert [json.loads(body)["desired_enabled"] for body in s.writes(
            "PUT", api_path(f"/routines/{sid}/state")
        )] == [False, True]
        s.assert_clean()
    finally:
        fake.release()
        for record in page.evaluate("PlaylistOperations.getRecords()"):
            operation_id = record.get("acceptedOperationId")
            if operation_id is not None:
                alice.wait_operation(api_path(f"/operations/{operation_id}"), operation_id)
        alice.ok("delete", f"/schedules/{sid}")


@pytest.mark.parametrize("width,height", SIZES)
def test_archive_unlink_waits_for_staged_tracks_and_then_asks_before_it_unlinks(
    alice,
    fake,
    open_session,
    tmp_path,
    width,
    height,
):
    """Unlink never clears staged archive work; once it is discarded, unlink asks through the shared confirmation."""
    _pair(alice)
    archived = fake.uris(LONG_HAUL)[:3]
    _save(alice, ARCHIVE, [], archived)
    original = fake.uris(TARGET)
    s = open_session(width, height)
    page = s.page
    unlink, confirm = page.locator("#archive-unlink-btn"), page.locator("#app-confirm")
    pair_path = api_path(f"/playlist/{TARGET}/pair")

    def open_setup():
        # Unlink is in Archive setup, which Archive opens; closing the setup returns to Archive.
        _open_tool(page, "archive")
        page.click("#workshop-archive-setup-btn")
        unlink.wait_for(state="visible")

    page.goto(WORKSHOP, wait_until="networkidle")
    _open_tool(page, "archive")
    page.wait_for_function(
        "n => workshopState.archivePair !== null && workshopState.archiveTracks.length === n",
        arg=len(archived),
    )
    open_setup()
    assert unlink.get_attribute("data-workshop-compat") == "pair-unlink"
    assert unlink.is_enabled() and _described(unlink) == ""

    # Staging an archive restoration is a draft: unlink waits for it and never clears it.
    _close_tool(page, "archive-setup")
    page.locator("#inbox-archive-list .archive-add-btn").first.click()
    page.wait_for_function("() => workshopState.stagedRestorations.length === 1")
    # The draft controls are on the playlist, which an open tool covers below 1024px.
    _close_tool(page, "archive")
    discard = page.locator("#undo-preview-btn")
    assert (
        discard.is_visible() and discard.inner_text().strip() == "Discard draft changes"
    )
    open_setup()
    assert unlink.is_disabled()
    assert _described(unlink) == "Save or discard the staged archive tracks first"
    unlink.dispatch_event("click")
    unlink.evaluate("el => deleteArchivePair(el)")
    assert confirm.is_hidden() and s.writes("DELETE", pair_path) == []
    assert (
        page.evaluate("workshopState.stagedRestorations.length") == 1
        and _paired(alice) is True
    )

    # Discarding the draft returns the staged tracks to the archive and releases the control.
    _close_tool(page, "archive-setup")
    _close_tool(page, "archive")
    page.click("#undo-preview-btn")
    page.wait_for_function("() => workshopState.stagedRestorations.length === 0")
    assert page.evaluate("workshopState.workingUris") == original
    assert page.evaluate("workshopState.archiveTracks.length") == len(archived)
    open_setup()
    assert unlink.is_enabled() and _described(unlink) == ""

    # Cancel keeps the pairing and returns focus to the control that asked.
    # The confirmation hides the panel behind it, so the archive's name is read first.
    name = page.locator("#archive-pair-name").inner_text()
    unlink.click()
    confirm.wait_for(state="visible")
    assert (
        page.locator("#app-confirm-heading").inner_text()
        == "Unlink the archive playlist?"
    )
    assert name and name in page.locator("#app-confirm-text").inner_text()
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    for button in ("#app-confirm-ok", "#app-confirm-cancel"):
        box = page.locator(button).bounding_box()
        assert (
            0 <= box["x"]
            and box["x"] + box["width"] <= width
            and box["y"] + box["height"] <= height
        )
    page.screenshot(
        path=tmp_path / f"archive-unlink-confirm-{width}.png", full_page=True
    )
    page.click("#app-confirm-cancel")
    confirm.wait_for(state="hidden")
    assert page.evaluate("document.activeElement.id") == "archive-unlink-btn"
    assert s.writes("DELETE", pair_path) == [] and _paired(alice) is True
    assert page.evaluate("workshopState.archivePair !== null")

    # Confirm unlinks; the archive playlist and its tracks stay on Spotify.
    unlink.click()
    confirm.wait_for(state="visible")
    page.click("#app-confirm-ok")
    page.wait_for_function("() => workshopState.archivePair === null")
    assert _paired(alice) is False
    assert len(s.writes("DELETE", pair_path)) == 1
    assert fake.uris(ARCHIVE) == archived and fake.uris(TARGET) == original
    assert page.locator("#archive-no-pair").is_visible()
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_dismiss_all_asks_first_and_dismisses_only_the_unstaged_rows(
    alice, fake, open_session, width, height
):
    """Dismiss all asks first, then sends only the rows shown; a staged row is part of the draft and stays pending."""
    collection = _collection(alice)
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
    assert len(pending) == 5 and len(fake.uris(collection)) == 5
    s = open_session(width, height)
    page = s.page
    confirm = page.locator("#app-confirm")
    dismiss_path = api_path(f"/playlist/{TARGET}/pending-raids/dismiss")
    page.goto(WORKSHOP, wait_until="networkidle")
    _open_tool(page, "inbox")
    page.wait_for_function("() => pendingRaidsState.tracks.length === 5")

    # Stage one inbox track for promotion: it is part of the draft, not of what Dismiss all may remove.
    rows = page.locator("#inbox-raids-list > div")
    staged_id = int(rows.first.get_attribute("data-track-id"))
    staged = next(track for track in pending if track["id"] == staged_id)
    rows.first.locator(".inbox-add-btn").click()
    page.wait_for_function(
        "() => pendingRaidsState.stagedPromotions.length === 1 && pendingRaidsState.tracks.length === 4"
    )
    others = sorted(track["id"] for track in pending if track["id"] != staged_id)
    dismiss_all = page.locator("#inbox-raids-dismiss-all")
    assert (
        dismiss_all.get_attribute("data-workshop-compat") == "dismiss"
        and dismiss_all.is_enabled()
    )

    # Cancel sends nothing.
    dismiss_all.click()
    confirm.wait_for(state="visible")
    assert (
        page.locator("#app-confirm-heading").inner_text()
        == "Dismiss all pending tracks?"
    )
    assert "4 pending tracks" in page.locator("#app-confirm-text").inner_text()
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    page.click("#app-confirm-cancel")
    confirm.wait_for(state="hidden")
    assert s.writes("POST", dismiss_path) == []
    assert len(alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]) == 5

    # Confirm sends exactly the four rows shown, through the one-row route, and never the dismiss-everything route.
    dismiss_all.click()
    confirm.wait_for(state="visible")
    page.click("#app-confirm-ok")
    page.wait_for_function("() => pendingRaidsState.tracks.length === 0")
    sent = s.writes("POST", dismiss_path)
    assert len(sent) == 1 and sorted(json.loads(sent[0])["track_ids"]) == others
    assert s.writes("POST", api_path(f"/playlist/{TARGET}/pending-raids/dismiss-all")) == []
    assert [
        track["id"]
        for track in alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
    ] == [staged_id]
    assert fake.uris(collection) == [staged["track_uri"]]
    assert page.evaluate(
        "pendingRaidsState.stagedPromotions.map(track => track.id)"
    ) == [staged_id]
    assert (
        page.evaluate(
            "uri => workshopState.workingUris.includes(uri)", staged["track_uri"]
        )
        is True
    )
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_library_favorite_and_hide_update_in_place_beside_a_lost_reply_result(
    alice,
    fake,
    open_session,
    tmp_path,
    width,
    height,
):
    """Favorite and hide save app preferences only, so tiles move without a reload and the result stays."""
    shuffle = (APP + api_path(f'/shuffle/{TARGET}'))
    s = open_session(width, height)
    page = s.page
    posted = []
    page.route(shuffle, _lose_reply(posted))
    card = _choose_shuffle(page, TARGET)
    _open_review(page, card.locator(".library-quick-shuffle"), "Shuffle now").click()
    _settled(page, 1)
    assert page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "unknown"
    assert page.evaluate("PlaylistOperations.hasAttention()") is True
    _close_result(page)
    assert posted == [shuffle]

    # Nothing here may reload the page: the result below would be lost with it.
    page.evaluate("window.__sameDocument = true")
    navigations = list(s.navigations)
    other = page.locator(f'.card-tile[data-playlist-id="{LONG_HAUL}"]')
    assert _sections(alice)[TARGET] == _sections(alice)[LONG_HAUL] == "regular"

    # Favorite and Hide are Organize controls; they save Library preferences only, never a Spotify change.
    page.locator("#organize-btn").click()
    card.locator(".fav-btn").click()
    page.wait_for_function(
        "id => Boolean(document.querySelector(`#favorites-grid .card-tile[data-playlist-id='${id}']`))",
        arg=TARGET,
    )
    assert (
        card.get_attribute("data-pinned") == "true"
        and card.get_attribute("data-section") == "favorites"
    )
    assert (
        page.locator("#favorites-section").is_visible()
        and page.locator("#favorites-count").inner_text() == "1"
    )
    # The star has no title: its words and its accessible name say what it would do next.
    favorite = card.locator(".fav-btn")
    assert favorite.text_content().strip() == "Unfavorite"
    assert favorite.get_attribute("aria-label") == "Unfavorite: Morning Mix"
    # The tile that moved is the one the unknown result is about: it keeps waiting for a check.
    assert card.locator(".library-quick-shuffle").is_disabled()

    # In Organize, Hide shows on the row itself: there is no disclosure to open first.
    other.locator(".hide-btn").click()
    page.wait_for_function(
        "id => Boolean(document.querySelector(`#hidden-grid .card-tile[data-playlist-id='${id}']`))",
        arg=LONG_HAUL,
    )
    assert (
        other.get_attribute("data-hidden") == "true"
        and other.get_attribute("data-section") == "hidden"
    )
    assert (
        page.locator("#hidden-playlists-section").is_visible()
        and page.locator("#hidden-count").inner_text() == "1"
    )
    assert page.locator("#library-reload-notice").is_hidden()
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    page.screenshot(path=tmp_path / f"library-in-place-{width}.png", full_page=True)

    # The server saved both, and the page that showed them is the one that was already open.
    assert (
        _sections(alice)[TARGET] == "favorites"
        and _sections(alice)[LONG_HAUL] == "hidden"
    )
    assert page.url == f"{APP}/" and s.navigations == navigations
    assert page.evaluate("window.__sameDocument") is True
    assert page.evaluate("PlaylistOperations.getRecords().length") == 1
    assert page.evaluate("PlaylistOperations.hasAttention()") is True
    assert page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "unknown"
    assert page.locator("#live-operation-open").is_visible()
    assert posted == [shuffle]
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_routines_state_stays_in_place_and_edit_guards_a_lost_run_reply(
    alice, fake, open_session, tmp_path, width, height
):
    """Exact state changes stay in place; a dedicated Edit obeys the unresolved-result leave guard."""
    # The first routine's Run loses its reply; the second can still be paused and resumed.
    first = _create_schedule(alice)["id"]
    second = _create_schedule(alice, "shuffle", TARGET)["id"]
    run_url = (APP + api_path(f'/schedules/{first}/run'))
    s = open_session(width, height)
    page = s.page
    posted = []
    lost = _lose_reply(posted)
    page.route(run_url, lost)
    try:
        page.goto(f"{APP}/schedules", wait_until="networkidle")
        run = page.locator(f'[data-live-operation-key="schedule:{first}"]')
        _open_review(page, run, "Run routine now").click()
        _settled(page, 1)
        assert alice.wait_operation(lost.location, lost.operation_id)["state"] == "succeeded"
        assert (
            page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "unknown"
        )
        assert page.evaluate("PlaylistOperations.hasAttention()") is True
        _close_result(page)
        assert posted == [run_url] and run.is_disabled()

        # Nothing below may reload the page: the result above would be lost with it.
        page.evaluate("window.__sameDocument = true")
        navigations = list(s.navigations)
        card = page.locator(f'.schedule-card[data-schedule-id="{second}"]')
        status, toggle = (
            card.locator(".routine-status"),
            card.locator(".routine-toggle"),
        )
        assert status.inner_text() == "Active" and toggle.inner_text().strip() == "Pause"

        for badge, label in (("Paused", "Resume"), ("Active", "Pause")):
            toggle.click()
            page.wait_for_function(
                "args => document.querySelector(`.schedule-card[data-schedule-id='${args.id}'] .routine-status`)"
                ".textContent === args.badge",
                arg={"id": second, "badge": badge},
            )
            assert toggle.inner_text().strip() == label
            assert _routine(alice, TARGET, second)["is_enabled"] is (badge == "Active")
        states = s.writes("PUT", api_path(f"/routines/{second}/state"))
        assert len(states) == 2
        assert [json.loads(body)["desired_enabled"] for body in states] == [False, True]

        # Edit navigates to the dedicated task. Cancel the Attention guard first: the
        # same document, unknown result and recoverable details must remain here.
        edit = card.locator(".routine-edit")
        expected_edit = (
            f"/playlist/{TARGET}/routines/new?schedule_id={second}&from=routines"
        )
        assert edit.get_attribute("href") == expected_edit
        edit.click()
        leave = page.locator("#live-operation-leave")
        leave.wait_for(state="visible")
        copy = page.locator("#live-operation-leave-text").inner_text()
        assert "Leaving or reloading loses these details" in copy
        assert "does not cancel or undo anything already sent to Spotify" in copy
        page.click("#live-operation-leave-stay")
        leave.wait_for(state="hidden")
        assert page.url == f"{APP}/schedules" and s.navigations == navigations
        assert page.evaluate("window.__sameDocument") is True
        assert page.evaluate("PlaylistOperations.hasAttention()") is True
        assert page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "unknown"
        assert s.writes("PUT", api_path(f"/schedules/{second}")) == []
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        page.screenshot(
            path=tmp_path / f"routines-in-place-{width}.png", full_page=True
        )

        assert page.url == f"{APP}/schedules" and s.navigations == navigations
        assert page.evaluate("window.__sameDocument") is True
        assert page.locator("#routines-reload-notice").is_hidden()
        assert page.evaluate("PlaylistOperations.getRecords().length") == 1
        assert (
            page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "unknown"
        )
        assert page.evaluate("PlaylistOperations.hasAttention()") is True
        assert posted == [run_url]

        # Closed results remain reachable near the page heading at every width.
        reopen = page.locator("#live-operation-open")
        expect(reopen).to_be_visible()
        reopen.click()
        expect(page.locator("#live-operation-panel")).to_be_visible()
        assert page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "unknown"
        _close_result(page)
        expect(reopen).to_be_focused()

        # Leaving is a separate deliberate choice; the guard remains truthful about
        # losing this page's unresolved details without claiming the Spotify action was undone.
        edit.click()
        leave.wait_for(state="visible")
        assert page.locator("#live-operation-leave-text").inner_text() == copy
        page.click("#live-operation-leave-confirm")
        page.wait_for_url(f"{APP}{expected_edit}")
        assert page.locator("#routine-task").is_visible()
        assert page.locator("#routine-heading").inner_text() == "Edit routine"
        assert posted == [run_url] and s.writes("PUT", api_path(f"/schedules/{second}")) == []
        s.assert_clean()
    finally:
        for sid in (first, second):
            alice.ok("delete", f"/schedules/{sid}")


@pytest.mark.parametrize("width,height", SIZES)
def test_routine_delete_asks_first_and_moves_focus_to_what_remains(
    alice, fake, open_session, width, height
):
    """Delete asks through the shared confirmation; confirming updates the count and moves focus on."""
    first = _create_schedule(alice)["id"]
    second = _create_schedule(alice, "shuffle", TARGET)["id"]
    s = open_session(width, height)
    page = s.page
    confirm, count = page.locator("#app-confirm"), page.locator("[data-routine-count]")
    page.goto(f"{APP}/schedules", wait_until="networkidle")
    assert count.inner_text() == "2 routines"

    def remove(sid):
        return page.locator(f'.schedule-card[data-schedule-id="{sid}"] .routine-delete')

    def card_gone(sid):
        page.wait_for_function(
            "id => !document.querySelector(`.schedule-card[data-schedule-id='${id}']`)",
            arg=sid,
        )

    def listed(target):
        return [
            r["id"]
            for r in list_schedules(alice, target)
        ]

    # Cancel keeps the routine and returns focus to the control that asked.
    remove(first).click()
    confirm.wait_for(state="visible")
    assert page.locator("#app-confirm-heading").inner_text() == "Delete this routine?"
    assert page.locator("#app-confirm-ok").inner_text() == "Delete routine"
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    page.click("#app-confirm-cancel")
    confirm.wait_for(state="hidden")
    assert remove(first).evaluate("el => document.activeElement === el") is True
    assert s.writes("DELETE", api_path(f"/schedules/{first}")) == []
    assert count.inner_text() == "2 routines" and listed(LONG_HAUL) == [
        first
    ]

    # Confirm deletes it: the count drops and focus lands on the card that remains.
    remove(first).click()
    confirm.wait_for(state="visible")
    page.click("#app-confirm-ok")
    card_gone(first)
    assert count.inner_text() == "1 routine"
    assert page.evaluate(
        "document.activeElement.closest('.schedule-card').dataset.scheduleId"
    ) == str(second)
    assert listed(LONG_HAUL) == [] and listed(TARGET) == [second]
    assert len(s.writes("DELETE", api_path(f"/schedules/{first}"))) == 1

    # Deleting the last routine leaves the empty state and puts focus on the page heading.
    remove(second).click()
    confirm.wait_for(state="visible")
    page.click("#app-confirm-ok")
    card_gone(second)
    assert count.inner_text() == "0 routines"
    assert page.locator("[data-routine-empty]").is_visible()
    assert page.evaluate("document.activeElement.id") == "routines-heading"
    assert listed(TARGET) == []
    assert page.url == f"{APP}/schedules"
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_the_announcer_reads_a_verified_library_shuffle_once(
    alice, fake, open_session, width, height
):
    """One polite announcer says a run started and how it finished, naming its action and playlist."""
    before = fake.uris(TARGET)
    s = open_session(width, height)
    page = s.page
    announcer = page.locator("#live-operation-announcer")
    # One polite region, outside every panel and the page content, so a closed results view is still heard.
    assert (
        announcer.get_attribute("role") == "status"
        and announcer.get_attribute("aria-live") == "polite"
    )
    assert page.evaluate(
        "!document.getElementById('live-operation-announcer').closest('#live-operation-panel, [data-panel-primary]')"
    )
    page.evaluate(
        """() => {
            window.__announced = [];
            const region = document.getElementById('live-operation-announcer');
            new MutationObserver(() => window.__announced.push(region.textContent))
                .observe(region, {childList: true, characterData: true, subtree: true});
        }"""
    )
    card = _choose_shuffle(page, TARGET)
    _open_review(page, card.locator(".library-quick-shuffle"), "Shuffle now").click()
    _settled(page, 1)
    assert page.evaluate("PlaylistOperations.getRecords()[0].complete") is True
    assert fake.uris(TARGET) != before
    done = "Complete: Shuffle now, Morning Mix. Morning Mix: verified; saved version history: saved."
    # The announcer writes once after the task that spoke, so the read waits for it.
    expect(announcer).to_have_text(done)
    # The review once when ready, the run once when it started and once when it finished;
    # the re-renders in between say nothing.
    assert page.evaluate("window.__announced") == [
        "Review ready: Shuffle now, Morning Mix. Nothing is sent until you run it.",
        "Running: Shuffle now, Morning Mix.",
        done,
    ]
    # Nothing else reads it again: no toast, and the card's note is visible text only.
    assert page.locator("#flash-messages .sh-toast").count() == 0
    # (Below 1024px the open results hide the Library, so the note is checked, not seen.)
    note = card.locator(".library-shuffle-feedback")
    assert note.get_attribute("hidden") is None and note.text_content().strip()
    assert note.get_attribute("role") is None
    s.assert_clean()
