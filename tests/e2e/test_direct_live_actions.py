"""Direct actions observed through the app and the isolated Spotify fake."""

from collections import Counter

import pytest

from tests.e2e.conftest import APP, App, hold_playlist_lock, pid
from tests.e2e.test_pages import CHROMIUM_ARGS, Visit

TARGET = pid("alice-morning-mix")
LONG_HAUL = pid("alice-long-haul")


def _direct(body, kind):
    assert body["operation"]["kind"] == kind
    assert not {"execution_id", "started_at", "execution_state", "tracks_added",
                "tracks_total"}.intersection(body)
    return body["operation"]


def _snapshot(alice, uris):
    return alice.ok("post", f"/playlist/{TARGET}/snapshots", json={
        "playlist_name": "Morning Mix", "track_uris": uris,
        "trigger_description": "Before rearranging",
    })["snapshot"]["id"]


def test_direct_shuffle_reads_external_changes_and_undo_preserves_occurrences(alice, fake):
    alice.ok("post", f"/shuffle/{LONG_HAUL}", data={"algorithm": "BasicShuffle"})
    stale = fake.uris(LONG_HAUL)
    other = App("alice", fake.display_name("alice"))
    external = [stale[-1]] + stale[1:] + [stale[-1]]
    other.ok("post", f"/workshop/{LONG_HAUL}/commit", json={
        "baseline_track_uris": stale, "track_uris": external,
    })
    body = alice.ok("post", f"/shuffle/{LONG_HAUL}", data={
        "algorithm": "BasicShuffle",
    })
    operation = _direct(body, "shuffle")
    assert operation["aggregate_status"] == "complete"
    resource = operation["resources"][0]
    assert resource["baseline_track_uris"] == external
    assert resource["current_track_uris"] == resource["desired_track_uris"] == fake.uris(LONG_HAUL)
    assert Counter(fake.uris(LONG_HAUL)) == Counter(external)
    assert fake.uris(LONG_HAUL) != external
    undo = alice.ok("post", f"/undo/{LONG_HAUL}")
    assert _direct(undo, "undo")["aggregate_status"] == "complete"
    assert fake.uris(LONG_HAUL) == external


def test_snapshot_restore_records_the_actual_before_order_for_undo(alice, fake):
    saved = fake.uris(TARGET)
    snapshot_id = _snapshot(alice, saved)
    alice.ok("post", f"/shuffle/{TARGET}", data={"algorithm": "BasicShuffle"})
    shuffled = fake.uris(TARGET)
    other = App("alice", fake.display_name("alice"))
    external = [shuffled[-1]] + shuffled[:-2]
    other.ok("post", f"/workshop/{TARGET}/commit", json={
        "baseline_track_uris": shuffled, "track_uris": external,
    })
    restored = alice.ok("post", f"/snapshots/{snapshot_id}/restore")
    operation = _direct(restored, "snapshot_restore")
    assert operation["aggregate_status"] == "complete"
    assert operation["resources"][0]["baseline_track_uris"] == external
    assert operation["resources"][0]["current_track_uris"] == saved
    assert restored["playlist_id"] == TARGET and fake.uris(TARGET) == saved
    alice.ok("post", f"/undo/{TARGET}")
    assert fake.uris(TARGET) == external


def test_direct_partial_batch_keeps_exact_intent_and_never_compensates(alice, fake):
    before = fake.uris(LONG_HAUL)
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=LONG_HAUL)
    response = alice.post(f"/shuffle/{LONG_HAUL}", data={"algorithm": "BasicShuffle"})
    assert response.status_code == 502, response.text
    operation = _direct(response.json(), "shuffle")
    assert operation["aggregate_status"] == "unknown" and operation["retryable"] is False
    resource = operation["resources"][0]
    assert resource["baseline_track_uris"] == before
    assert Counter(resource["desired_track_uris"]) == Counter(before)
    # Dispatch failed before readback: the service must not label its baseline
    # as a current observation. The independent fake shows the partial effect.
    assert "current_track_uris" not in resource
    assert len(fake.uris(LONG_HAUL)) == 100
    assert fake.uris(LONG_HAUL) == resource["desired_track_uris"][:100]
    assert [fault["fired"] for fault in fake.faults()] == [1]


def test_privacy_uses_strict_boolean_and_changes_only_metadata(alice, fake):
    before = fake.playlist(TARGET)
    desired = not before["public"]
    for invalid in ("false", 0, None, [], {}):
        response = alice.post(f"/playlist/{TARGET}/toggle-visibility", json={"public": invalid})
        assert response.status_code == 400
        assert _direct(response.json(), "privacy")["resources"] == []
        assert fake.playlist(TARGET) == before
    changed = alice.ok("post", f"/playlist/{TARGET}/toggle-visibility", json={"public": desired})
    operation = _direct(changed, "privacy")
    assert operation["aggregate_status"] == "complete" and changed["public"] is desired
    assert operation["resources"][0]["current_metadata"]["public"] is desired
    assert fake.playlist(TARGET) == {**before, "public": desired}
    repeated = alice.ok("post", f"/playlist/{TARGET}/toggle-visibility", json={"public": desired})
    resource = _direct(repeated, "privacy")["resources"][0]
    assert resource["status"] == "not_required" and not resource["dispatched"]
    assert fake.playlist(TARGET) == {**before, "public": desired}


@pytest.mark.parametrize("kind", ["shuffle", "undo", "snapshot_restore"])
def test_direct_target_actions_honor_the_shared_postgres_lock(alice, fake, kind):
    baseline = fake.uris(TARGET)
    snapshot_id = _snapshot(alice, baseline)
    alice.ok("post", f"/shuffle/{TARGET}", data={"algorithm": "BasicShuffle"})
    before = fake.playlist(TARGET)
    path = f"/snapshots/{snapshot_id}/restore" if kind == "snapshot_restore" else f"/{kind}/{TARGET}"
    with hold_playlist_lock(TARGET):
        response = alice.post(path, data={"algorithm": "BasicShuffle"} if kind == "shuffle" else None)
    assert response.status_code == 409, response.text
    operation = _direct(response.json(), kind)
    assert operation["error_code"] == "playlist_busy" and operation["retryable"] is True
    assert operation["resources"] == []
    assert fake.playlist(TARGET) == before


@pytest.fixture(scope="module")
def direct_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        yield browser
        browser.close()


def _choose_shuffle(page, playlist_id):
    """Pick Basic in the row's Playlist actions, which stay open so Shuffle now and Restore are reachable."""
    card = page.locator(f'.card-tile[data-playlist-id="{playlist_id}"]')
    # The child combinator: the shuffle method's own summary inside is a descendant too (Playwright strict mode).
    card.locator(".library-playlist-actions > summary").click()
    card.locator(".library-shuffle-summary").click()
    card.locator(".library-shuffle-method").select_option("BasicShuffle")
    card.locator(".library-shuffle-done").click()
    return card


def _settled(page, count):
    page.wait_for_function(
        "n => PlaylistOperations.getRecords().length === n && !PlaylistOperations.isBusy()", arg=count,
    )


def _close_result(page):
    page.locator("#live-operation-panel [data-panel-close]").first.click()


def _open_review(page, trigger, label=None):
    """Live actions open a review first; return its final named Run control."""
    trigger.click()
    page.wait_for_function(
        "label => { const text = document.getElementById('live-operation-heading').textContent;"
        " return label ? text === 'Review: ' + label : text.startsWith('Review: '); }",
        arg=label,
    )
    return page.locator("#live-operation-run")


def _heading(page, heading):
    page.wait_for_function(
        "heading => document.getElementById('live-operation-heading').textContent === heading",
        arg=heading,
    )


# The Workshop's tools are task panels, #workshop-<tool>-panel: beside the playlist at 1024px and wider, over it
# below. One panel is open at a time, so a live review, the Save review or another tool replaces the open one;
# a test that goes back to a tool opens it again.
def _open_tool(page, tool):
    """Open a Workshop tool: options, add, inbox, archive, routines, history or details.

    Shuffle options, Add tracks and Inbox have buttons on the page at every width. Archive, Routines, History
    and Playlist details have them from 1024px; below that they are listed under Playlist tools. Does nothing
    when the tool is already open. Below 1024px an open tool covers these buttons, so close it first."""
    panel = page.locator(f"#workshop-{tool}-panel")
    if panel.is_visible():
        return
    button = page.locator("#workshop-details-btn" if tool == "details" else f"#workshop-tool-{tool}")
    if button.is_visible():
        button.click()
    else:
        page.click("#workshop-tools-btn")
        page.locator(f'#workshop-tools-panel [data-workshop-tool="{tool}"]').click()
    panel.wait_for(state="visible")


def _open_inbox(page):
    """Open Inbox, then wait for the raid status that its Collect and Add collected tracks buttons follow."""
    _open_tool(page, "inbox")
    page.wait_for_function("() => raidPanel.loaded")


def _close_tool(page, tool):
    """Close a tool with its Close button. Source or Archive setup, opened from Inbox or Archive, returns to it."""
    panel = page.locator(f"#workshop-{tool}-panel")
    panel.locator("[data-panel-close]").first.click()
    panel.wait_for(state="hidden")


def _open_row_actions(page, row):
    """Open one row's Row actions, the single #track-actions group shown inside that row; returns it."""
    button = row.locator(".track-actions-btn")
    if button.get_attribute("aria-expanded") != "true":
        button.click()
    actions = page.locator("#track-actions")
    actions.wait_for(state="visible")
    return actions


def _remove_row(page, index=0):
    """Remove one row from the draft through its Row actions."""
    _open_row_actions(page, page.locator("#track-list .track-item").nth(index))
    page.click('#track-actions [data-row-action="remove"]')


def _lose_reply(posted):
    """Let the app apply the request, then lose its reply before the page reads it."""

    def handle(route):
        posted.append(route.request.url)
        response = route.fetch()
        assert response.status == 200
        route.fulfill(status=200, content_type="application/json", body="")

    return handle


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (375, 812)])
def test_library_shuffle_and_undo_show_verified_results_without_navigation(
    alice, fake, direct_browser, tmp_path, width, height,
):
    before = fake.uris(TARGET)
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": width, "height": height})
    posts = []
    page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        assert posts == [] and fake.uris(TARGET) == before
        _open_review(page, card.locator(".library-quick-shuffle")).click()
        _settled(page, 1)
        assert page.evaluate("PlaylistOperations.getRecords()[0].complete") is True
        assert fake.uris(TARGET) != before
        assert page.url == f"{APP}/"
        page.screenshot(path=tmp_path / f"library-direct-{width}.png", full_page=True)
        _close_result(page)
        _open_review(page, card.locator(".library-undo")).click()
        _settled(page, 2)
        assert page.evaluate("PlaylistOperations.getRecords()[1].complete") is True
        assert fake.uris(TARGET) == before
        context = f"{APP}/playlist/{TARGET}/live-context"
        assert posts == [context, f"{APP}/shuffle/{TARGET}", context, f"{APP}/undo/{TARGET}"]
        assert card.locator(".library-shuffle-method").input_value() == "BasicShuffle"
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert visit.problems == []
    finally:
        visit.close()


def test_lost_library_shuffle_response_blocks_random_replay(alice, fake, direct_browser):
    before = fake.uris(TARGET)
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    posted = []
    shuffle_url = f"{APP}/shuffle/{TARGET}"

    page.route(shuffle_url, _lose_reply(posted))
    try:
        visit.log_in()
        card = _choose_shuffle(page, TARGET)
        _open_review(page, card.locator(".library-quick-shuffle")).click()
        _settled(page, 1)
        assert fake.uris(TARGET) != before
        assert page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "unknown"
        assert page.evaluate("PlaylistOperations.hasAttention()") is True
        _close_result(page)
        assert card.locator(".library-quick-shuffle").is_disabled()
        card.locator(".library-quick-shuffle").dispatch_event("click")
        assert posted == [shuffle_url]
        assert page.evaluate("PlaylistOperations.getRecords().length") == 1
        assert visit.problems == []
    finally:
        visit.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (375, 812)])
def test_workshop_privacy_preserves_draft_and_snapshot_requires_resolution(
    alice, fake, direct_browser, tmp_path, width, height,
):
    original = fake.uris(TARGET)
    desired = list(reversed(original))
    snapshot_id = _snapshot(alice, desired)
    was_public = fake.playlist(TARGET)["public"]
    visit = Visit(direct_browser, "desktop")
    page = visit.page
    page.set_viewport_size({"width": width, "height": height})
    posts = []
    page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
    restore_url = f"{APP}/snapshots/{snapshot_id}/restore"
    draft_state = (
        "({working:workshopState.workingUris,saved:workshopState.savedUris,"
        "recovery:workshopState.sourceRecovery})"
    )
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
        _remove_row(page)
        draft = page.evaluate(draft_state)
        # Visibility is metadata only: it is reviewed and runs beside a track draft.
        _open_tool(page, "details")
        _open_review(page, page.locator("#workshop-visibility-btn")).click()
        _settled(page, 1)
        assert page.evaluate("PlaylistOperations.getRecords()[0].complete") is True
        assert fake.playlist(TARGET)["public"] is not was_public
        assert fake.uris(TARGET) == original
        assert page.evaluate(draft_state) == draft
        _close_result(page)
        restore = page.locator(f'[data-live-operation-key="snapshot_restore:{snapshot_id}"]')

        def choose_restore():
            # A review or the Save panel replaces History, so each Restore is reached through History again.
            _open_tool(page, "history")
            restore.click()

        # A restore writes the drafted tracks, so the draft is settled first. Escape sends nothing.
        choose_restore()
        _heading(page, "Save or discard your draft first")
        assert page.locator("#live-operation-discard").is_visible()
        assert page.locator("#live-operation-run").inner_text() == "Review & save draft"
        page.screenshot(path=tmp_path / f"restore-draft-conflict-{width}.png", full_page=True)
        page.keyboard.press("Escape")
        page.wait_for_function("() => !PlaylistOperations.isPreparing()")
        # Review & save opens the real Save review; backing out of it drops the restore.
        choose_restore()
        _heading(page, "Save or discard your draft first")
        page.click("#live-operation-run")
        page.locator("#workshop-save-panel").wait_for(state="visible")
        assert restore_url not in posts and fake.uris(TARGET) == original
        assert page.evaluate("workshopState.workingUris") == original[1:]
        page.click("#workshop-review-back")
        page.wait_for_function("() => !PlaylistOperations.isPreparing()")
        assert restore_url not in posts
        # Discarding is local; the restore is then reviewed afresh and still needs its own Run.
        choose_restore()
        _heading(page, "Save or discard your draft first")
        page.click("#live-operation-discard")
        _heading(page, "Review: Restore saved snapshot now")
        assert page.evaluate("workshopState.workingUris") == original
        assert restore_url not in posts
        page.click("#live-operation-run")
        _settled(page, 2)
        assert page.evaluate("PlaylistOperations.getRecords()[1].complete") is True
        assert page.evaluate("workshopState.workingUris") == desired
        assert page.evaluate("workshopState.savedUris") == desired
        assert fake.uris(TARGET) == desired
        assert page.url == f"{APP}/workshop/{TARGET}"
        assert page.evaluate(
            "document.getElementById('live-operation-panel').contains(document.activeElement)"
        ), page.evaluate("({id:document.activeElement.id,tag:document.activeElement.tagName})")
        assert posts.count(restore_url) == 1
        assert f"{APP}/workshop/{TARGET}/commit" not in posts
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        page.screenshot(path=tmp_path / f"direct-workshop-{width}.png", full_page=True)
        assert visit.problems == []
    finally:
        visit.close()


def test_snapshot_restore_is_reviewed_against_fresh_spotify_and_refuses_a_later_change(
    alice, fake, direct_browser,
):
    original = fake.uris(TARGET)
    desired = list(reversed(original))
    snapshot_id = _snapshot(alice, desired)
    restore_path = f"/snapshots/{snapshot_id}/restore"
    other = App("alice", fake.display_name("alice"))
    visit = Visit(direct_browser, "desktop", allowed=[(restore_path, 409)])
    page = visit.page
    posts = []
    page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
        # Another session drops a track after this page loaded: the review reads Spotify, not the page.
        other.ok("post", f"/workshop/{TARGET}/commit", json={
            "baseline_track_uris": original, "track_uris": original[1:],
        })
        _open_tool(page, "history")
        restore = page.locator(f'[data-live-operation-key="snapshot_restore:{snapshot_id}"]')
        run = _open_review(page, restore, "Restore saved snapshot now")
        assert "Restoring adds 1 track, changes the order." in page.locator("#live-operation-review").inner_text()
        # A change after the review is refused before anything is written.
        other.ok("post", f"/workshop/{TARGET}/commit", json={
            "baseline_track_uris": original[1:], "track_uris": original[2:],
        })
        run.click()
        _settled(page, 1)
        record = page.evaluate("PlaylistOperations.getRecords()[0]")
        assert record["noEffect"] is True and record["operation"]["error_code"] == "resource_changed"
        assert "Review it again" in page.locator("#live-operation-results").inner_text()
        assert fake.uris(TARGET) == original[2:]
        _close_result(page)
        assert restore.is_enabled()
        _open_tool(page, "history")
        run = _open_review(page, restore, "Restore saved snapshot now")
        assert "Restoring adds 2 tracks, changes the order." in page.locator("#live-operation-review").inner_text()
        run.click()
        _settled(page, 2)
        assert page.evaluate("PlaylistOperations.getRecords()[1].complete") is True
        assert fake.uris(TARGET) == desired
        assert page.evaluate("workshopState.workingUris") == desired
        assert posts.count(f"{APP}{restore_path}") == 2
        assert visit.problems == []
    finally:
        visit.close()


@pytest.mark.parametrize("kind", ["shuffle", "privacy"])
def test_proven_first_write_rejection_allows_an_explicit_browser_retry(
    alice, fake, direct_browser, kind,
):
    before = fake.playlist(TARGET)
    before_uris = fake.uris(TARGET)
    operation = "PUT /playlists/{playlist_id}/items" if kind == "shuffle" else "PUT /playlists/{playlist_id}"
    fake.arm(operation=operation, kind="quota", playlist_id=TARGET)
    path = f"/shuffle/{TARGET}" if kind == "shuffle" else f"/playlist/{TARGET}/toggle-visibility"

    visit = Visit(direct_browser, "desktop", allowed=[(path, 429)])
    page = visit.page
    responses = []
    page.on("response", lambda response: responses.append(response.status) if response.url == APP + path else None)
    try:
        visit.log_in()
        if kind == "shuffle":
            card = _choose_shuffle(page, TARGET)
            action = card.locator(".library-quick-shuffle")
        else:
            page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
            _remove_row(page)
            draft = page.evaluate("workshopState.workingUris")
            action = page.locator("#workshop-visibility-btn")

        def review():
            # The visibility change is in Playlist details, which a review replaces.
            if kind == "privacy":
                _open_tool(page, "details")
            return _open_review(page, action)

        review().click()
        _settled(page, 1)
        assert responses == [429]
        assert fake.playlist(TARGET) == before
        assert page.evaluate("PlaylistOperations.getRecords()[0].operation.retryable") is True
        assert page.evaluate("PlaylistOperations.getRecords()[0].aggregate") == "partial"
        _close_result(page)
        assert action.is_enabled()
        # A second dispatch requires a fresh review and an explicit Run.
        review().click()
        _settled(page, 2)
        assert responses == [429, 200]
        assert page.evaluate("PlaylistOperations.getRecords()[1].complete") is True
        if kind == "shuffle":
            assert fake.uris(TARGET) != before_uris
        else:
            assert fake.playlist(TARGET) == {**before, "public": not before["public"]}
            assert page.evaluate("workshopState.workingUris") == draft
        assert [fault["fired"] for fault in fake.faults()] == [1]
        assert visit.problems == []
    finally:
        visit.close()
