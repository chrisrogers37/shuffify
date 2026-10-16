"""Reviewed React Library actions retain exact queue identity and actual provider facts."""

import json
import re
from collections import Counter

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, App, pid
from tests.e2e.test_direct_live_actions import (
    _choose_shuffle,
    _close_result,
    _lose_reply,
    _open_review,
    _panel,
    _run_accepted,
    _settled,
)
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.e2e.test_live_operation_outcomes import _nav_to_settings
from tests.e2e.test_pages import Visit
from tests.paths import api_path

TARGET = pid("alice-morning-mix")
OTHER = pid("alice-long-haul")


def _open_more(card):
    details = card.locator(":scope > details")
    if details.get_attribute("open") is None:
        details.locator(":scope > summary").click()


def _visibility_words(public):
    return "Make private now" if public else "Make public now"


def _posts(page):
    posts = []
    page.on("request", lambda req: posts.append(req.url.removeprefix(APP)) if req.method == "POST" else None)
    return posts


def _operation(page):
    details = (
        _panel(page).locator("details").filter(has=page.locator("summary", has_text="Exact retained operation details"))
    )
    return json.loads(details.locator("pre").text_content())


def _shuffle(card):
    return card.get_by_role("button", name=re.compile("^Shuffle now"))


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
        quick = _shuffle(card)
        run = _open_review(page, quick)
        review = _panel(page).get_by_role("region", name="Fresh action review")
        expect(review).to_contain_text("40 current tracks")
        expect(review).to_contain_text("Basic")
        expect(_panel(page)).to_contain_text("Morning Mix")
        assert posts == [api_path(f"/playlist/{TARGET}/live-context")] and fake.uris(TARGET) == before
        expect(_panel(page).get_by_role("heading", name="Playlist action", exact=True)).to_be_focused()
        for control in (run, _panel(page).get_by_role("button", name="Cancel", exact=True)):
            control.scroll_into_view_if_needed()
            box = control.bounding_box()
            assert box["height"] >= 44 and 0 <= box["y"] and box["y"] + box["height"] <= height
        page.screenshot(path=tmp_path / f"library-review-{width}.png", full_page=True)
        page.keyboard.press("Escape")
        expect(_panel(page)).to_have_count(0)
        expect(quick).to_be_focused()
        assert posts == [api_path(f"/playlist/{TARGET}/live-context")] and fake.uris(TARGET) == before
        run = _open_review(page, quick)
        path = api_path(f"/shuffle/{TARGET}")
        with page.expect_response(
            lambda response: response.url == APP + path and response.request.method == "POST"
        ) as reply:
            run.dblclick()
        response = reply.value
        assert response.status == 202
        accepted = response.json()
        operation = alice.wait_operation(response.headers["location"], accepted["operation_id"])
        assert operation["state"] == "succeeded"
        _settled(page)
        assert posts.count(path) == 1 and _operation(page)["id"] == operation["id"]
        assert _operation(page)["result"]["operation"]["aggregate_status"] == "complete"
        after = fake.uris(TARGET)
        assert after != before and Counter(after) == Counter(before)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") and visit.problems == []
    finally:
        visit.close()


def test_undo_review_refuses_a_changed_playlist_then_restores_the_reviewed_version(alice, fake, direct_browser):
    original = fake.uris(TARGET)
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        first = _run_accepted(page, _open_review(page, _shuffle(card)), api_path(f"/shuffle/{TARGET}"), alice)
        shuffled = fake.uris(TARGET)
        _close_result(page)
        undo = card.get_by_role("button", name="Restore previous saved version now", exact=True)
        run = _open_review(page, undo)
        expect(_panel(page)).to_contain_text("Restore saved version 1 from version 2.")
        external = shuffled[1:] + shuffled[:1]
        App("alice", fake.display_name("alice")).write(
            f"/workshop/{TARGET}/commit",
            json={
                "baseline_track_uris": shuffled,
                "track_uris": external,
            },
        )
        refused = _run_accepted(page, run, api_path(f"/undo/{TARGET}"), alice, state="failed")
        assert refused["result"]["operation"]["error_code"] == "resource_changed"
        assert all(not resource["dispatched"] for resource in refused["result"]["operation"]["resources"])
        assert fake.uris(TARGET) == external
        _close_result(page)
        expect(undo).to_have_attribute("aria-disabled", "true")
        expect(page.get_by_role("button", name="View action results", exact=True)).to_be_visible()
        page.once("dialog", lambda dialog: dialog.accept())
        page.reload(wait_until="networkidle")
        card.locator(".sh-library-playlist-actions > summary").click()
        restored = _run_accepted(page, _open_review(page, undo), api_path(f"/undo/{TARGET}"), alice)
        assert len({first["id"], refused["id"], restored["id"]}) == 3
        assert fake.uris(TARGET) == original and visit.problems == []
    finally:
        visit.close()


def test_undo_review_refuses_history_changed_by_another_tab_in_the_same_session(alice, fake, direct_browser):
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    tab = None
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        _run_accepted(page, _open_review(page, _shuffle(card)), api_path(f"/shuffle/{TARGET}"), alice)
        _close_result(page)
        run = _open_review(page, card.get_by_role("button", name="Restore previous saved version now", exact=True))
        tab = visit.context.new_page()
        tab.goto(f"{APP}/library", wait_until="networkidle")
        other = _choose_shuffle(tab, TARGET)
        _run_accepted(tab, _open_review(tab, _shuffle(other)), api_path(f"/shuffle/{TARGET}"), alice)
        changed = fake.uris(TARGET)
        rejected = _run_accepted(page, run, api_path(f"/undo/{TARGET}"), alice, state="failed")
        assert rejected["result"]["operation"]["error_code"] == "history_changed"
        assert fake.uris(TARGET) == changed and visit.problems == []
    finally:
        if tab:
            tab.close()
        visit.close()


def test_privacy_review_runs_a_verified_noop_and_inspection_does_not_invent_admission(alice, fake, direct_browser):
    toggle = APP + api_path(f"/playlist/{TARGET}/toggle-visibility")
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    try:
        visit.log_in()
        start = fake.playlist(TARGET)["public"]
        App("alice", fake.display_name("alice")).write(
            f"/playlist/{TARGET}/toggle-visibility", json={"public": not start}
        )
        card = page.locator(f'.sh-library-row[data-playlist-id="{TARGET}"]')
        _open_more(card)
        button = card.get_by_role("button", name=_visibility_words(start), exact=True)
        run = _open_review(page, button)
        expect(_panel(page)).to_contain_text(
            "Change from public to public." if not start else "Change from private to private."
        )
        noop = _run_accepted(page, run, api_path(f"/playlist/{TARGET}/toggle-visibility"), alice)
        resource = noop["result"]["operation"]["resources"][0]
        assert resource["status"] == "not_required" and resource["dispatched"] is False
        assert fake.playlist(TARGET)["public"] is not start
        expect(card.get_by_role("button", name=_visibility_words(not start), exact=True)).to_be_visible()
        _close_result(page)
        posted = []
        lost = _lose_reply(posted)
        page.route(toggle, lost)
        _open_review(page, card.get_by_role("button", name=_visibility_words(not start), exact=True)).click()
        expect(_panel(page).get_by_role("button", name="Retry this exact request", exact=True)).to_be_visible()
        assert alice.wait_operation(lost.location, lost.operation_id)["state"] == "succeeded"
        assert posted == [toggle] and fake.playlist(TARGET)["public"] is start
        _panel(page).get_by_role("button", name="Inspect current state", exact=True).click()
        expect(_panel(page).get_by_text("Inspection result: match", exact=True)).to_be_visible()
        # Current visibility is useful evidence, but the lost original admission still owns its claims.
        expect(_panel(page).get_by_role("button", name="Retry this exact request", exact=True)).to_be_visible()
        _close_result(page)
        expect(card.get_by_role("button", name=_visibility_words(start), exact=True)).to_have_attribute(
            "aria-disabled", "true"
        )
        assert posted == [toggle] and visit.problems == []
    finally:
        visit.close()


def test_lost_random_reply_keeps_attention_and_leaving_needs_an_explicit_decision(alice, fake, direct_browser):
    shuffle = APP + api_path(f"/shuffle/{TARGET}")
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
        _open_review(page, _shuffle(card)).click()
        expect(_panel(page).get_by_role("button", name="Retry this exact request", exact=True)).to_be_visible()
        assert alice.wait_operation(lost.location, lost.operation_id)["state"] == "succeeded"
        _panel(page).get_by_role("button", name="Inspect current state", exact=True).click()
        expect(_panel(page).get_by_text("Inspection result: unavailable", exact=True)).to_be_visible()
        assert posted == [shuffle]
        _close_result(page)
        expect(_shuffle(card)).to_have_attribute("aria-disabled", "true")
        other = _choose_shuffle(page, OTHER)
        expect(_shuffle(other)).to_have_attribute("aria-disabled", "true")
        _nav_to_settings(page)
        dialog = page.get_by_role("dialog", name="Leave this page?", exact=True)
        expect(dialog).to_contain_text("unconfirmed Library changes")
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        expect(page).to_have_url(APP + "/library")
        _nav_to_settings(page)
        dialog.get_by_role("button", name="Leave page", exact=True).click()
        expect(page).to_have_url(APP + "/settings")
        assert dialogs == [] and posted == [shuffle] and visit.problems == []
    finally:
        visit.close()


def test_accepted_work_keeps_observing_across_navigation_until_verified(alice, fake, direct_browser):
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    try:
        visit.log_in()
        fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="slow", delay=3, playlist_id=TARGET)
        card = _choose_shuffle(page, TARGET)
        path = api_path(f"/shuffle/{TARGET}")
        with page.expect_response(APP + path) as reply:
            _open_review(page, _shuffle(card)).click()
        assert reply.value.status == 202
        accepted = reply.value.json()
        _close_result(page)
        _nav_to_settings(page)
        # An accepted operation has a durable identity and survives a page/panel unmount.
        # Edited shuffle options still require their normal Leave confirmation.
        dialog = page.get_by_role("dialog", name="Leave this page?", exact=True)
        page.wait_for_function("""() => location.pathname === '/settings' ||
            [...document.querySelectorAll('dialog[open]')].some(dialog =>
                dialog.textContent.includes('Leave this page?'))""")
        if dialog.is_visible():
            dialog.get_by_role("button", name="Leave page", exact=True).click()
        expect(page).to_have_url(APP + "/settings")
        expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
        settled = alice.wait_operation(reply.value.headers["location"], accepted["operation_id"])
        assert (
            settled["state"] == "succeeded"
            and fake.uris(TARGET) != settled["result"]["operation"]["resources"][0]["baseline_track_uris"]
        )
        page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role(
            "link", name="Library", exact=True,
        ).click()
        expect(page.get_by_role("heading", name="Your playlists", exact=True)).to_be_visible()
        page.get_by_role("button", name="View action results", exact=True).click()
        result = _panel(page).get_by_role("region", name="Retained operation result", exact=True)
        expect(result.get_by_role("heading", name=f"Operation #{accepted['operation_id']}", exact=True)).to_be_visible()
        expect(result.get_by_role("status")).to_have_text("succeeded")
        assert [fault["fired"] for fault in fake.faults()] == [1]
        assert visit.problems == []
    finally:
        visit.close()
