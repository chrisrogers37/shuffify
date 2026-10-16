"""React shared setup stages locally and saves exact reviewed settings against real services."""

import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, hold_playlist_lock, pid
from tests.e2e.test_compatibility_and_dialogs import SIZES
from tests.e2e.test_compatibility_and_dialogs import open_session as open_session
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.e2e.test_live_operation_outcomes import _registration
from tests.e2e.test_playlist_workspace import _no_sideways_scroll, _open_tool, _panel
from tests.paths import api_path
from tests.routine_flow import add_playlist_source, create_routine, list_schedules, save_setup, set_routine_enabled

TARGET = pid("alice-morning-mix")
ARCHIVE = pid("alice-morning-archive")
OLD_FAITHFUL = pid("alice-old-faithful")
BOB_FINDS = pid("bob-finds")
LATE_NIGHT = pid("carol-late-night")
FOCUS = pid("frank-focus")
SETUP_PATH = f"/playlist/{TARGET}/setup"
SETUP = f"{APP}{SETUP_PATH}"
WORKSHOP = f"{APP}/workshop/{TARGET}"
IMPACT = api_path(f"{SETUP_PATH}/impact")
SETTINGS = api_path(f"{SETUP_PATH}/settings")
CREATE = api_path(f"{SETUP_PATH}/spotify-playlists")
DESKTOP, PHONE = (1440, 900), (375, 812)
CREATION_HOLD = 60
ACK = "I understand these settings affect existing routines"


def _context(alice):
    return alice.ok("get", f"{SETUP_PATH}/context")["context"]


def _schedules(alice):
    return {row["id"]: row for row in list_schedules(alice, TARGET)}


def _watch(alice, playlist_id, name):
    return add_playlist_source(alice, TARGET, playlist_id)


def _create_routine(alice, kind, every):
    return create_routine(
        alice,
        TARGET,
        job_type=kind,
        timing={
            "kind": "interval",
            "interval": every,
            "timezone": "UTC",
        },
    )


def _eventually(read, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(0.2)


def _sent(s):
    return [(method, path) for method, path, _ in s.sent]


def _add_by_link(page, playlist_id):
    page.get_by_label("Add by playlist link or ID", exact=True).fill(f"https://open.spotify.com/playlist/{playlist_id}")
    page.get_by_role("button", name="Add source", exact=True).click()


def _use_existing(page, purpose, playlist_id):
    select = page.get_by_label(f"Your playlists for {purpose}", exact=True)
    if select.locator(f'option[value="{playlist_id}"]').count() == 0:
        page.get_by_role("button", name=f"Refresh my playlists for {purpose}", exact=True).click()
    expect(select.locator(f'option[value="{playlist_id}"]')).to_have_count(1)
    select.select_option(playlist_id)
    page.get_by_role("button", name=f"Use existing {purpose}", exact=True).click()


def _review(page):
    with page.expect_response(lambda response: urlsplit(response.url).path == IMPACT) as reply:
        page.get_by_role("button", name="Review playlist settings", exact=True).click()
    assert reply.value.status == 200, reply.value.text()
    expect(page.get_by_role("heading", name="Review shared impact", exact=True)).to_be_visible()
    return reply.value.json()


def _save(page, status=200):
    with page.expect_response(
        lambda response: urlsplit(response.url).path == SETTINGS and response.request.method == "PUT"
    ) as reply:
        page.get_by_role("button", name="Save playlist settings", exact=True).click()
    assert reply.value.status == status, reply.value.text()
    if status == 200:
        expect(page.get_by_text(re.compile(r"^Playlist settings saved\.")).first).to_be_visible()
    return reply.value.json()


def _pending(page, count):
    expect(page.locator(".sh-setup-summary")).to_contain_text(f"{count} of 20 changes staged")


def _ask_to_leave(page):
    page.get_by_role("link", name="Back to playlist", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("heading", name="Leave this page?", exact=True)).to_be_visible()
    return dialog


def _focus_visible(page, control):
    target = control.locator("xpath=..") if control.get_attribute("type") == "checkbox" else control
    target.scroll_into_view_if_needed()
    control.focus()
    expect(control).to_be_focused()
    page.wait_for_function("""element => {
        const target = element.type === 'checkbox' ? element.closest('label') : element;
        const {y, height} = target.getBoundingClientRect();
        return y >= 0 && y + height <= innerHeight + 1;
    }""", arg=control.element_handle(), timeout=5000)
    box = control.evaluate("""element => {
        const target = element.type === 'checkbox' ? element.closest('label') : element;
        const {x, y, width, height} = target.getBoundingClientRect();
        return {x, y, width, height};
    }""")
    assert box and box["height"] >= 44 and 0 <= box["y"] <= page.viewport_size["height"] - box["height"] + 1
    assert _no_sideways_scroll(page)


@pytest.mark.parametrize("width,height", SIZES)
def test_an_added_source_is_reviewed_against_every_collecting_routine_and_saved_without_starting_one(
    alice,
    fake,
    open_session,
    width,
    height,
):
    _watch(alice, BOB_FINDS, "Bob's Finds")
    save_setup(
        alice,
        TARGET,
        [
            {"op": "link_collection", "playlist_id": OLD_FAITHFUL},
            {"op": "set_drip", "drip_enabled": True},
        ],
    )
    collecting = [
        _create_routine(alice, kind, every)["id"]
        for kind, every in (
            ("raid", "daily"),
            ("raid_and_shuffle", "every_12h"),
            ("raid_and_drip", "every_3d"),
        )
    ]
    shuffle = _create_routine(alice, "shuffle", "weekly")["id"]
    ids = collecting + [shuffle]
    try:
        set_routine_enabled(alice, collecting[2], False)
        before = _schedules(alice)
        jobs = {sid: _registration(sid) for sid in ids}
        assert [sid for sid in ids if jobs[sid] is None] == [collecting[2]]
        spotify = fake.playlists()
        s = open_session(width, height)
        page = s.page
        page.goto(SETUP, wait_until="networkidle")
        expect(page.get_by_role("heading", name="Playlist setup · Morning Mix", exact=True)).to_be_visible()
        _add_by_link(page, LATE_NIGHT)
        _pending(page, 1)
        assert s.sent == []
        review = _review(page)
        assert {row["id"] for row in review["affected_routines"]} == set(collecting)
        impact = page.locator(".sh-setup-summary .sh-routine-group")
        for sid in collecting:
            expect(impact).to_contain_text(f"Routine #{sid} ·")
        expect(impact).not_to_contain_text(f"Routine #{shuffle} ·")
        save = page.get_by_role("button", name="Save playlist settings", exact=True)
        expect(save).to_be_disabled()
        assert s.writes("PUT", SETTINGS) == []
        page.get_by_label(ACK, exact=True).check()
        _save(page)
        _pending(page, 0)
        assert _sent(s) == [("POST", IMPACT), ("PUT", SETTINGS)]
        added = [src for src in _context(alice)["sources"] if src["playlist_id"] == LATE_NIGHT]
        assert [(src["type"], src["name"], src["raid_count"]) for src in added] == [("external", "Late Night", 5)]
        after = _schedules(alice)
        rows = alice.ok("get", f"/playlist/{TARGET}/routines/context")["routines"]
        source_ids = {src["id"] for src in _context(alice)["sources"]}
        for sid in collecting:
            assert after[sid]["source_playlist_ids"] == before[sid]["source_playlist_ids"] + [LATE_NIGHT]
            row = next(row for row in rows if row["schedule_id"] == sid)
            scope = alice.ok(
                "post",
                f"/playlist/{TARGET}/routines/review",
                json={
                    "request_key": f"source-scope-{sid}",
                    "definition": {},
                    "schedule_id": sid,
                    "expected_revision": row["schedule_revision"],
                },
            )
            assert set(scope["resource_scope"]["source_ids"]) == source_ids
        assert after[shuffle]["source_playlist_ids"] == []
        for sid in ids:
            for field in ("job_type", "schedule_type", "schedule_value", "is_enabled", "algorithm_name"):
                assert after[sid][field] == before[sid][field], (sid, field)
            assert _registration(sid) == jobs[sid]
            assert alice.ok("get", f"/schedules/{sid}/history")["history"] == []
        assert fake.playlists() == spotify
        s.assert_clean()
    finally:
        for sid in ids:
            alice.ok("delete", f"/schedules/{sid}")


@pytest.mark.parametrize("width,height", SIZES)
def test_staging_undo_section_links_and_refresh_write_nothing_and_back_discards_only_after_asking(
    alice,
    fake,
    open_session,
    width,
    height,
):
    _watch(alice, BOB_FINDS, "Bob's Finds")
    _watch(alice, FOCUS, "Focus")
    saved, spotify = _context(alice), fake.playlists()
    s = open_session(width, height)
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    _add_by_link(page, LATE_NIGHT)
    page.get_by_role("button", name="Remove Bob's Finds", exact=True).click()
    count = page.get_by_label("Tracks to collect from Focus", exact=True)
    count.fill("12")
    _use_existing(page, "collection", OLD_FAITHFUL)
    _pending(page, 4)
    page.get_by_role("button", name="Undo Change source collection count", exact=True).click()
    _pending(page, 3)
    expect(count).to_have_value("5")
    for section in ("Archive", "Collection", "Sources"):
        page.get_by_role("navigation", name="Setup sections").get_by_role("link", name=section, exact=True).click()
        expect(page).to_have_url(f"{SETUP}#setup-{section.lower()}")
    page.get_by_role("button", name="Refresh my playlists for archive", exact=True).click()
    _pending(page, 3)
    assert s.sent == [] and _context(alice) == saved and fake.playlists() == spotify
    dialog = _ask_to_leave(page)
    expect(dialog).to_contain_text("Your staged playlist settings are not saved.")
    page.keyboard.press("Escape")
    expect(page.get_by_role("link", name="Back to playlist", exact=True)).to_be_focused()
    _pending(page, 3)
    _ask_to_leave(page).get_by_role("button", name="Leave page", exact=True).click()
    expect(page).to_have_url(WORKSHOP)
    assert s.sent == [] and _context(alice) == saved and fake.playlists() == spotify
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_settings_saved_without_routines_stay_after_leaving_and_show_when_setup_reopens(
    alice,
    fake,
    open_session,
    width,
    height,
):
    spotify = fake.playlists()
    s = open_session(width, height)
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    page.get_by_label("Add by search words", exact=True).fill("late night jazz")
    page.get_by_role("button", name="Add search source", exact=True).click()
    _use_existing(page, "collection", OLD_FAITHFUL)
    page.get_by_label("Enable adding collected tracks", exact=True).check()
    page.get_by_label("Tracks added per run", exact=True).fill("4")
    _use_existing(page, "archive", ARCHIVE)
    page.get_by_label("Automatically archive tracks removed in Workshop", exact=True).uncheck()
    _pending(page, 5)
    reviewed = _review(page)
    assert reviewed["affected_routines"] == []
    page.get_by_label(ACK, exact=True).check()
    _save(page)
    _pending(page, 0)
    page.get_by_role("link", name="Back to playlist", exact=True).click()
    expect(page).to_have_url(WORKSHOP)
    expect(page.get_by_role("heading", name="Morning Mix", exact=True)).to_be_visible()
    context = _context(alice)
    assert [(src["type"], src["search_query"], src["name"], src["raid_count"]) for src in context["sources"]] == [
        ("search_query", "late night jazz", "late night jazz", 5),
    ]
    assert context["collection"] == {
        "playlist_id": OLD_FAITHFUL,
        "name": "Old Faithful",
        "drip_enabled": True,
        "drip_count": 4,
    }
    assert context["archive"] == {
        "playlist_id": ARCHIVE,
        "name": "Morning Mix [Archive]",
        "auto_archive_on_remove": False,
    }
    assert context["routines"] == [] and _schedules(alice) == {}
    assert fake.playlists() == spotify and _sent(s) == [("POST", IMPACT), ("PUT", SETTINGS)]
    page.goto(SETUP, wait_until="networkidle")
    expect(page.locator("#setup-collection")).to_contain_text("Saved: Old Faithful")
    expect(page.locator("#setup-archive")).to_contain_text("Saved: Morning Mix [Archive]")
    expect(page.get_by_label("Enable adding collected tracks", exact=True)).to_be_checked()
    expect(page.get_by_label("Tracks added per run", exact=True)).to_have_value("4")
    expect(page.get_by_label("Automatically archive tracks removed in Workshop", exact=True)).not_to_be_checked()
    _pending(page, 0)
    s.assert_clean()


def test_an_unconfirmed_creation_blocks_create_after_refresh_and_is_never_retried(alice, fake, open_session):
    name = "Morning Mix Inbox"
    fake.arm(operation="POST /me/playlists", kind="slow", delay=CREATION_HOLD, times=5)
    before = {playlist["id"] for playlist in fake.playlists()}
    s = open_session(*PHONE)
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    page.get_by_label("Name a new collection playlist", exact=True).fill(name)
    create = page.get_by_role("button", name="Create collection playlist in Spotify", exact=True)
    with page.expect_response(lambda response: urlsplit(response.url).path == CREATE) as reply:
        create.dblclick()
    assert reply.value.status == 202
    expect(create).to_be_disabled()
    expect(page.get_by_text(re.compile(r"^Creation could not be confirmed\. The earlier request"))).to_be_visible(
        timeout=(CREATION_HOLD + 10) * 1000
    )
    expect(create).to_be_disabled()
    assert len(s.writes("POST", CREATE)) == 1
    fake.release()
    made = _eventually(lambda: [row for row in fake.playlists() if row["id"] not in before])
    assert [(row["name"], row["owner"], row["public"], row["uris"]) for row in made] == [(name, "alice", False, [])]
    page.get_by_role("button", name="Refresh my playlists for collection", exact=True).click()
    select = page.get_by_label("Your playlists for collection", exact=True)
    expect(select.locator(f'option[value="{made[0]["id"]}"]')).to_have_count(1)
    expect(select).to_have_value("")
    _pending(page, 0)
    expect(create).to_be_disabled()
    assert [(fault["operation"], fault["fired"]) for fault in fake.faults()] == [("POST /me/playlists", 1)]
    assert len([row for row in fake.playlists() if row["name"] == name]) == 1
    assert _sent(s) == [("POST", CREATE)] and _context(alice)["collection"] is None
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_a_created_playlist_requires_exact_selection_and_leaving_never_links_or_deletes_it(
    alice,
    fake,
    open_session,
    width,
    height,
):
    name = "Morning Mix Waiting Room"
    before = {row["id"] for row in fake.playlists()}
    s = open_session(width, height)
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    page.get_by_label("Name a new collection playlist", exact=True).fill(name)
    with page.expect_response(lambda response: urlsplit(response.url).path == CREATE) as reply:
        page.get_by_role("button", name="Create collection playlist in Spotify", exact=True).click()
    assert reply.value.status == 202
    expect(page.get_by_text(f"Created: {name}", exact=True)).to_be_visible(timeout=45000)
    expect(page.get_by_text("Private visibility confirmed.", exact=True)).to_be_visible()
    made = [row for row in fake.playlists() if row["id"] not in before]
    assert [(row["name"], row["owner"], row["public"], row["uris"]) for row in made] == [(name, "alice", False, [])]
    _pending(page, 0)
    assert _context(alice)["collection"] is None
    page.get_by_role("button", name="Use created collection", exact=True).click()
    _pending(page, 1)
    _ask_to_leave(page).get_by_role("button", name="Stay", exact=True).click()
    _pending(page, 1)
    page.get_by_role("button", name="Undo Link collection", exact=True).click()
    _pending(page, 0)
    dialog = _ask_to_leave(page)
    expect(dialog).to_contain_text(f"{name} stays in your Spotify library.")
    dialog.get_by_role("button", name="Leave page", exact=True).click()
    expect(page).to_have_url(WORKSHOP)
    assert _context(alice)["collection"] is None
    assert fake.playlist(made[0]["id"])["name"] == name
    assert _sent(s) == [("POST", CREATE)]
    s.assert_clean()


def test_a_save_while_the_playlist_is_busy_writes_nothing_and_saves_once_it_is_free(alice, fake, open_session):
    saved, spotify = _context(alice), fake.playlists()
    s = open_session(*DESKTOP)
    s.visit.allowed.add((f"{APP}{SETTINGS}", 409))
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    _add_by_link(page, LATE_NIGHT)
    _review(page)
    page.get_by_label(ACK, exact=True).check()
    with hold_playlist_lock(TARGET):
        failed = _save(page, 409)
        assert failed["error_code"] == "playlist_busy"
        assert _context(alice) == saved
    _pending(page, 1)
    expect(page.get_by_label(ACK, exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Save playlist settings", exact=True)).to_have_count(0)
    _review(page)
    expect(page.get_by_label(ACK, exact=True)).not_to_be_checked()
    page.get_by_label(ACK, exact=True).check()
    _save(page)
    assert [row["playlist_id"] for row in _context(alice)["sources"]] == [LATE_NIGHT]
    assert _sent(s) == [("POST", IMPACT), ("PUT", SETTINGS), ("POST", IMPACT), ("PUT", SETTINGS)]
    assert json.loads(s.sent[0][2])["changes"] == json.loads(s.sent[2][2])["changes"]
    assert json.loads(s.sent[1][2])["changes"] == json.loads(s.sent[3][2])["changes"]
    assert fake.playlists() == spotify
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_the_workshop_opens_playlist_setup_and_returns_to_the_workshop(alice, fake, open_session, width, height):
    saved, spotify = _context(alice), fake.playlists()
    s = open_session(width, height)
    page = s.page
    for tool, setup in (("Inbox", "Source setup"), ("Archive", "Archive setup")):
        page.goto(WORKSHOP, wait_until="networkidle")
        _open_tool(page, tool)
        _panel(page).get_by_role("button", name=setup, exact=True).click()
        _panel(page).get_by_role("link", name="Playlist setup", exact=True).click()
        expect(page).to_have_url(re.compile(rf"{re.escape(SETUP)}\?return_target=playlist$"))
        assert parse_qs(urlsplit(page.url).query) == {"return_target": ["playlist"]}
        page.get_by_role("link", name="Back to playlist", exact=True).click()
        expect(page).to_have_url(WORKSHOP)
    assert s.sent == [] and fake.playlists() == spotify and _context(alice) == saved
    s.assert_clean()


def test_a_source_is_staged_reviewed_acknowledged_and_saved_by_keyboard_on_a_phone(alice, fake, open_session):
    s = open_session(*PHONE)
    page = s.page
    page.goto(SETUP, wait_until="networkidle")
    field = page.get_by_label("Add by playlist link or ID", exact=True)
    _focus_visible(page, field)
    page.keyboard.type(f"https://open.spotify.com/playlist/{LATE_NIGHT}")
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Add source", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    _pending(page, 1)
    review = page.get_by_role("button", name="Review playlist settings", exact=True)
    _focus_visible(page, review)
    with page.expect_response(lambda response: urlsplit(response.url).path == IMPACT):
        page.keyboard.press("Enter")
    ack = page.get_by_label(ACK, exact=True)
    _focus_visible(page, ack)
    page.keyboard.press("Space")
    expect(ack).to_be_checked()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Save playlist settings", exact=True)).to_be_focused()
    with page.expect_response(lambda response: urlsplit(response.url).path == SETTINGS):
        page.keyboard.press("Enter")
    expect(page.get_by_text(re.compile(r"^Playlist settings saved\.")).first).to_be_visible()
    assert _no_sideways_scroll(page)
    assert [row["playlist_id"] for row in _context(alice)["sources"]] == [LATE_NIGHT]
    assert _sent(s) == [("POST", IMPACT), ("PUT", SETTINGS)]
    s.assert_clean()
