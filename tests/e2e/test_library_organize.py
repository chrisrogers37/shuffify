"""React Library order and preferences persisted through the real API."""

import json
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP
from tests.e2e.test_compatibility_and_dialogs import _sections
from tests.e2e.test_compatibility_and_dialogs import open_session as open_session
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.paths import api_path

SIZES = [(1440, 900), (768, 1024), (360, 800)]
ORDER = api_path("/api/playlist-preferences/order")
ALL = '.sh-library-list[data-section="playlists"]'
FAVORITES = '.sh-library-list[data-section="favorites"]'


def _ids(page, grid):
    return page.locator(f"{grid}>.sh-library-row").evaluate_all("rows => rows.map(row => row.dataset.playlistId)")


def _row(page, grid, playlist_id):
    return page.locator(f'{grid}>[data-playlist-id="{playlist_id}"]')


def _assert_positions(page, grid, count):
    actual = page.locator(f"{grid}>.sh-library-row").evaluate_all("""rows => rows.map(row => [
        row.querySelector('button[aria-label^="Drag"]') !== null,
        row.querySelector('input[type="number"]').value,
        Array.from(row.querySelectorAll('button')).find(b => b.textContent.startsWith('Move up'))
            .getAttribute('aria-disabled'),
        Array.from(row.querySelectorAll('button')).find(b => b.textContent.startsWith('Move down'))
            .getAttribute('aria-disabled'),
    ])""")
    assert actual == [
        [True, str(i + 1), "true" if i == 0 else "false", "true" if i == count - 1 else "false"] for i in range(count)
    ]


def _save(page, session, move):
    sent = len(session.writes("POST", ORDER))
    with page.expect_response(f"{APP}{ORDER}") as response:
        move()
    assert response.value.status == 200, response.value.text()
    expect(page.get_by_text("Order saved.", exact=True)).to_be_visible()
    bodies = session.writes("POST", ORDER)
    assert len(bodies) == sent + 1
    return json.loads(bodies[-1])["playlist_ids"]


@pytest.mark.parametrize("width,height", SIZES)
def test_organize_moves_rows_by_their_controls_saves_each_order_once_and_favorites_in_place(
    alice, open_session, width, height
):
    s = open_session(width, height)
    page = s.page
    page.goto(f"{APP}/library", wait_until="networkidle")
    page.get_by_role("button", name="Organize", exact=True).click()
    original = _ids(page, ALL)
    assert len(original) >= 3 and _ids(page, FAVORITES) == []
    top, chosen, *rest = original
    row = _row(page, ALL, chosen)
    name = row.get_by_role("heading").inner_text()
    up = row.get_by_role("button", name=re.compile(r"^Move up"))
    assert _save(page, s, up.click) == [chosen, top, *rest]
    assert _ids(page, ALL) == [chosen, top, *rest]
    expect(up).to_be_focused()
    expect(page.locator('[aria-live="polite"]')).to_have_text(f"{name} moved to position 1.")
    _assert_positions(page, ALL, len(original))

    row.get_by_role("spinbutton").fill(str(len(original)))
    move = row.get_by_role("button", name=f"Move {name}", exact=True)
    moved_last = [top, *rest, chosen]
    assert _save(page, s, move.click) == moved_last
    assert _ids(page, ALL) == moved_last
    expect(row.get_by_role("spinbutton")).to_be_focused()
    _assert_positions(page, ALL, len(original))
    page.reload(wait_until="networkidle")
    assert _ids(page, ALL) == moved_last
    assert list(_sections(alice)) == moved_last

    page.get_by_role("button", name="Organize", exact=True).click()
    page.evaluate("window.__sameDocument = true")
    navigations = list(s.navigations)
    liked = moved_last[1]
    tile = _row(page, ALL, liked)
    favorite = tile.get_by_role("button", name=re.compile(r"^Add favorite"))
    with page.expect_response(f"{APP}/api/playlist-preferences/{liked}/toggle-pinned") as response:
        favorite.click()
    assert response.value.status == 200
    expect(_row(page, FAVORITES, liked)).to_have_count(1)
    regular = [playlist for playlist in moved_last if playlist != liked]
    assert _ids(page, ALL) == regular
    expect(_row(page, FAVORITES, liked).get_by_role("button", name=re.compile(r"^Remove favorite"))).to_be_focused()
    assert page.evaluate("window.__sameDocument") is True
    assert s.navigations == navigations
    assert len(s.writes("POST", ORDER)) == 2
    assert _sections(alice) == {p: "favorites" if p == liked else "regular" for p in [liked, *regular]}

    end = regular[-1]
    swapped = [*regular[:-2], end, regular[-2]]
    assert _save(page, s, _row(page, ALL, end).get_by_role("button", name=re.compile(r"^Move up")).click) == [
        liked,
        *swapped,
    ]
    assert list(_sections(alice)) == [liked, *swapped]
    _assert_positions(page, FAVORITES, 1)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    s.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_keyboard_moves_persist_and_reset_returns_the_library_to_spotify_order(alice, open_session, width, height):
    s = open_session(width, height)
    page = s.page
    page.goto(f"{APP}/library", wait_until="networkidle")
    original = _ids(page, ALL)
    page.get_by_role("button", name="Organize", exact=True).press("Enter")
    first, second, *rest = original
    row = _row(page, ALL, first)
    down = row.get_by_role("button", name=re.compile(r"^Move down"))
    assert _save(page, s, lambda: down.press("Enter")) == [second, first, *rest]
    expect(down).to_be_focused()
    field = row.get_by_role("spinbutton")
    field.fill(str(len(original)))
    assert _save(page, s, lambda: field.press("Enter")) == [second, *rest, first]
    expect(field).to_be_focused()
    with page.expect_response(f"{APP}/api/playlist-preferences/{second}/toggle-pinned"):
        _row(page, ALL, second).get_by_role("button", name=re.compile(r"^Add favorite")).press("Enter")
    expect(_row(page, FAVORITES, second)).to_have_count(1)

    reset = page.get_by_role("button", name="Reset preferences", exact=True)
    reset.press("Enter")
    dialog = page.get_by_role("dialog", name="Reset Library preferences?")
    expect(dialog.get_by_role("heading", name="Reset Library preferences?")).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(reset).to_be_focused()
    assert s.writes("POST", api_path("/api/playlist-preferences/reset")) == []
    assert _sections(alice)[second] == "favorites"

    reset.press("Enter")
    with page.expect_response(f"{APP}/api/playlist-preferences/reset") as response:
        dialog.get_by_role("button", name="Reset preferences", exact=True).click()
    assert response.value.status == 200
    page.wait_for_function(
        "expected => JSON.stringify(Array.from(document.querySelectorAll("
        "'.sh-library-list[data-section=playlists]>.sh-library-row'))"
        ".map(row => row.dataset.playlistId)) === JSON.stringify(expected)",
        arg=original,
    )
    assert _ids(page, ALL) == original and _ids(page, FAVORITES) == []
    assert list(_sections(alice)) == original
    assert len(s.writes("POST", api_path("/api/playlist-preferences/reset"))) == 1
    s.assert_clean()
