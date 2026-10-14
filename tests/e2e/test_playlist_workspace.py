"""The playlist workspace in a real browser: the Workshop's draft, its tools and its exits.

Each flow drives the app against the isolated fake
Spotify at a desktop, a tablet and a phone width (Long Haul at the phone width only). It checks the outcome in the
fake's own playlists and in the requests the page sent, not only in what the page says, and fails on a native
dialog, a console error or any failed request.
"""

import json
import re
from collections import Counter

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, pid
from tests.e2e.test_compatibility_and_dialogs import SIZES
from tests.e2e.test_compatibility_and_dialogs import open_session as open_session
from tests.e2e.test_direct_live_actions import _close_tool, _open_inbox, _open_row_actions, _open_tool
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.e2e.test_live_operation_outcomes import _nav_to_settings

MORNING_MIX = pid("alice-morning-mix")
ON_REPEAT = pid("alice-on-repeat")
LONG_HAUL = pid("alice-long-haul")
WORKSHOP = f"{APP}/workshop/{MORNING_MIX}"
# The tools sit beside the playlist from this width; below it a tool covers the list.
WORKSPACE_WIDTH = 1024
# Every catalogue track matching "signal" on the first results page is already in Morning Mix; the second page
# (Load more) has only tracks it lacks.
QUERY = "signal"
DRAFT_NAME = "Weekday order"


def _commit(playlist_id):
    return f"/workshop/{playlist_id}/commit"


def _rows(page):
    return page.locator("#track-list .track-item")


def _shown(page):
    """The rows as the list shows them, in order: [occurrence key, URI] each."""
    return page.eval_on_selector_all(
        "#track-list .track-item", "rows => rows.map(row => [row.dataset.occurrence, row.dataset.uri])"
    )


def _uris(page):
    return [uri for _, uri in _shown(page)]


def _working(page):
    return page.evaluate("workshopState.workingUris")


def _focus(page):
    """Where focus is: its row action ("actions" for a row's Row actions button) and its row's occurrence key."""
    return page.evaluate(
        """() => {
            const el = document.activeElement, row = el.closest('.track-item');
            const what = el.classList.contains('track-actions-btn') ? 'actions' : el.dataset.rowAction || el.id;
            return [what, row ? row.dataset.occurrence : null];
        }"""
    )


def _no_sideways_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def _preview(page):
    """Press Preview shuffle once and wait for the draft it makes."""
    with page.expect_response(lambda response: response.url.endswith("/preview-shuffle")):
        page.click("#preview-shuffle-btn")
    page.wait_for_function("() => !workshopState.isShuffling")
    expect(page.locator("#workshop-save-state")).to_have_text("Draft")


def _review(page):
    """Open Review & save from the page (no tool open); its summary."""
    page.click("#save-btn")
    page.locator("#workshop-save-panel").wait_for(state="visible")
    return page.locator("#workshop-review-summary").inner_text()


def _save_to_spotify(page):
    """Save to Spotify from the open review, and wait for the verified result."""
    page.click("#workshop-review-save")
    page.wait_for_function(
        "() => workshopState.saveIntent && workshopState.saveIntent.outcome !== null"
        " && !workshopState.isSaving && !workshopState.isFinalizing"
    )
    assert page.evaluate("workshopState.saveIntent.outcome") == "saved"
    expect(page.locator("#workshop-save-state")).to_have_text("Ready")


def _scroll_settled(page):
    """Wait until the page stops scrolling: core.css scrolls smoothly, so a focus scroll animates."""
    page.evaluate(
        """() => new Promise(resolve => {
            let last = -1, still = 0;
            const tick = () => {
                still = window.scrollY === last ? still + 1 : 0;
                last = window.scrollY;
                if (still >= 10) resolve();
                else requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
        })"""
    )


def _scroll_to(page, where):
    """Jump the page to its top or its end, without the smooth animation."""
    top = "0" if where == "top" else "document.documentElement.scrollHeight"
    page.evaluate(f"window.scrollTo({{top: {top}, behavior: 'instant'}})")
    _scroll_settled(page)


def _clear_of_footer(page, control):
    """The control is on screen and above the sticky draft footer, which is on screen too.

    A focus scroll lines the control's box up with the space reserved for the footer, but scroll offsets are whole
    pixels and layout positions are not: the box can land up to a pixel lower (0.27px into the footer at 375px)."""
    box = control.bounding_box()
    footer = page.locator("#workshop-draft-footer").bounding_box()
    height = page.viewport_size["height"]
    assert footer["y"] < height and footer["y"] + footer["height"] <= height + 1, footer
    assert box["y"] >= 0 and box["y"] + box["height"] < footer["y"] + 1, (box, footer)


@pytest.mark.parametrize("width,height", SIZES)
def test_morning_mix_previews_keeps_options_adds_a_track_and_saves_once_after_review(
    fake, open_session, width, height
):
    """Preview in one activation, options kept, a track added from the second results page, Inbox empty, then a
    full review: Back writes nothing, Save to Spotify sends one commit that leaves Spotify equal to the draft."""
    unchanged = fake.playlist(MORNING_MIX)
    before = unchanged["uris"]
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    assert _uris(page) == before
    expect(page.locator("#workshop-save-state")).to_have_text("Ready")

    # One activation reorders the visible list, with the current options: nothing to open first.
    _preview(page)
    previewed = _uris(page)
    assert previewed != before and Counter(previewed) == Counter(before)
    assert previewed == _working(page)
    assert len(s.writes("POST", f"/workshop/{MORNING_MIX}/preview-shuffle")) == 1

    # Shuffle options keep a changed value through a close and a reopening.
    _open_tool(page, "options")
    assert page.locator("#workshop-algorithm").input_value() == "BasicShuffle"
    keep_first = page.locator("#workshop-param-keep_first")
    assert keep_first.input_value() == "0"
    keep_first.fill("3")
    _close_tool(page, "options")
    _open_tool(page, "options")
    assert keep_first.input_value() == "3"
    _close_tool(page, "options")

    # Add tracks: the first page has only tracks already in the playlist; Load more reads the next page.
    _open_tool(page, "add")
    page.fill("#search-input", QUERY)
    page.press("#search-input", "Enter")
    results = page.locator("#search-results .add-track-btn")
    expect(results).to_have_count(10)
    assert results.evaluate_all("buttons => buttons.every(b => b.getAttribute('aria-disabled') === 'true')")
    page.click("#search-load-more")
    expect(results).to_have_count(20)
    assert [json.loads(body)["offset"] for body in s.writes("POST", "/workshop/search")] == [0, 10]
    found = results.evaluate_all("buttons => buttons.map(b => b.closest('[data-uri]').dataset.uri)")
    assert len(set(found)) == 20 and set(found[:10]) <= set(before) and not set(found[10:]) & set(before)
    add, added = results.nth(10), found[10]
    assert add.text_content().startswith("Add to draft")
    add.focus()
    page.keyboard.press("Enter")
    expect(add).to_have_attribute("aria-disabled", "true")
    assert add.text_content().startswith("In playlist")
    assert add.evaluate("button => button === document.activeElement")
    expect(_rows(page)).to_have_count(41)
    assert _working(page) == _uris(page) == previewed + [added]
    _close_tool(page, "add")

    # Inbox: nothing collected yet, so it says so.
    _open_inbox(page)
    page.wait_for_function("() => pendingRaidsState.loaded && !pendingRaidsState.isLoading")
    expect(page.locator("#inbox-raids-empty")).to_be_visible()
    expect(page.locator("#inbox-raids-list > *")).to_have_count(0)
    _close_tool(page, "inbox")

    # Nothing has reached Spotify, and nothing was sent to save.
    draft = _working(page)
    assert fake.playlist(MORNING_MIX) == unchanged and s.writes("POST", _commit(MORNING_MIX)) == []

    summary = _review(page)
    assert summary == (
        "Morning Mix: 1 added, 0 removed (counting repeated tracks). Order changed. Final size: 41."
    )
    page.click("#workshop-review-back")
    page.locator("#workshop-save-panel").wait_for(state="hidden")
    assert fake.playlist(MORNING_MIX) == unchanged and s.writes("POST", _commit(MORNING_MIX)) == []
    assert _working(page) == draft

    _review(page)
    _save_to_spotify(page)
    assert fake.uris(MORNING_MIX) == draft
    commits = s.writes("POST", _commit(MORNING_MIX))
    assert len(commits) == 1 and json.loads(commits[0])["track_uris"] == draft
    assert _no_sideways_scroll(page)
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_on_repeat_moves_and_removes_one_occurrence_by_keyboard(fake, open_session, width, height):
    """Row actions move one copy of a repeated song, keeping its key and focus, and remove exactly one copy; the
    review counts it, and the save leaves Spotify with one copy fewer, in the draft order."""
    unchanged = fake.playlist(ON_REPEAT)
    before = unchanged["uris"]
    assert len(before) == 10 and set(Counter(before).values()) == {2}
    s = open_session(width, height)
    page = s.page
    page.goto(f"{APP}/workshop/{ON_REPEAT}", wait_until="networkidle")
    announcer = page.locator("#live-operation-announcer")
    rows = _rows(page)
    start = _shown(page)
    assert [uri for _, uri in start] == before and len({key for key, _ in start}) == 10
    key, uri = start[1]
    title = page.evaluate("uri => trackDataByUri[uri].name", uri)

    # The second row's Row actions open on Move up; Tab reaches Move down.
    rows.nth(1).locator(".track-actions-btn").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#track-actions")).to_be_visible()
    assert _focus(page) == ["up", key]
    page.keyboard.press("Tab")
    assert _focus(page) == ["down", key]
    page.keyboard.press("Enter")
    # The same occurrence moved: its key goes with it, and focus stays on Move down in its row.
    assert _shown(page) == [start[0], start[2], [key, uri]] + start[3:]
    assert _focus(page) == ["down", key]
    # The move is said first. This first change also makes the page a Draft, which the same announcement says
    # after it (one announcement per task).
    expect(announcer).to_have_text(re.compile(rf"^Moved {re.escape(title)} to position 3\. Draft\b"))

    # Move to position: the number field, then Enter.
    page.keyboard.press("Tab")
    assert _focus(page) == ["position", key]
    position = page.locator("#track-actions-position")
    position.fill("1")
    page.keyboard.press("Enter")
    assert _shown(page) == [[key, uri], start[0], start[2]] + start[3:]
    assert _focus(page) == ["position", key]
    expect(announcer).to_have_text(f"Moved {title} to position 1.")

    # Remove one copy of another repeated song: its other copy, and every other row, stay.
    shown = _shown(page)
    gone_key, gone_uri = shown[8]
    twin = next(k for k, u in shown if u == gone_uri and k != gone_key)
    rows.nth(8).locator(".track-actions-btn").focus()
    page.keyboard.press("Enter")
    assert _focus(page) == ["up", gone_key]
    page.locator('#track-actions [data-row-action="remove"]').focus()
    page.keyboard.press("Enter")
    expect(rows).to_have_count(9)
    remaining = _shown(page)
    assert remaining == shown[:8] + shown[9:]
    assert [twin, gone_uri] in remaining
    # Focus moves on to the next row's Row actions button.
    assert _focus(page) == ["actions", shown[9][0]]
    draft = [u for _, u in remaining]
    assert _working(page) == draft and Counter(draft)[gone_uri] == 1
    assert fake.playlist(ON_REPEAT) == unchanged and s.writes("POST", _commit(ON_REPEAT)) == []

    assert _review(page) == (
        "On Repeat: 0 added, 1 removed (counting repeated tracks). Order changed. Final size: 9."
    )
    _save_to_spotify(page)
    saved = fake.uris(ON_REPEAT)
    assert saved == draft
    assert Counter(saved)[gone_uri] == Counter(before)[gone_uri] - 1 == 1
    assert len(s.writes("POST", _commit(ON_REPEAT))) == 1
    s.assert_clean()


def test_long_haul_scrolls_with_the_page_and_its_last_row_clears_the_footer(fake, open_session):
    """At a phone width the 250-track list is not a nested scroller, and the last rows' Row actions buttons are
    never under the sticky draft footer, with or without a draft. Review & save from deep in the list covers it;
    leaving the review brings the list back at the same place, with focus on Review & save again, and after a
    save, on the draft controls."""
    before = fake.uris(LONG_HAUL)
    s = open_session(375, 812)
    page = s.page
    page.goto(f"{APP}/workshop/{LONG_HAUL}", wait_until="networkidle")
    rows = _rows(page)
    expect(rows).to_have_count(250)
    for selector in ("#track-list", "#workshop-track-section"):
        scroll, client = page.eval_on_selector(selector, "el => [el.scrollHeight, el.clientHeight]")
        assert scroll <= client, f"{selector} scrolls on its own: {scroll} > {client}"
    footer = page.locator("#workshop-draft-footer")
    buttons = rows.locator(".track-actions-btn")

    def last_rows_clear_the_footer():
        # From the top, focus walks the last rows by keyboard; then the page scrolls to its end.
        _scroll_to(page, "top")
        buttons.nth(239).focus()
        _scroll_settled(page)
        for index in range(240, 250):
            page.keyboard.press("Tab")
            _scroll_settled(page)
            assert buttons.nth(index).evaluate("button => button === document.activeElement")
            _clear_of_footer(page, buttons.nth(index))
        _scroll_to(page, "end")
        buttons.last.focus()
        _scroll_settled(page)
        _clear_of_footer(page, buttons.last)

    expect(footer).to_be_visible()
    expect(footer).to_contain_text("Ready")
    last_rows_clear_the_footer()

    _preview(page)
    expect(footer).to_contain_text("Draft")
    expect(page.locator("#undo-preview-btn")).to_be_visible()
    assert _no_sideways_scroll(page)
    last_rows_clear_the_footer()
    assert _no_sideways_scroll(page)

    # Review & save by keyboard from row 201, then Back to Workshop or Escape: the list is where it was.
    save = page.locator("#save-btn")
    for leave in ("back", "escape"):
        _scroll_to(page, "top")
        buttons.nth(200).focus()
        _scroll_settled(page)
        assert page.evaluate("window.scrollY") > 5000
        place = buttons.nth(200).bounding_box()["y"]
        # Focus inside the sticky footer is on screen already, so moving it there leaves the list still.
        save.focus()
        _scroll_settled(page)
        assert abs(buttons.nth(200).bounding_box()["y"] - place) <= 1, (leave, place)
        page.keyboard.press("Enter")
        page.locator("#workshop-save-panel").wait_for(state="visible")
        expect(page.locator("#workshop-surface")).to_be_hidden()
        if leave == "back":
            page.locator("#workshop-review-back").focus()
            page.keyboard.press("Enter")
        else:
            page.keyboard.press("Escape")
        page.locator("#workshop-save-panel").wait_for(state="hidden")
        _scroll_settled(page)
        expect(save).to_be_focused()
        # Row 201 is where it was on screen.
        now = buttons.nth(200).bounding_box()["y"]
        assert abs(now - place) <= 1, (leave, place, now)
        _clear_of_footer(page, buttons.nth(200))
    assert s.writes("POST", _commit(LONG_HAUL)) == [] and fake.uris(LONG_HAUL) == before

    # Saving from row 201 and closing the results keeps the place too. With nothing left to save,
    # Review & save is unavailable, so focus goes to the draft controls around it.
    draft = _working(page)
    _scroll_to(page, "top")
    buttons.nth(200).focus()
    _scroll_settled(page)
    place = buttons.nth(200).bounding_box()["y"]
    save.focus()
    page.keyboard.press("Enter")
    page.locator("#workshop-save-panel").wait_for(state="visible")
    _save_to_spotify(page)
    page.locator("#workshop-review-back").focus()
    page.keyboard.press("Enter")
    page.locator("#workshop-save-panel").wait_for(state="hidden")
    _scroll_settled(page)
    expect(page.locator("#workshop-draft-footer")).to_be_focused()
    now = buttons.nth(200).bounding_box()["y"]
    assert abs(now - place) <= 1, ("saved", place, now)
    assert len(s.writes("POST", _commit(LONG_HAUL))) == 1 and fake.uris(LONG_HAUL) == draft
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_setup_deep_links_open_their_task_and_return_focus_to_its_tool(fake, open_session, width, height):
    """?setup=raids opens Source setup and ?setup=archive Archive setup, each as a full-width task; closing it
    returns focus to its tool's button, or to Playlist tools where Archive is listed there. Opening them only
    reads: nothing is sent."""
    before = fake.playlists()
    s = open_session(width, height)
    page = s.page
    sent = len(s.sent)
    archive_trigger = "workshop-tool-archive" if width >= WORKSPACE_WIDTH else "workshop-tools-btn"
    links = (
        ("raids", "sources", "raidPanel.loaded", "workshop-tool-inbox"),
        ("archive", "archive-setup", "archiveState.isLoaded", archive_trigger),
    )
    for setup, tool, loaded, trigger in links:
        page.goto(f"{WORKSHOP}?setup={setup}", wait_until="networkidle")
        task = page.locator(f"#workshop-{tool}-panel")
        expect(task).to_be_visible()
        page.wait_for_function(f"() => {loaded}")
        assert task.get_attribute("data-panel-full") is not None
        expect(page.locator("#workshop-surface")).to_be_hidden()
        _close_tool(page, tool)
        expect(page.locator("#workshop-surface")).to_be_visible()
        assert page.evaluate("document.activeElement.id") == trigger, setup
    assert s.sent[sent:] == [] and fake.playlists() == before
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_a_setup_step_closed_from_its_end_returns_to_its_tool_with_the_opener_in_view(
    fake, open_session, width, height
):
    """Archive setup scrolls with the page; Escape from its end brings Archive back with focus on Archive setup,
    and that button on screen. (A parent taller than the screen, where only the restored scroll keeps the button
    in view, is covered by tests/test_app_panel_runtime.py: no seeded tool is that tall.)"""
    before = fake.playlists()
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    _open_tool(page, "archive")
    page.wait_for_function("() => archiveState.isLoaded")
    opener = page.locator("#workshop-archive-setup-btn")
    opener.focus()
    _scroll_settled(page)
    parent_scroll = page.evaluate("window.scrollY")
    page.keyboard.press("Enter")
    setup = page.locator("#workshop-archive-setup-panel")
    expect(setup).to_be_visible()
    # The archive setup is short, so give this browser case a long step: closing from its end
    # still exercises restoration after the document has actually scrolled at every viewport.
    setup.evaluate("panel => { panel.style.minHeight = `${innerHeight + 640}px`; }")
    _scroll_to(page, "end")
    assert page.evaluate("window.scrollY") > 0
    page.keyboard.press("Escape")
    expect(setup).to_be_hidden()
    expect(page.locator("#workshop-archive-panel")).to_be_visible()
    _scroll_settled(page)
    expect(opener).to_be_focused()
    assert page.evaluate("window.scrollY") == parent_scroll
    box = opener.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= page.viewport_size["height"], box
    assert s.sent == [] and fake.playlists() == before
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_saved_draft_saves_loads_over_a_draft_after_asking_and_deletes_without_writing_spotify(
    alice, fake, open_session, width, height
):
    """A named draft keeps the order shown; loading it over a changed order asks first, then shows its order;
    deleting it asks too. Spotify is never written."""
    before = fake.playlist(MORNING_MIX)
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    # An order that differs from Spotify's, so loading it cannot be mistaken for a discard.
    _preview(page)
    kept = _working(page)

    _open_tool(page, "history")
    page.wait_for_function("() => savedDraftsState.isLoaded")
    drafts = page.locator("#saved-drafts-list > li")
    expect(page.locator("#saved-drafts-empty")).to_be_visible()
    page.fill("#saved-draft-name", DRAFT_NAME)
    page.click("#saved-draft-save-btn")
    expect(drafts).to_have_count(1)
    expect(drafts.first).to_contain_text(DRAFT_NAME)
    saved = alice.ok("get", f"/workshop/{MORNING_MIX}/sessions")["sessions"]
    assert [(draft["session_name"], draft["track_uris"]) for draft in saved] == [(DRAFT_NAME, kept)]

    # Change the order with Row actions. Below the workspace width History covers the list, so it closes first.
    if width < WORKSPACE_WIDTH:
        _close_tool(page, "history")
    _open_row_actions(page, _rows(page).first)
    page.click('#track-actions [data-row-action="down"]')
    changed = [kept[1], kept[0]] + kept[2:]
    assert _working(page) == _uris(page) == changed

    _open_tool(page, "history")
    drafts.first.locator('[data-action-click="wk-draft-load"]').click()
    confirm = page.locator("#app-confirm")
    expect(confirm).to_be_visible()
    expect(page.locator("#app-confirm-heading")).to_have_text(f"Discard your draft changes and load {DRAFT_NAME}?")
    expect(page.locator("#app-confirm-ok")).to_have_text("Discard and load")
    page.click("#app-confirm-ok")
    expect(confirm).to_be_hidden()
    page.wait_for_function("uris => JSON.stringify(workshopState.workingUris) === JSON.stringify(uris)", arg=kept)
    assert _uris(page) == kept
    expect(page.locator("#workshop-save-state")).to_have_text("Draft")

    drafts.first.locator('[data-action-click="wk-draft-delete"]').click()
    expect(confirm).to_be_visible()
    expect(page.locator("#app-confirm-heading")).to_have_text(f'Delete "{DRAFT_NAME}"?')
    page.click("#app-confirm-ok")
    expect(drafts).to_have_count(0)
    expect(page.locator("#saved-drafts-empty")).to_be_visible()
    assert alice.ok("get", f"/workshop/{MORNING_MIX}/sessions")["sessions"] == []

    assert fake.playlist(MORNING_MIX) == before
    assert s.writes("POST", _commit(MORNING_MIX)) == []
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_snapshot_view_lists_its_tracks_and_closes_without_writing(alice, fake, open_session, width, height):
    """A manual snapshot's View lists its tracks in order by title; a second press closes the list. Only the
    snapshot itself is saved, in Shuffify; Spotify does not change."""
    before = fake.playlist(MORNING_MIX)
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    sent = len(s.sent)

    _open_tool(page, "history")
    page.wait_for_function("() => snapshotState.isLoaded")
    expect(page.locator("#snapshot-empty")).to_be_visible()
    page.click("#snapshot-take-btn")
    # With no draft it saves the playlist's own order.
    save = page.locator("#snapshot-save-btn")
    expect(save).to_have_text("Save snapshot")
    save.click()
    cards = page.locator("#snapshot-list .snapshot-card")
    expect(cards).to_have_count(1)
    (snapshot,) = alice.ok("get", f"/playlist/{MORNING_MIX}/snapshots")["snapshots"]
    assert alice.ok("get", f"/snapshots/{snapshot['id']}")["snapshot"]["track_uris"] == before["uris"]

    view = cards.first.locator('[data-action-click="wk-snapshot-view"]')
    expect(view).to_have_attribute("aria-expanded", "false")
    view.click()
    listed = page.locator(f"#snapshot-view-{snapshot['id']} li")
    expect(listed).to_have_count(40)
    titles = page.evaluate("uris => uris.map(uri => trackDataByUri[uri].name)", before["uris"])
    assert [re.sub(r"^\d+\.\s*", "", text) for text in listed.all_inner_texts()] == titles
    expect(view).to_have_attribute("aria-expanded", "true")

    view.click()
    expect(page.locator(f"#snapshot-view-{snapshot['id']}")).to_be_hidden()
    expect(view).to_have_attribute("aria-expanded", "false")

    assert [(method, path) for method, path, _ in s.sent[sent:]] == [("POST", f"/playlist/{MORNING_MIX}/snapshots")]
    assert fake.playlist(MORNING_MIX) == before
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_leaving_a_previewed_draft_offers_review_discard_or_stay(fake, open_session, width, height):
    """Following Settings with a draft asks first: Stay keeps the draft and the page; Discard draft changes and
    leave goes to Settings. Spotify does not change."""
    before = fake.playlist(MORNING_MIX)
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    _preview(page)
    draft = _working(page)
    leave = page.locator("#live-operation-leave")

    _nav_to_settings(page)
    expect(leave).to_be_visible()
    expect(page.locator("#live-operation-leave-save")).to_have_text("Review & save")
    expect(page.locator("#live-operation-leave-discard")).to_have_text("Discard draft changes and leave")
    expect(page.locator("#live-operation-leave-stay")).to_have_text("Stay")
    for offered in ("save", "discard", "stay"):
        expect(page.locator(f"#live-operation-leave-{offered}")).to_be_visible()
    for withheld in ("review", "confirm"):
        expect(page.locator(f"#live-operation-leave-{withheld}")).to_be_hidden()
    assert _no_sideways_scroll(page)

    page.click("#live-operation-leave-stay")
    expect(leave).to_be_hidden()
    assert page.url == WORKSHOP
    assert _working(page) == _uris(page) == draft
    expect(page.locator("#workshop-save-state")).to_have_text("Draft")

    _nav_to_settings(page)
    expect(leave).to_be_visible()
    page.click("#live-operation-leave-discard")
    page.wait_for_url(f"{APP}/settings")
    assert fake.playlist(MORNING_MIX) == before
    assert s.writes("POST", _commit(MORNING_MIX)) == []
    s.assert_clean()
