"""Real React Workshop drafts, keyboard actions and tools against the isolated Spotify provider."""

import json
import re
from collections import Counter
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, pid
from tests.e2e.test_compatibility_and_dialogs import SIZES
from tests.e2e.test_compatibility_and_dialogs import open_session as open_session
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.paths import api_path

MORNING_MIX = pid("alice-morning-mix")
ON_REPEAT = pid("alice-on-repeat")
LONG_HAUL = pid("alice-long-haul")
WORKSHOP = f"{APP}/workshop/{MORNING_MIX}"
QUERY = "signal"
DRAFT_NAME = "Weekday order"


def _commit(playlist_id):
    return api_path(f"/workshop/{playlist_id}/commit")


def _panel(page):
    return page.locator("section.sh-task-panel:not([hidden])")


def _rows(page):
    return page.locator("ol[aria-label='Draft tracks'] > [data-occurrence]")


def _shown(page):
    """Read actual occurrence identities and Spotify links from the rendered draft."""
    return _rows(page).evaluate_all("""rows => rows.map(row => [row.dataset.occurrence,
        'spotify:track:' + new URL(row.querySelector('a[href*="open.spotify.com/track/"]').href)
            .pathname.split('/').at(-1)])""")


def _uris(page):
    return [uri for _, uri in _shown(page)]


def _no_sideways_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def _open_tool(page, name):
    name = {
        "options": "Shuffle options",
        "add": "Add tracks",
        "inbox": "Inbox",
        "archive": "Archive",
        "history": "Snapshots",
    }.get(name, name)
    button = page.get_by_role("button", name=name, exact=True)
    if not button.is_visible():
        page.get_by_role("button", name="Tools", exact=True).click()
    button.click()
    expect(_panel(page).get_by_role("heading", name=name, exact=True)).to_be_visible()


def _close_tool(page, name=None, *, one=False):
    while _panel(page).count():
        _panel(page).get_by_role("button", name="Close", exact=True).click()
        if one:
            break


def _open_inbox(page):
    _open_tool(page, "Inbox")


def _remove_row(page, index=0):
    row = _rows(page).nth(index)
    row.get_by_role("button", name=re.compile("^Actions for ")).click()
    row.get_by_role("button", name="Remove from draft", exact=True).click()


def _preview(page):
    _open_tool(page, "Shuffle options")
    with page.expect_response(lambda response: urlsplit(response.url).path.endswith("/preview-shuffle")):
        _panel(page).get_by_role("button", name="Preview shuffle", exact=True).click()
    expect(page.get_by_text("Draft changes are local until you review and save.", exact=True).first).to_be_attached()
    _close_tool(page)


def _review(page):
    page.get_by_role("button", name="Review & save", exact=True).click()
    expect(_panel(page).get_by_role("heading", name="Review save", exact=True)).to_be_visible()
    expect(_panel(page).get_by_role("button", name="Save to Spotify", exact=True)).to_be_visible()
    return _panel(page).inner_text()


def _save_to_spotify(page, playlist_id=MORNING_MIX):
    with page.expect_response(
        lambda response: urlsplit(response.url).path == _commit(playlist_id) and response.request.method == "POST"
    ) as pending:
        _panel(page).get_by_role("button", name="Save to Spotify", exact=True).dblclick()
    response = pending.value
    assert response.status == 202, response.text()
    accepted = response.json()
    assert accepted["operation"]["id"] == accepted["operation_id"]
    expect(
        page.get_by_role("region", name="Retained operation").get_by_text("Playlist saved to Spotify!", exact=True)
    ).to_be_visible(timeout=45000)
    return accepted


def _scroll_settled(page):
    page.evaluate("""() => new Promise(resolve => {
        let last = -1, still = 0;
        const tick = () => { still = window.scrollY === last ? still + 1 : 0; last = window.scrollY;
            if (still >= 10) resolve(); else requestAnimationFrame(tick); };
        requestAnimationFrame(tick);
    })""")


def _scroll_to(page, where):
    page.evaluate(
        """where => window.scrollTo({
        top: where === 'top' ? 0 : document.documentElement.scrollHeight,
        behavior: 'instant'})""",
        where,
    )
    _scroll_settled(page)


def _clear_of_footer(page, control):
    box = control.bounding_box()
    footer = page.locator(".sh-workshop-draft-footer").bounding_box()
    assert 0 <= footer["y"] < page.viewport_size["height"]
    assert 0 <= box["y"] and box["y"] + box["height"] <= footer["y"] + 1, (box, footer)


@pytest.mark.parametrize("width,height", SIZES)
def test_morning_mix_previews_keeps_options_adds_a_track_and_saves_once_after_review(fake, open_session, width, height):
    unchanged = fake.playlist(MORNING_MIX)
    before = unchanged["uris"]
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    assert _uris(page) == before
    _preview(page)
    previewed = _uris(page)
    assert previewed != before and Counter(previewed) == Counter(before)
    assert len(s.writes("POST", api_path(f"/workshop/{MORNING_MIX}/preview-shuffle"))) == 1
    _open_tool(page, "Shuffle options")
    expect(page.get_by_label("Shuffle algorithm", exact=True)).to_have_value("BasicShuffle")
    page.get_by_label("Number of tracks to keep at start", exact=True).fill("3")
    _close_tool(page)
    _open_tool(page, "Shuffle options")
    expect(page.get_by_label("Number of tracks to keep at start", exact=True)).to_have_value("3")
    _close_tool(page)

    _open_tool(page, "Add tracks")
    page.get_by_role("searchbox", name="Track, artist or album", exact=True).fill(QUERY)
    page.get_by_role("button", name="Search tracks", exact=True).click()
    results = _panel(page).locator(".sh-tools-track")
    expect(results).to_have_count(10)
    expect(results.get_by_role("button", name="Already in draft", exact=True)).to_have_count(10)
    page.get_by_role("button", name="Load more tracks", exact=True).click()
    expect(results).to_have_count(20)
    assert [json.loads(body)["offset"] for body in s.writes("POST", api_path("/workshop/search"))] == [0, 10]
    result = results.nth(10)
    added = (
        "spotify:track:"
        + urlsplit(result.locator('a[href*="open.spotify.com/track/"]').first.get_attribute("href")).path.split("/")[-1]
    )
    button = result.get_by_role("button", name="Add to draft", exact=True)
    button.focus()
    page.keyboard.press("Enter")
    expect(result.get_by_role("button", name="Already in draft", exact=True)).to_have_attribute("aria-disabled", "true")
    assert _uris(page) == previewed + [added]
    _close_tool(page)
    _open_inbox(page)
    expect(_panel(page).get_by_text("No pending tracks.", exact=False)).to_be_visible()
    _close_tool(page)
    draft = _uris(page)
    assert fake.playlist(MORNING_MIX) == unchanged and s.writes("POST", _commit(MORNING_MIX)) == []
    summary = _review(page)
    assert "Added occurrences" in summary and "Reviewed desired order" in summary
    _close_tool(page)
    assert _uris(page) == draft and fake.playlist(MORNING_MIX) == unchanged
    _review(page)
    _save_to_spotify(page)
    assert fake.uris(MORNING_MIX) == draft
    commits = s.writes("POST", _commit(MORNING_MIX))
    assert len(commits) == 1 and json.loads(commits[0])["track_uris"] == draft
    assert _no_sideways_scroll(page)
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_on_repeat_moves_and_removes_one_occurrence_by_keyboard(fake, open_session, width, height):
    unchanged = fake.playlist(ON_REPEAT)
    before = unchanged["uris"]
    assert len(before) == 10 and set(Counter(before).values()) == {2}
    s = open_session(width, height)
    page = s.page
    page.goto(f"{APP}/workshop/{ON_REPEAT}", wait_until="networkidle")
    start = _shown(page)
    key, uri = start[1]
    row = _rows(page).nth(1)
    row.get_by_role("button", name=re.compile("^Actions for ")).focus()
    page.keyboard.press("Enter")
    expect(row.get_by_role("button", name="Move up", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(row.get_by_role("button", name="Move down", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    assert _shown(page)[2] == [key, uri]
    row = page.locator(f'[data-occurrence="{key}"]')
    row.get_by_label("Move to position", exact=True).fill("1")
    row.get_by_role("button", name="Move", exact=True).press("Enter")
    assert _shown(page)[0] == [key, uri]
    row.get_by_role("button", name="Remove from draft", exact=True).click()
    expect(_rows(page)).to_have_count(9)
    assert key not in [value[0] for value in _shown(page)]
    assert Counter(_uris(page)) == Counter(before) - Counter([uri])
    draft = _uris(page)
    assert fake.playlist(ON_REPEAT) == unchanged
    _review(page)
    _save_to_spotify(page, ON_REPEAT)
    assert fake.uris(ON_REPEAT) == draft
    assert len(s.writes("POST", _commit(ON_REPEAT))) == 1
    s.assert_clean()


def test_long_haul_scrolls_with_the_page_and_its_last_row_clears_the_footer(fake, open_session):
    s = open_session(375, 812)
    page = s.page
    page.goto(f"{APP}/workshop/{LONG_HAUL}", wait_until="networkidle")
    expect(_rows(page)).to_have_count(250)
    button = _rows(page).last.get_by_role("button", name=re.compile("^Actions for "))
    button.scroll_into_view_if_needed()
    _scroll_settled(page)
    _clear_of_footer(page, button)
    assert _no_sideways_scroll(page)
    unchanged = fake.playlist(LONG_HAUL)
    _remove_row(page, 249)
    draft = _uris(page)
    assert fake.playlist(LONG_HAUL) == unchanged
    _review(page)
    _close_tool(page)
    expect(page.get_by_role("button", name="Review & save", exact=True)).to_be_focused()
    expect(_rows(page)).to_have_count(249)
    _review(page)
    _save_to_spotify(page, LONG_HAUL)
    assert fake.uris(LONG_HAUL) == draft
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
@pytest.mark.parametrize("kind,title", [("raids", "Source setup"), ("archive", "Archive setup")])
def test_setup_deep_links_open_their_task_and_return_focus_to_its_tool(fake, open_session, width, height, kind, title):
    unchanged = fake.playlist(MORNING_MIX)
    s = open_session(width, height)
    page = s.page
    page.goto(f"{WORKSHOP}?setup={kind}", wait_until="networkidle")
    expect(_panel(page).get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Tools", exact=True)).to_be_focused()
    assert s.sent == [] and fake.playlist(MORNING_MIX) == unchanged
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_a_setup_step_closed_from_its_end_returns_to_its_tool_with_the_opener_in_view(
    fake, open_session, width, height
):
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    _open_tool(page, "Archive")
    _panel(page).get_by_role("button", name="Archive setup", exact=True).click()
    expect(_panel(page).get_by_role("heading", name="Archive setup", exact=True)).to_be_visible()
    _panel(page).get_by_role("button", name="Refresh owned playlists", exact=True).scroll_into_view_if_needed()
    page.keyboard.press("Escape")
    expect(_panel(page).get_by_role("heading", name="Archive", exact=True)).to_be_visible()
    expect(_panel(page).get_by_role("button", name="Archive setup", exact=True)).to_be_focused()
    assert s.sent == []
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_saved_draft_saves_loads_over_a_draft_after_asking_and_deletes_without_writing_spotify(
    fake, open_session, width, height
):
    unchanged = fake.playlist(MORNING_MIX)
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    _remove_row(page)
    saved = _uris(page)
    page.get_by_role("button", name="Named drafts", exact=True).click()
    page.get_by_label("Draft name", exact=True).fill(DRAFT_NAME)
    page.get_by_role("button", name="Save named draft", exact=True).click()
    expect(_panel(page).get_by_role("heading", name=DRAFT_NAME, exact=True)).to_be_visible()
    _close_tool(page)
    _remove_row(page)
    page.get_by_role("button", name="Named drafts", exact=True).click()
    page.get_by_role("button", name=f"Load {DRAFT_NAME}", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("Load saved draft?")
    dialog.get_by_role("button", name="Load saved draft", exact=True).click()
    expect(_rows(page)).to_have_count(len(saved))
    assert _uris(page) == saved
    page.get_by_role("button", name=f"Delete {DRAFT_NAME}", exact=True).click()
    page.get_by_role("dialog").get_by_role("button", name="Delete draft", exact=True).click()
    expect(_panel(page).get_by_role("heading", name=DRAFT_NAME, exact=True)).to_have_count(0)
    assert s.writes("POST", _commit(MORNING_MIX)) == [] and fake.playlist(MORNING_MIX) == unchanged
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_snapshot_view_lists_its_tracks_and_closes_without_writing(fake, open_session, width, height):
    unchanged = fake.playlist(MORNING_MIX)
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    _open_tool(page, "Snapshots")
    page.get_by_role("button", name="Take snapshot", exact=True).click()
    page.get_by_label("Snapshot description", exact=True).fill("Original Morning Mix")
    page.get_by_role("button", name="Save snapshot", exact=True).click()
    card = _panel(page).locator("[data-snapshot-id]").first
    card.get_by_role("button", name="View tracks", exact=True).click()
    expect(card.locator(".sh-tools-snapshot-tracks li")).to_have_count(len(unchanged["uris"]))
    actual = card.locator(".sh-tools-snapshot-tracks li").all_text_contents()
    assert len(actual) == 40 and all(text.strip() for text in actual)
    card.get_by_role("button", name="Hide tracks", exact=True).click()
    expect(card.locator(".sh-tools-snapshot-tracks")).to_be_hidden()
    expect(card.get_by_role("button", name="View tracks", exact=True)).to_have_attribute("aria-expanded", "false")
    assert fake.playlist(MORNING_MIX) == unchanged and s.writes("POST", _commit(MORNING_MIX)) == []
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_leaving_a_previewed_draft_offers_review_discard_or_stay(fake, open_session, width, height):
    unchanged = fake.playlist(MORNING_MIX)
    s = open_session(width, height)
    page = s.page
    page.goto(WORKSHOP, wait_until="networkidle")
    _preview(page)
    draft = _uris(page)
    for _ in range(2):
        if page.locator(".sh-app-menu").is_visible() and page.locator(".sh-app-menu").get_attribute("open") is None:
            page.locator(".sh-app-menu summary").click()
        page.get_by_role("link", name="Settings", exact=True).click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_role("button", name="Review & save", exact=True)).to_be_visible()
        if _ == 0:
            dialog.get_by_role("button", name="Stay", exact=True).click()
            assert _uris(page) == draft
        else:
            dialog.get_by_role("button", name="Discard", exact=True).click()
    expect(page).to_have_url(re.compile(r"/settings$"))
    assert fake.playlist(MORNING_MIX) == unchanged and s.writes("POST", _commit(MORNING_MIX)) == []
    s.assert_clean()
