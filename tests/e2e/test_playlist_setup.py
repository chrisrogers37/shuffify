"""Shared playlist setup in a real browser: staging, review, acknowledgment, Save and Create in Spotify.

Each flow drives the app against the isolated fake
Spotify at a desktop, a tablet and a phone width unless its docstring says otherwise. It checks the outcome in the
app's own reads (the setup context, the routines, APScheduler's job store) and in the fake's playlists, not only in
what the page says. Every request the page sends is recorded, and a flow fails on a native dialog, a console error or
any failed request it did not provoke on purpose.
"""

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, hold_playlist_lock, pid
from tests.e2e.test_compatibility_and_dialogs import SIZES
from tests.e2e.test_compatibility_and_dialogs import open_session as open_session
from tests.e2e.test_direct_live_actions import _open_inbox, _open_tool
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.e2e.test_live_operation_outcomes import _registration
from tests.e2e.test_playlist_workspace import _no_sideways_scroll, _scroll_settled

TARGET = pid("alice-morning-mix")
ARCHIVE = pid("alice-morning-archive")
OLD_FAITHFUL = pid("alice-old-faithful")
BOB_FINDS = pid("bob-finds")
LATE_NIGHT = pid("carol-late-night")
FOCUS = pid("frank-focus")
SETUP_PATH = f"/playlist/{TARGET}/setup"
SETUP = f"{APP}{SETUP_PATH}"
WORKSHOP = f"{APP}/workshop/{TARGET}"
IMPACT = f"{SETUP_PATH}/impact"
SETTINGS = f"{SETUP_PATH}/settings"
CREATE = f"{SETUP_PATH}/spotify-playlists"
DESKTOP, PHONE = (1440, 900), (375, 812)

SAVED = "Settings saved. Existing routines now use these settings. No routine was started."
SAVED_NONE = "Settings saved. No existing routines are affected. This does not schedule anything."
NO_ROUTINES = "No existing routines are affected. This does not schedule anything."
ACK_AFFECTED = (
    "I understand these existing routines will use these settings. Saving does not start or schedule any routine."
)
BUSY = "A routine or save is running on this playlist. Try again when it finishes."
CREATION_UNKNOWN = (
    "We could not confirm whether the playlist was created. Check your Spotify playlists before creating another."
)
DISCARD = "Discard unsaved settings changes?"
PAGE_ONLY = "These details are kept only on this page."
# The server's Spotify client gives up on a request after 30 seconds (REQUEST_TIMEOUT in
# shuffify/spotify/http_client.py); the fake holds the creation longer than that, at most 60.
CREATION_HOLD = 60


# ---- The app's own reads, and seeding through its routes ----


def _context(alice):
    """The saved shared settings, as the app reads them (GET .../setup/context)."""
    return alice.ok("get", f"{SETUP_PATH}/context")["context"]


def _schedules(alice):
    return {s["id"]: s for s in alice.ok("get", f"/playlist/{TARGET}/schedules")["schedules"]}


def _watch(alice, playlist_id, name):
    """A saved source, through the legacy route and without its automatic routine."""
    return alice.ok("post", f"/playlist/{TARGET}/raid-watch", json={
        "source_playlist_id": playlist_id, "source_playlist_name": name, "auto_schedule": False,
    })["source"]


def _create_routine(alice, kind, every):
    payload = {
        "job_type": kind, "target_playlist_id": TARGET, "target_playlist_name": "Morning Mix",
        "schedule_type": "interval", "schedule_value": every,
    }
    if kind.startswith("raid"):
        payload["source_playlist_ids"] = [BOB_FINDS]
    if kind in ("shuffle", "raid_and_shuffle"):
        payload["algorithm_name"] = "BasicShuffle"
    return alice.ok("post", "/schedules/create", json=payload)["schedule"]


def _eventually(read, timeout=10):
    """read() once it returns something truthy, or its last value after the timeout."""
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(0.2)


# ---- The page ----


def _sent(s):
    """(method, path) of every request the page sent that was not a GET, in order."""
    return [(method, path) for method, path, _ in s.sent]


def _listen(page):
    """Record every text the shared announcer reads from now on, in window.__announced."""
    page.evaluate(
        """() => {
            window.__announced = [];
            const region = document.getElementById('live-operation-announcer');
            new MutationObserver(() => window.__announced.push(region.textContent.trim()))
                .observe(region, {childList: true, characterData: true, subtree: true});
        }"""
    )


def _announced(page):
    return page.evaluate("window.__announced")


def _add_by_link(page, playlist_id):
    page.fill("#setup-add-playlist-input", f"https://open.spotify.com/playlist/{playlist_id}")
    page.click("#setup-add-playlist")


def _use_existing(page, purpose, playlist_id):
    """Choose one of the person's playlists in a section's chooser, then Use existing playlist."""
    select = page.locator(f"#setup-{purpose}-select")
    expect(select.locator(f'option[value="{playlist_id}"]')).to_have_count(1)
    select.select_option(playlist_id)
    page.click(f"#setup-{purpose}-use")


def _review(page):
    """Review changes, and wait for the review the read-only preview returns."""
    with page.expect_response(lambda response: response.url == f"{APP}{IMPACT}") as reply:
        page.click("#setup-review-btn")
    assert reply.value.status == 200
    expect(page.locator("#setup-review")).to_be_visible()
    expect(page.locator("#setup-review-heading")).to_be_focused()


def _save(page, status=200):
    """Press Save playlist settings once; the server must answer with this status."""
    with page.expect_response(lambda response: response.url == f"{APP}{SETTINGS}") as reply:
        page.click("#setup-save")
    assert reply.value.status == status, reply.value.text()


def _pending(page, count):
    words = "No pending changes" if count == 0 else f"{count} pending change{'' if count == 1 else 's'}"
    expect(page.locator("#setup-pending-count")).to_have_text(words)


def _ask_to_leave(page):
    """Press Back to playlist; the shared confirmation it opens."""
    page.click("#setup-back")
    confirm = page.locator("#app-confirm")
    expect(confirm).to_be_visible()
    return confirm


def _focus_clear_of_footer(page):
    """The focused control is on screen and, unless it is in the action footer, ends above that footer, which is
    on screen too. Scroll offsets are whole pixels and layout positions are not, so a box may land a pixel lower."""
    box = page.evaluate(
        """() => {
            const el = document.activeElement, footer = document.getElementById('setup-footer');
            const r = el.getBoundingClientRect(), f = footer.getBoundingClientRect();
            return {name: el.id || el.getAttribute('data-setup-focus') || el.localName, inside: footer.contains(el),
                    top: r.top, bottom: r.bottom, footerTop: f.top, footerBottom: f.bottom, height: innerHeight};
        }"""
    )
    assert 0 <= box["footerTop"] and box["footerBottom"] <= box["height"] + 1, box
    assert 0 <= box["top"] and box["bottom"] <= box["height"] + 1, box
    if not box["inside"]:
        assert box["bottom"] <= box["footerTop"] + 1, box
    return box["name"]


def _tab_to(page, element_id, limit=40):
    """Press Tab until focus reaches the element; every control focus stops on on the way must clear the footer."""
    path = []
    for _ in range(limit):
        page.keyboard.press("Tab")
        _scroll_settled(page)
        path.append(_focus_clear_of_footer(page))
        if page.evaluate("document.activeElement.id") == element_id:
            return path
    raise AssertionError(f"Tab never reached #{element_id}: {path}")


# ---- Flows ----


@pytest.mark.parametrize("width,height", SIZES)
def test_an_added_source_is_reviewed_against_every_collecting_routine_and_saved_without_starting_one(
    alice, fake, open_session, width, height
):
    """Two active collecting routines, a paused one and a shuffle share Morning Mix's sources. Adding Late Night
    by link is reviewed against the three collecting routines, by operation, timing and Active or Paused, and Save
    waits for the acknowledgment. Once saved, every collecting routine's source list holds it; no routine's timing,
    state or APScheduler job changes, none runs, and Spotify is not written."""
    _watch(alice, BOB_FINDS, "Bob's Finds")
    collect = _create_routine(alice, "raid", "daily")
    composite = _create_routine(alice, "raid_and_shuffle", "every_12h")
    paused = _create_routine(alice, "raid_and_drip", "every_3d")
    shuffle = _create_routine(alice, "shuffle", "weekly")
    ids = [collect["id"], composite["id"], paused["id"], shuffle["id"]]
    collecting = ids[:3]
    try:
        assert alice.ok("post", f"/schedules/{paused['id']}/toggle")["schedule"]["is_enabled"] is False
        before = _schedules(alice)
        # The harness scheduler's PostgreSQL job store holds a job for each active routine, none for the paused one.
        jobs = {sid: _registration(sid) for sid in ids}
        assert [sid for sid in ids if jobs[sid] is None] == [paused["id"]]
        spotify = fake.playlists()
        s = open_session(width, height)
        page = s.page
        page.goto(SETUP, wait_until="networkidle")
        _listen(page)
        expect(page.locator("#setup-title")).to_have_text("Playlist setup · Morning Mix")
        expect(page.locator("#setup-boundary")).to_have_text("Settings for this playlist · Save applies immediately")
        expect(page.locator("#setup-routines li")).to_have_text([
            f"Collect · Every day (#{collect['id']}) — Active",
            f"Collect and shuffle · Every 12 hours (#{composite['id']}) — Active",
            f"Collect and add · Every 3 days (#{paused['id']}) — Paused",
            f"Shuffle · Every week (#{shuffle['id']}) — Active",
        ])

        _add_by_link(page, LATE_NIGHT)
        _pending(page, 1)
        expect(page.locator("#setup-source-limit")).to_have_text("2 of 10 sources")
        assert s.sent == [], "staging sent a request"
        _review(page)
        expect(page.locator("#setup-review-changes li")).to_have_text(["Add source: Late Night (playlist)"])
        # Each collecting routine, by operation, timing and state, the paused one too; the shuffle collects
        # nothing, so it is not named.
        expect(page.locator("#setup-review-affected li")).to_have_text([
            f"Collect · Every day (#{collect['id']}) — Active — will use Late Night on its next run",
            f"Collect and shuffle · Every 12 hours (#{composite['id']}) — Active — will use Late Night on its next run",
            f"Collect and add · Every 3 days (#{paused['id']}) — Paused — will use Late Night on its next run",
        ])
        expect(page.locator("#setup-ack-text")).to_have_text(ACK_AFFECTED)

        # Save waits for the acknowledgment: pressed before it, it says why and sends nothing.
        save, ack = page.locator("#setup-save"), page.locator("#setup-ack")
        expect(save).to_have_attribute("aria-disabled", "true")
        save.click(force=True)
        expect(page.locator("#setup-status")).to_have_text("Check the acknowledgment in the review first.")
        assert s.writes("PUT", SETTINGS) == []
        ack.check()
        expect(save).not_to_have_attribute("aria-disabled", "true")
        _save(page)
        expect(page.locator("#setup-outcome-heading")).to_have_text("Settings saved")
        expect(page.locator("#setup-outcome-text")).to_have_text(SAVED)
        expect(page.locator("#setup-outcome-heading")).to_be_focused()
        _pending(page, 0)
        expect(page.locator("#setup-source-list > li[data-source-id]")).to_have_count(2)
        # The announcer reads it once, after the outcome is shown.
        expect(page.locator("#live-operation-announcer")).to_have_text(SAVED)
        assert _announced(page).count(SAVED) == 1 and _announced(page)[-1] == SAVED
        assert _sent(s) == [("POST", IMPACT), ("PUT", SETTINGS)]

        # The app's own reads: the source is saved, and every collecting routine now lists it.
        added = [src for src in _context(alice)["sources"] if src["playlist_id"] == LATE_NIGHT]
        assert [(src["type"], src["name"], src["raid_count"]) for src in added] == [("external", "Late Night", 5)]
        after = _schedules(alice)
        for sid in collecting:
            assert after[sid]["source_playlist_ids"] == [BOB_FINDS, LATE_NIGHT], sid
        assert after[shuffle["id"]]["source_playlist_ids"] == []
        for sid in ids:
            for field in ("job_type", "schedule_type", "schedule_value", "is_enabled", "algorithm_name"):
                assert after[sid][field] == before[sid][field], (sid, field)
            # The same APScheduler job, next run and trigger alike: nothing was registered again or removed, and
            # the paused routine still has none.
            assert _registration(sid) == jobs[sid], sid
            assert alice.ok("get", f"/schedules/{sid}/history")["history"] == [], "a routine ran"
        assert fake.playlists() == spotify
        s.assert_clean()
    finally:
        for sid in ids:
            alice.ok("delete", f"/schedules/{sid}")


@pytest.mark.parametrize("width,height", SIZES)
def test_staging_undo_section_links_and_refresh_write_nothing_and_back_discards_only_after_asking(
    alice, fake, open_session, width, height
):
    """An addition, a removal, a count and a chosen collection are staged, one is undone, the section links and
    Refresh my playlists are used: the page sends no write, and Spotify and the saved settings stay as they were.
    Back asks before it discards them; Escape keeps the page and its changes, and confirming leaves."""
    bob = _watch(alice, BOB_FINDS, "Bob's Finds")
    focus = _watch(alice, FOCUS, "Focus")
    saved = _context(alice)
    spotify = fake.playlists()
    s = open_session(width, height)
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    loaded = len(s.navigations)

    _add_by_link(page, LATE_NIGHT)
    page.click(f'[data-setup-focus="source-remove-{bob["id"]}"]')
    focus_row = page.locator(f'#setup-source-list > li[data-source-id="{focus["id"]}"]')
    count = page.locator(f"#setup-source-count-{focus['id']}")
    count.fill("12")
    count.press("Tab")
    expect(focus_row).to_contain_text("Change pending")
    with page.expect_response(lambda response: "/setup/playlists?refresh=1" in response.url):
        page.click("#setup-collection-refresh")
    _use_existing(page, "collection", OLD_FAITHFUL)
    expect(page.locator("#setup-collection-current")).to_contain_text("Pending: use as the collection playlist")
    _pending(page, 4)

    # Undo removes just that change: the count goes back to the saved one.
    page.locator("#setup-pending-list > li", has_text="Focus: collect 12 per run").locator("[data-setup-undo]").click()
    _pending(page, 3)
    expect(count).to_have_value("5")
    expect(page.locator("#setup-pending-list .sh-setup-row-text")).to_have_text([
        "Add source: a playlist from a link (named when you review)", "Remove source: Bob's Finds",
        "Collection: use Old Faithful",
    ])
    # The section links stay on this page.
    for section in ("archive", "collection", "sources"):
        page.click(f'#setup-nav a[href="#setup-{section}"]')
        page.wait_for_url(f"{SETUP}#setup-{section}")
    with page.expect_response(lambda response: "/setup/playlists?refresh=1" in response.url):
        page.click("#setup-archive-refresh")
    _pending(page, 3)
    assert s.sent == [], "staging, Undo, the section links or Refresh sent a write"
    assert all(url.split("#")[0] == SETUP for url in s.navigations[loaded:]), s.navigations

    # Back asks first; Escape stays, with every change and focus back on Back.
    back = page.locator("#setup-back")
    expect(back).to_have_text("Back to playlist")
    confirm = _ask_to_leave(page)
    expect(page.locator("#app-confirm-heading")).to_have_text(DISCARD)
    expect(page.locator("#app-confirm-text")).to_have_text("Your pending changes are not saved. Leaving discards them.")
    expect(page.locator("#app-confirm-ok")).to_have_text("Discard and leave")
    expect(page.locator("#app-confirm-cancel")).to_have_text("Stay")
    page.keyboard.press("Escape")
    expect(confirm).to_be_hidden()
    expect(back).to_be_focused()
    assert page.url.split("#")[0] == SETUP
    _pending(page, 3)

    _ask_to_leave(page)
    page.click("#app-confirm-ok")
    page.wait_for_url(WORKSHOP)
    assert s.sent == []
    assert _context(alice) == saved
    assert fake.playlists() == spotify
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_settings_saved_without_routines_stay_after_leaving_and_show_when_setup_reopens(
    alice, fake, open_session, width, height
):
    """With no routine, a search source, an existing playlist as the collection with Drip on, and an archive with
    auto-archive off are reviewed and saved with the no-routine wording. Back then leaves without asking; the
    settings are saved, nothing is scheduled, Spotify is untouched, and reopening setup shows them."""
    spotify = fake.playlists()
    s = open_session(width, height)
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    page.fill("#setup-add-search-input", "late night jazz")
    page.click("#setup-add-search")
    _use_existing(page, "collection", OLD_FAITHFUL)
    page.check("#setup-drip-enabled")
    page.fill("#setup-drip-count", "4")
    page.press("#setup-drip-count", "Tab")
    _use_existing(page, "archive", ARCHIVE)
    page.uncheck("#setup-auto-archive")
    _pending(page, 5)

    _review(page)
    expect(page.locator("#setup-review-changes li")).to_have_text([
        "Collection: use Old Faithful",
        "Archive: use Morning Mix [Archive]",
        "Collection: add collected tracks automatically, add 4 per run",
        "Archive: stop auto-archiving removed tracks",
        "Add source: “late night jazz” (search)",
    ])
    expect(page.locator("#setup-review-affected")).to_be_hidden()
    expect(page.locator("#setup-review-notes li")).to_have_text([
        "Auto-archive is used only by Workshop drafts; no routine reads it.",
    ])
    expect(page.locator("#setup-ack-text")).to_have_text(NO_ROUTINES)
    page.check("#setup-ack")
    _save(page)
    expect(page.locator("#setup-outcome-text")).to_have_text(SAVED_NONE)
    _pending(page, 0)

    # Nothing is pending and nothing was created here, so Back leaves without asking.
    page.click("#setup-back")
    page.wait_for_url(WORKSHOP)
    context = _context(alice)
    assert [(src["type"], src["search_query"], src["name"], src["raid_count"]) for src in context["sources"]] == [
        ("search_query", "late night jazz", "late night jazz", 5)
    ]
    assert context["collection"] == {
        "playlist_id": OLD_FAITHFUL, "name": "Old Faithful", "drip_enabled": True, "drip_count": 4,
    }
    assert context["archive"] == {
        "playlist_id": ARCHIVE, "name": "Morning Mix [Archive]", "auto_archive_on_remove": False,
    }
    assert context["routines"] == [] and _schedules(alice) == {}, "saving scheduled something"
    assert fake.playlists() == spotify
    assert _sent(s) == [("POST", IMPACT), ("PUT", SETTINGS)]

    page.goto(SETUP, wait_until="networkidle")
    expect(page.locator("#setup-source-list > li[data-source-id] .sh-setup-row-text")).to_have_text([
        "late night jazz Search words",
    ])
    expect(page.locator("#setup-collection-current .sh-setup-name")).to_have_text("Old Faithful")
    expect(page.locator("#setup-drip-enabled")).to_be_checked()
    expect(page.locator("#setup-drip-count")).to_have_value("4")
    expect(page.locator("#setup-archive-current .sh-setup-name")).to_have_text("Morning Mix [Archive]")
    expect(page.locator("#setup-auto-archive")).not_to_be_checked()
    expect(page.locator("#setup-saved dd")).to_have_text([
        "1 source: late night jazz", "Old Faithful · adds 4 per run", "Morning Mix [Archive] · auto-archive off",
    ])
    _pending(page, 0)
    s.assert_clean()


def test_an_unconfirmed_creation_blocks_create_until_refresh_finds_it_and_is_never_retried(
    alice, fake, open_session
):
    """At the phone width only: the server's Spotify client waits 30 seconds before it gives up, so this flow takes
    that long. The fake holds the creation past the server's timeout; the page says it cannot tell, keeps Create
    blocked and never sends it again. Released, the fake applies the creation; Refresh my playlists finds it and
    points it out without selecting it. Exactly one creation reached the fake, and nothing was linked."""
    name = "Morning Mix Inbox"
    # times > 1: a second creation, from the page or the server, would be held and counted too.
    fake.arm(operation="POST /me/playlists", kind="slow", delay=CREATION_HOLD, times=5)
    before = {playlist["id"] for playlist in fake.playlists()}
    s = open_session(*PHONE)
    s.visit.allowed.add((f"{APP}{CREATE}", 502))
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    create, status = page.locator("#setup-collection-create"), page.locator("#setup-collection-create-status")
    page.fill("#setup-collection-create-name", name)
    create.click()
    expect(create).to_have_text("Creating…")
    expect(create).to_have_attribute("aria-disabled", "true")
    expect(page.locator("#setup-page")).to_have_attribute("aria-busy", "true")
    # A second press while it is pending sends nothing, and leaving is refused with the reason.
    create.click(force=True)
    page.click("#setup-back")
    expect(page.locator("#setup-status")).to_have_text(
        "Creating a playlist can't be cancelled by leaving; wait for it to finish."
    )
    assert page.url == SETUP

    # The server gives up after its Spotify timeout: the page cannot tell whether the playlist exists.
    expect(status).to_have_text(CREATION_UNKNOWN, timeout=(CREATION_HOLD + 10) * 1000)
    expect(create).to_have_text("Create playlist in Spotify")
    expect(create).to_have_attribute("aria-disabled", "true")
    expect(page.locator("#setup-collection-create-another")).to_be_hidden()
    create.click(force=True)
    expect(status).to_have_text(CREATION_UNKNOWN)
    assert len(s.writes("POST", CREATE)) == 1

    # Released, the fake applies the creation the server gave up on.
    fake.release()
    made = _eventually(lambda: [p for p in fake.playlists() if p["id"] not in before])
    assert [(p["name"], p["owner"], p["public"], p["uris"]) for p in made] == [(name, "alice", False, [])]
    with page.expect_response(lambda response: "/setup/playlists?refresh=1" in response.url):
        page.click("#setup-collection-refresh")
    expect(page.locator("#setup-collection-found")).to_be_visible()
    expect(page.locator("#setup-collection-found-text")).to_have_text(f"Found {name} — use it?")
    # Pointed out, never selected: the chooser lists it unselected and nothing is staged.
    select = page.locator("#setup-collection-select")
    expect(select.locator(f'option[value="{made[0]["id"]}"]')).to_have_count(1)
    assert select.input_value() == ""
    _pending(page, 0)
    expect(create).to_be_hidden()
    expect(page.locator("#setup-collection-create-another")).to_be_visible()

    assert [(f["operation"], f["fired"]) for f in fake.faults()] == [("POST /me/playlists", 1)]
    assert len([p for p in fake.playlists() if p["name"] == name]) == 1
    assert _sent(s) == [("POST", CREATE)]
    assert _context(alice)["collection"] is None
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_a_created_playlist_left_unsaved_is_named_on_leaving_and_nothing_is_linked(
    alice, fake, open_session, width, height
):
    """Create playlist in Spotify makes a private playlist and stages it as the collection. Leaving names it as
    staying in the Spotify library, with the pending link (Stay keeps the page) and after that link is undone.
    Leaving links nothing and deletes nothing: the playlist stays in Spotify."""
    name = "Morning Mix Waiting Room"
    before = {playlist["id"] for playlist in fake.playlists()}
    s = open_session(width, height)
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    page.fill("#setup-collection-create-name", name)
    with page.expect_response(lambda response: response.url == f"{APP}{CREATE}") as reply:
        page.click("#setup-collection-create")
    assert reply.value.status == 201
    expect(page.locator("#setup-collection-create-status")).to_have_text(
        f"Created {name} in your Spotify library. It stays there if you close setup. "
        "Save playlist settings to use it."
    )
    made = [p for p in fake.playlists() if p["id"] not in before]
    assert [(p["name"], p["owner"], p["public"], p["uris"]) for p in made] == [(name, "alice", False, [])]
    expect(page.locator("#setup-pending-list .sh-setup-row-text")).to_have_text([f"Collection: use {name}"])

    stays = f"{name} stays in your Spotify library. {PAGE_ONLY}"
    confirm = _ask_to_leave(page)
    expect(page.locator("#app-confirm-heading")).to_have_text(DISCARD)
    expect(page.locator("#app-confirm-text")).to_have_text(
        f"Your pending changes are not saved. Leaving discards them. {stays}"
    )
    page.click("#app-confirm-cancel")
    expect(confirm).to_be_hidden()
    assert page.url == SETUP
    _pending(page, 1)

    # Without the pending link, leaving still names the playlist made here.
    page.click("#setup-pending-list [data-setup-undo]")
    _pending(page, 0)
    _ask_to_leave(page)
    expect(page.locator("#app-confirm-heading")).to_have_text("Leave setup?")
    expect(page.locator("#app-confirm-text")).to_have_text(stays)
    expect(page.locator("#app-confirm-ok")).to_have_text("Leave")
    page.click("#app-confirm-ok")
    page.wait_for_url(WORKSHOP)

    assert _context(alice)["collection"] is None
    assert fake.playlist(made[0]["id"])["name"] == name
    assert _sent(s) == [("POST", CREATE)]
    s.assert_clean()


def test_a_save_while_the_playlist_is_busy_writes_nothing_and_saves_once_it_is_free(alice, fake, open_session):
    """At the desktop width only. With the target's lock held, as a running routine holds it, Save answers Busy,
    keeps the review and its acknowledgment, and writes nothing. Released, the same Save saves."""
    saved = _context(alice)
    spotify = fake.playlists()
    s = open_session(*DESKTOP)
    s.visit.allowed.add((f"{APP}{SETTINGS}", 409))
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    _add_by_link(page, LATE_NIGHT)
    _review(page)
    page.check("#setup-ack")

    with hold_playlist_lock(TARGET):
        _save(page, 409)
        expect(page.locator("#setup-outcome-heading")).to_have_text("Not saved yet")
        expect(page.locator("#setup-outcome-text")).to_have_text(BUSY)
        assert _context(alice) == saved, "a busy Save wrote something"
    # Everything is kept, and Save stays available.
    _pending(page, 1)
    expect(page.locator("#setup-ack")).to_be_checked()
    expect(page.locator("#setup-save")).not_to_have_attribute("aria-disabled", "true")

    _save(page)
    expect(page.locator("#setup-outcome-text")).to_have_text(SAVED_NONE)
    assert [src["playlist_id"] for src in _context(alice)["sources"]] == [LATE_NIGHT]
    assert _sent(s) == [("POST", IMPACT), ("PUT", SETTINGS), ("PUT", SETTINGS)]
    assert fake.playlists() == spotify
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_the_workshop_opens_playlist_setup_at_its_section_and_returns_to_the_workshop(
    alice, fake, open_session, width, height
):
    """Each Workshop entry link opens setup at its section with the Workshop as the way back. From Source setup,
    Playlist setup opens focused on Sources, and Back to playlist returns to the Workshop; from
    Archive setup, it opens focused on Archive. Nothing is sent, and nothing changes."""
    saved = _context(alice)
    spotify = fake.playlists()
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    links = page.locator('a[data-workshop-compat="view"][href*="/setup"]')
    back_to = f"return=/workshop/{TARGET}"
    assert links.evaluate_all(
        "links => links.map(a => [a.closest('[data-app-panel]').id, a.getAttribute('href'), a.textContent.trim()])"
    ) == [
        ["workshop-inbox-panel", f"{SETUP_PATH}?section=sources&{back_to}", "Playlist setup"],
        ["workshop-sources-panel", f"{SETUP_PATH}?section=sources&{back_to}", "Playlist setup"],
        ["workshop-archive-setup-panel", f"{SETUP_PATH}?section=archive&{back_to}", "Playlist setup"],
    ]

    def follow(link, section):
        link.click()
        page.wait_for_url(lambda url: url.startswith(f"{SETUP}?"))
        assert parse_qs(urlsplit(page.url).query) == {"section": [section], "return": [f"/workshop/{TARGET}"]}
        expect(page.locator(f"#setup-{section}-heading")).to_be_focused()
        back = page.locator("#setup-back")
        expect(back).to_have_text("Back to playlist")
        assert back.get_attribute("href") == f"/workshop/{TARGET}"
        back.click()
        page.wait_for_url(WORKSHOP)

    _open_inbox(page)
    page.click("#workshop-sources-btn")
    expect(page.locator("#workshop-sources-panel")).to_be_visible()
    follow(page.locator("#workshop-sources-panel a[href*='/setup']"), "sources")

    _open_tool(page, "archive")
    page.wait_for_function("() => archiveState.isLoaded")
    page.click("#workshop-archive-setup-btn")
    expect(page.locator("#workshop-archive-setup-panel")).to_be_visible()
    follow(page.locator("#workshop-archive-setup-panel a[href*='/setup']"), "archive")
    assert s.sent == [] and fake.playlists() == spotify and _context(alice) == saved
    s.assert_clean()


def test_a_source_is_staged_reviewed_acknowledged_and_saved_by_keyboard_on_a_phone(alice, fake, open_session):
    """At the phone width only, with Tab, Enter and Space alone: add a source by link, review it, acknowledge and
    save. Each control focus stops on is on screen and clear of the sticky action footer, which stays on screen,
    and the page never scrolls sideways."""
    s = open_session(*PHONE)
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    assert _no_sideways_scroll(page)
    # Setup opens at Sources.
    expect(page.locator("#setup-sources-heading")).to_be_focused()

    _tab_to(page, "setup-add-playlist-input")
    page.keyboard.type(f"https://open.spotify.com/playlist/{LATE_NIGHT}")
    page.keyboard.press("Enter")
    _pending(page, 1)
    expect(page.locator("#setup-add-playlist-input")).to_be_focused()
    expect(page.locator("#setup-add-playlist-input")).to_have_value("")

    _tab_to(page, "setup-review-btn")
    with page.expect_response(lambda response: response.url == f"{APP}{IMPACT}"):
        page.keyboard.press("Enter")
    expect(page.locator("#setup-review-heading")).to_be_focused()
    _scroll_settled(page)
    _focus_clear_of_footer(page)
    assert _no_sideways_scroll(page)

    _tab_to(page, "setup-ack")
    page.keyboard.press("Space")
    expect(page.locator("#setup-ack")).to_be_checked()
    _tab_to(page, "setup-save")
    with page.expect_response(lambda response: response.url == f"{APP}{SETTINGS}"):
        page.keyboard.press("Enter")
    expect(page.locator("#setup-outcome-text")).to_have_text(SAVED_NONE)
    expect(page.locator("#setup-outcome-heading")).to_be_focused()
    _scroll_settled(page)
    _focus_clear_of_footer(page)
    assert _no_sideways_scroll(page)

    assert [src["playlist_id"] for src in _context(alice)["sources"]] == [LATE_NIGHT]
    assert _sent(s) == [("POST", IMPACT), ("PUT", SETTINGS)]
    s.assert_clean()
