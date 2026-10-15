"""Library reviewed live actions through the app and the isolated Spotify fake."""

from collections import Counter

import pytest

from tests.e2e.conftest import APP, App, pid
from tests.e2e.test_direct_live_actions import (
    _choose_shuffle,
    _close_result,
    _lose_reply,
    _open_review,
    _settled,
)
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.e2e.test_pages import Visit
from tests.paths import api_path

TARGET = pid("alice-morning-mix")
OTHER = pid("alice-long-haul")
SETTINGS_LINK = '.sh-app-nav-desktop a[data-nav-key="settings"]'


def _open_more(card):
    """Open the row's Playlist actions without toggling them shut when already open.

    The child combinator matters: the shuffle method's own summary inside is a descendant too (strict mode).
    """
    if not card.locator(".visibility-btn").is_visible():
        card.locator(".library-playlist-actions > summary").click()


def _visibility_words(public):
    """The visibility button names the change it would make now, so it shows the opposite of the current state."""
    return "Make private now" if public else "Make public now"


def _posts(page):
    posts = []
    page.on(
        "request",
        lambda req: (
            posts.append(req.url.removeprefix(APP)) if req.method == "POST" else None
        ),
    )
    return posts


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (375, 812)])
def test_shuffle_review_reads_only_then_one_named_run_dispatches_once(
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
    posts = _posts(page)
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        quick = card.locator(".library-quick-shuffle")
        run = _open_review(page, quick, "Shuffle now")
        review = page.locator("#live-operation-review").inner_text()
        assert (
            "Playlist: Morning Mix" in review
            and "Method: Basic" in review
            and "Current tracks: 40." in review
        )
        assert (
            posts == [api_path(f"/playlist/{TARGET}/live-context")]
            and fake.uris(TARGET) == before
        )
        assert page.evaluate("document.activeElement.id") == "live-operation-heading"
        # Named footer actions are reachable and large enough to touch.
        for control in (run, page.locator("#live-operation-close")):
            control.scroll_into_view_if_needed()
            box = control.bounding_box()
            assert (
                box["height"] >= 44
                and 0 <= box["y"]
                and box["y"] + box["height"] <= height
            )
        assert page.locator("#live-operation-close").inner_text() == "Cancel"
        page.screenshot(path=tmp_path / f"library-review-{width}.png", full_page=True)
        # Escape cancels before dispatch and returns focus to the trigger.
        page.keyboard.press("Escape")
        page.wait_for_function("() => !PlaylistOperations.isPreparing()")
        assert page.locator("#live-operation-panel").is_hidden()
        assert page.evaluate(
            "id => document.activeElement === document.querySelector("
            "`.card-tile[data-playlist-id='${id}'] .library-quick-shuffle`)",
            TARGET,
        )
        assert (
            posts == [api_path(f"/playlist/{TARGET}/live-context")]
            and fake.uris(TARGET) == before
        )
        run = _open_review(page, quick, "Shuffle now")
        run.dblclick()
        _settled(page, 1)
        assert posts.count(api_path(f"/shuffle/{TARGET}")) == 1
        assert page.evaluate("PlaylistOperations.getRecords()[0].complete") is True
        after = fake.uris(TARGET)
        assert after != before and Counter(after) == Counter(before)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        assert visit.problems == []
    finally:
        visit.close()


def test_undo_review_refuses_a_changed_playlist_then_restores_the_reviewed_version(
    alice,
    fake,
    direct_browser,
):
    original = fake.uris(TARGET)
    visit = Visit(direct_browser, "desktop", allowed=[(api_path(f"/undo/{TARGET}"), 409)])
    page = visit.page
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        _open_review(
            page, card.locator(".library-quick-shuffle"), "Shuffle now"
        ).click()
        _settled(page, 1)
        shuffled = fake.uris(TARGET)
        _close_result(page)
        undo = card.locator(".library-undo")
        _open_review(page, undo, "Restore previous saved version now")
        review = page.locator("#live-operation-review").inner_text()
        assert (
            "previous version saved in this session" in review
            and "changes the order" in review
        )
        # Another client changes the playlist after the review was shown.
        external = shuffled[1:] + shuffled[:1]
        App("alice", fake.display_name("alice")).write(
            f"/workshop/{TARGET}/commit",
            json={"baseline_track_uris": shuffled, "track_uris": external},
        )
        page.locator("#live-operation-run").click()
        _settled(page, 2)
        record = page.evaluate("PlaylistOperations.getRecords()[1]")
        assert record["operation"]["error_code"] == "resource_changed"
        assert (
            record["noEffect"] is True
            and record["blocked"] is False
            and record["attention"] is False
        )
        assert fake.uris(TARGET) == external
        assert "Review it again" in page.locator("#live-operation-results").inner_text()
        _close_result(page)
        _open_review(page, undo, "Restore previous saved version now").click()
        _settled(page, 3)
        assert page.evaluate("PlaylistOperations.getRecords()[2].complete") is True
        assert fake.uris(TARGET) == original
        assert visit.problems == []
    finally:
        visit.close()


def test_undo_review_refuses_history_changed_by_another_tab_in_the_same_session(
    alice,
    fake,
    direct_browser,
):
    visit = Visit(direct_browser, "desktop", allowed=[(api_path(f"/undo/{TARGET}"), 409)])
    page = visit.page
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        _open_review(
            page, card.locator(".library-quick-shuffle"), "Shuffle now"
        ).click()
        _settled(page, 1)
        _close_result(page)
        _open_review(
            page, card.locator(".library-undo"), "Restore previous saved version now"
        )
        tab = visit.context.new_page()
        tab.goto(f"{APP}/", wait_until="networkidle")
        other = _choose_shuffle(tab, TARGET)
        _open_review(
            tab, other.locator(".library-quick-shuffle"), "Shuffle now"
        ).click()
        _settled(tab, 1)
        changed = fake.uris(TARGET)
        page.locator("#live-operation-run").click()
        _settled(page, 2)
        assert (
            page.evaluate("PlaylistOperations.getRecords()[1].operation.error_code")
            == "history_changed"
        )
        assert fake.uris(TARGET) == changed
        tab.close()
        assert visit.problems == []
    finally:
        visit.close()


def test_privacy_review_runs_a_verified_noop_and_check_resolves_only_a_lost_visibility_reply(
    alice,
    fake,
    direct_browser,
):
    toggle = (APP + api_path(f'/playlist/{TARGET}/toggle-visibility'))
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    try:
        visit.log_in()
        start = fake.playlist(TARGET)["public"]
        # Another client changes visibility after the Library rendered it.
        App("alice", fake.display_name("alice")).write(
            f"/playlist/{TARGET}/toggle-visibility",
            json={"public": not start},
        )
        card = page.locator(f'.card-tile[data-playlist-id="{TARGET}"]')
        _open_more(card)
        button = card.locator(".visibility-btn")
        label = "Make playlist public now" if not start else "Make playlist private now"
        run = _open_review(page, button, label)
        assert "already" in page.locator("#live-operation-review").inner_text()
        run.click()
        _settled(page, 1)
        record = page.evaluate("PlaylistOperations.getRecords()[0]")
        assert (
            record["complete"] is True
            and record["operation"]["resources"][0]["status"] == "not_required"
        )
        assert fake.playlist(TARGET)["public"] is (not start)
        assert button.get_attribute("data-public") == str(not start).lower()
        assert button.locator(".visibility-label").text_content().strip() == _visibility_words(not start)
        _close_result(page)
        posted = []
        lost = _lose_reply(posted)
        page.route(toggle, lost)
        _open_more(card)
        label = "Make playlist public now" if start else "Make playlist private now"
        _open_review(page, button, label).click()
        _settled(page, 2)
        assert alice.wait_operation(lost.location, lost.operation_id)["state"] == "succeeded"
        assert posted == [toggle] and fake.playlist(TARGET)["public"] is start
        assert (
            page.evaluate(
                "key => PlaylistOperations.isBlocked(key)", f"privacy:{TARGET}"
            )
            is True
        )
        assert (
            page.evaluate(
                "key => PlaylistOperations.isBlocked(key)", f"shuffle:{TARGET}"
            )
            is False
        )
        page.locator('[data-live-check="1"]').click()
        page.wait_for_function(
            "() => PlaylistOperations.getRecords()[1].observations.length === 1"
        )
        checked = page.evaluate("PlaylistOperations.getRecords()[1]")
        assert (
            checked["observations"][0]["status"] == "match"
            and checked["blocked"] is False
        )
        assert checked["aggregate"] == "unknown"
        assert button.get_attribute("data-public") == str(start).lower()
        assert button.locator(".visibility-label").text_content().strip() == _visibility_words(start)
        assert posted == [toggle]
        assert visit.problems == []
    finally:
        visit.close()


def test_lost_random_reply_keeps_attention_and_leaving_needs_an_explicit_decision(
    alice,
    fake,
    direct_browser,
):
    shuffle = (APP + api_path(f'/shuffle/{TARGET}'))
    # The check reports the lost random order as unavailable: an expected 502.
    visit = Visit(direct_browser, "desktop", allowed=[(api_path(f"/playlist/{TARGET}/inspect-live"), 502)])
    page = visit.page
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    posted = []
    lost = _lose_reply(posted)
    page.route(shuffle, lost)
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        _open_review(
            page, card.locator(".library-quick-shuffle"), "Shuffle now"
        ).click()
        _settled(page, 1)
        assert alice.wait_operation(lost.location, lost.operation_id)["state"] == "succeeded"
        assert (
            page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "unknown"
        )
        page.locator('[data-live-check="0"]').click()
        page.wait_for_function(
            "() => PlaylistOperations.getRecords()[0].observations.length === 1"
        )
        assert (
            page.evaluate("PlaylistOperations.getRecords()[0].observations[0].status")
            == "unavailable"
        )
        # Unknown saved history blocks track actions on every card; visibility stays usable.
        other = page.locator(
            f'.card-tile[data-playlist-id="{OTHER}"] .library-quick-shuffle'
        )
        assert (
            card.locator(".library-quick-shuffle").is_disabled() and other.is_disabled()
        )
        _close_result(page)
        page.locator(SETTINGS_LINK).click()
        page.locator("#live-operation-leave").wait_for(state="visible")
        assert (
            page.evaluate("document.activeElement.id") == "live-operation-leave-heading"
        )
        page.keyboard.press("Escape")
        page.locator("#live-operation-leave").wait_for(state="hidden")
        assert page.url == f"{APP}/"
        page.locator(SETTINGS_LINK).click()
        page.locator("#live-operation-leave-confirm").click()
        page.wait_for_url(f"{APP}/settings")
        assert dialogs == [] and posted == [shuffle]
        assert visit.problems == []
    finally:
        visit.close()


def test_busy_blocks_navigation_until_the_dispatched_write_is_verified(
    alice, fake, direct_browser
):
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    try:
        visit.log_in()
        fake.arm(
            operation="PUT /playlists/{playlist_id}/items",
            kind="slow",
            delay=3,
            playlist_id=TARGET,
        )
        card = _choose_shuffle(page, TARGET)
        _open_review(
            page, card.locator(".library-quick-shuffle"), "Shuffle now"
        ).click()
        page.wait_for_function("() => PlaylistOperations.isBusy()")
        page.locator(SETTINGS_LINK).click()
        assert (
            page.url == f"{APP}/" and page.locator("#live-operation-leave").is_hidden()
        )
        _settled(page, 1)
        assert page.evaluate("PlaylistOperations.getRecords()[0].complete") is True
        page.locator(SETTINGS_LINK).click()
        page.wait_for_url(f"{APP}/settings")
        assert [fault["fired"] for fault in fake.faults()] == [1]
        assert visit.problems == []
    finally:
        visit.close()
