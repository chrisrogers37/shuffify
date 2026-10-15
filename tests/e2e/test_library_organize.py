"""Keyboard Organize on the Library, in a real browser.

Organize shows each row's preference controls. Move up, Move down and Move to position share one path: the row takes
its new place in its own list, every row shows its place again, focus stays on the control that moved it, the move is
announced once, and the order is saved once (Favorites, then All playlists; Hidden keeps no order). Favorite saves
through its own route and moves the row between lists without a reload. Reset asks the shared confirmation first:
Escape changes nothing, and confirming reloads the Library in Spotify's order with no favorites.

Each test drives the app against the isolated fake Spotify at a desktop, a tablet and a phone width. It checks
what the server kept (the order it renders on a reload, the list it places each row in) as well as what the page shows,
and fails on a native dialog, a console error or any failed request.
"""

import json

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP
from tests.e2e.test_compatibility_and_dialogs import SIZES, _sections
from tests.e2e.test_compatibility_and_dialogs import open_session as open_session
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.paths import api_path

ORDER = api_path("/api/playlist-preferences/order")
ALL, FAVORITES = "#playlist-grid", "#favorites-grid"
# For each row of a list: Move up and Move down (aria-disabled at the ends) and the position field.
PLACES = """rows => rows.map(row => [
    row.querySelector('.library-move-up').getAttribute('aria-disabled'),
    row.querySelector('.library-move-down').getAttribute('aria-disabled'),
    row.querySelector('.library-position').value,
])"""
# Counts the "Order saved" toast. The page adds it once, when the last order save it has queued has finished.
WATCH_SAVES = """() => {
    window.__saved = 0;
    new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
        if (node.textContent === 'Order saved') window.__saved += 1;
    }))).observe(document.getElementById('flash-messages'), {childList: true});
}"""


def _ids(page, grid):
    """The playlist IDs one Library list shows, top to bottom."""
    return page.eval_on_selector_all(
        f"{grid} > .card-tile", "rows => rows.map(row => row.dataset.playlistId)"
    )


def _row(page, grid, playlist_id):
    return page.locator(f'{grid} > .card-tile[data-playlist-id="{playlist_id}"]')


def _places(count):
    """What a list of `count` rows shows of its places: the ends are marked, and each field reads its row's place."""
    return [
        ["true" if i == 0 else None, "true" if i == count - 1 else None, str(i + 1)]
        for i in range(count)
    ]


@pytest.mark.parametrize("width,height", SIZES)
def test_organize_moves_rows_by_their_controls_saves_each_order_once_and_favorites_in_place(
    alice, open_session, width, height
):
    """Move up and Move to position reorder All playlists and persist; Favorite moves a row to Favorites in place."""
    s = open_session(width, height)
    page = s.page
    announcer = page.locator("#live-operation-announcer")
    page.goto(f"{APP}/", wait_until="networkidle")
    page.evaluate(WATCH_SAVES)
    page.locator("#organize-btn").click()

    def saved_order(move):
        """Make one move; return the playlist_ids of the one save it started, once the page says it was saved."""
        saved, sent = page.evaluate("window.__saved"), len(s.writes("POST", ORDER))
        move()
        page.wait_for_function("n => window.__saved === n", arg=saved + 1)
        bodies = s.writes("POST", ORDER)
        assert len(bodies) == sent + 1
        return json.loads(bodies[-1])["playlist_ids"]

    # Move up: the second row takes the first place, its control keeps focus, and the move is announced and saved once.
    original = _ids(page, ALL)
    favorites = []
    assert len(original) >= 3 and _ids(page, FAVORITES) == favorites
    top, chosen, *rest = original
    row = _row(page, ALL, chosen)
    name = row.get_attribute("data-playlist-name")
    moved_up = [chosen, top, *rest]
    sent = saved_order(row.locator(".library-move-up").click)
    assert _ids(page, ALL) == moved_up
    assert sent == favorites + moved_up
    expect(row.locator(".library-move-up")).to_be_focused()
    expect(announcer).to_have_text(f"Moved {name} to position 1 in All playlists.")
    assert page.eval_on_selector_all(f"{ALL} > .card-tile", PLACES) == _places(
        len(original)
    )

    # Move to position: typing the last place sends the same row there, and that is saved once too.
    last = len(original)
    moved_last = [top, *rest, chosen]
    row.locator(".library-position").fill(str(last))
    sent = saved_order(row.locator(".library-move-to").click)
    assert _ids(page, ALL) == moved_last
    assert sent == favorites + moved_last
    expect(row.locator(".library-move-to")).to_be_focused()
    expect(announcer).to_have_text(f"Moved {name} to position {last} in All playlists.")
    assert page.eval_on_selector_all(f"{ALL} > .card-tile", PLACES) == _places(last)
    assert row.locator(".library-position").input_value() == str(last)
    assert len(s.writes("POST", ORDER)) == 2
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )

    # The server kept it: a fresh page renders the saved order, and so does another session.
    page.reload(wait_until="networkidle")
    assert _ids(page, ALL) == moved_last and _ids(page, FAVORITES) == favorites
    assert list(_sections(alice)) == moved_last

    # Favorite saves its own preference and moves the row in place: no reload, and no order save.
    page.evaluate(WATCH_SAVES)
    page.evaluate("window.__sameDocument = true")
    navigations = list(s.navigations)
    page.locator("#organize-btn").click()
    liked = moved_last[1]
    tile = page.locator(f'.card-tile[data-playlist-id="{liked}"]')
    tile.locator(".fav-btn").click()
    expect(_row(page, FAVORITES, liked)).to_have_count(1)
    regular = [playlist for playlist in moved_last if playlist != liked]
    assert _ids(page, FAVORITES) == [liked] and _ids(page, ALL) == regular
    expect(page.locator("#favorites-count")).to_have_text("1")
    expect(page.locator("#playlists-count")).to_have_text(str(len(regular)))
    assert page.locator("#favorites-section").is_visible()
    assert (
        tile.get_attribute("data-pinned") == "true"
        and tile.get_attribute("data-section") == "favorites"
    )
    favorite = tile.locator(".fav-btn")
    assert favorite.text_content().strip() == "Unfavorite"
    assert favorite.get_attribute("aria-label").startswith("Unfavorite: ")
    expect(favorite).to_be_focused()
    assert (
        len(s.writes("POST", api_path(f"/api/playlist-preferences/{liked}/toggle-pinned"))) == 1
    )
    assert len(s.writes("POST", ORDER)) == 2
    assert page.url == f"{APP}/" and s.navigations == navigations
    assert page.evaluate("window.__sameDocument") is True
    sections = _sections(alice)
    assert sections == {
        playlist: "favorites" if playlist == liked else "regular"
        for playlist in moved_last
    }
    assert list(sections) == [liked, *regular]

    # With a favorite, the order the next move saves lists the Favorites row first, then All playlists.
    end = regular[-1]
    swapped = [*regular[:-2], end, regular[-2]]
    sent = saved_order(_row(page, ALL, end).locator(".library-move-up").click)
    assert _ids(page, ALL) == swapped
    assert sent == [liked, *swapped]
    assert page.eval_on_selector_all(f"{FAVORITES} > .card-tile", PLACES) == _places(1)
    assert list(_sections(alice)) == [liked, *swapped]
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_keyboard_moves_persist_and_reset_returns_the_library_to_spotify_order(
    alice, open_session, width, height
):
    """Enter on a control or in the position field moves a row; Reset asks first, then restores Spotify's order."""
    s = open_session(width, height)
    page = s.page
    announcer = page.locator("#live-operation-announcer")
    page.goto(f"{APP}/", wait_until="networkidle")
    page.evaluate(WATCH_SAVES)
    original = _ids(page, ALL)
    assert len(original) >= 3 and _ids(page, FAVORITES) == []
    page.locator("#organize-btn").press("Enter")

    # Enter on Move down: the first row takes the second place, its control keeps focus, and it is saved once.
    first, second, *rest = original
    row = _row(page, ALL, first)
    name = row.get_attribute("data-playlist-name")
    row.locator(".library-move-down").focus()
    page.keyboard.press("Enter")
    page.wait_for_function("() => window.__saved === 1")
    moved_down = [second, first, *rest]
    assert _ids(page, ALL) == moved_down
    expect(row.locator(".library-move-down")).to_be_focused()
    expect(announcer).to_have_text(f"Moved {name} to position 2 in All playlists.")

    # Enter in the position field moves the row there, and focus stays in the field.
    last = len(original)
    field = row.locator(".library-position")
    field.fill(str(last))
    field.press("Enter")
    page.wait_for_function("() => window.__saved === 2")
    moved_last = [second, *rest, first]
    assert _ids(page, ALL) == moved_last
    expect(field).to_be_focused()
    expect(announcer).to_have_text(f"Moved {name} to position {last} in All playlists.")
    assert json.loads(s.writes("POST", ORDER)[-1])["playlist_ids"] == moved_last
    # A favorite too, so Reset has every kind of preference to clear.
    _row(page, ALL, second).locator(".fav-btn").press("Enter")
    expect(_row(page, FAVORITES, second)).to_have_count(1)
    assert list(_sections(alice)) == [second, *rest, first]

    # Reset asks first; Escape means no, returns focus to Reset and changes nothing.
    reset = page.locator('[data-action-click="dash-004"]')
    reset.press("Enter")
    dialog = page.locator("#app-confirm")
    expect(dialog).to_be_visible()
    expect(page.locator("#app-confirm-heading")).to_have_text("Reset Library preferences?")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(reset).to_be_focused()
    assert s.writes("POST", api_path("/api/playlist-preferences/reset")) == []
    assert _sections(alice)[second] == "favorites"

    # Confirming resets once and reloads: Spotify's order again, and no favorites, here and on the server.
    reset.press("Enter")
    expect(dialog).to_be_visible()
    with page.expect_navigation(wait_until="networkidle"):
        page.locator("#app-confirm-ok").click()
    assert len(s.writes("POST", api_path("/api/playlist-preferences/reset"))) == 1
    assert _ids(page, ALL) == original and _ids(page, FAVORITES) == []
    assert _sections(alice) == {playlist: "regular" for playlist in original}
    assert list(_sections(alice)) == original
    s.assert_clean()
