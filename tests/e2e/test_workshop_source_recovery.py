"""Source finalizers observe actual fake Spotify state without replaying targets.

Run only through the canonical harness. Every playlist/account is fictional.
"""

import time

import pytest
from playwright.sync_api import sync_playwright

from tests.e2e.conftest import APP, pid
from tests.e2e.test_direct_live_actions import _close_tool, _open_inbox, _remove_row
from tests.e2e.test_pages import CHROMIUM_ARGS, Visit
from tests.paths import api_path

TARGET = pid("alice-morning-mix")
ARCHIVE = pid("alice-morning-archive")
OTHER = pid("alice-old-faithful")
RAID_SOURCE = pid("bob-finds")


def _pair(alice, archive=ARCHIVE):
    alice.ok("post", f"/playlist/{TARGET}/pair", json={
        "archive_playlist_id": archive,
        "archive_playlist_name": "Browser-supplied name is not authoritative",
        "production_playlist_name": "Morning Mix",
    })


def _save(alice, playlist, baseline, desired):
    return alice.write(f"/workshop/{playlist}/commit", json={
        "baseline_track_uris": baseline, "track_uris": desired,
    })


def _archive_intent(alice, fake):
    _pair(alice)
    current = fake.uris(TARGET)
    # Duplicate occurrences are deliberate: recovery must never deduplicate.
    _save(alice, TARGET, current, [current[0], current[0]] + current[1:])
    context = alice.ok("get", f"/workshop/{TARGET}/source-context?raid=0&archive=1")
    assert context["archive"]["name"] == "Morning Mix [Archive]"
    assert context["archive"]["baseline_track_uris"] == []
    desired = current[1:]
    payload = {
        "source_playlist_id": context["archive"]["id"],
        "target_track_uris": desired,
        "baseline_track_uris": context["archive"]["baseline_track_uris"],
        "track_uris": [current[0], current[0]],
    }
    _save(alice, TARGET, [current[0], current[0]] + current[1:], desired)
    return payload


def test_saved_target_survives_rejected_archive_step_and_source_only_retry(alice, fake):
    payload = _archive_intent(alice, fake)
    confirmed_target = fake.playlist(TARGET)
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=ARCHIVE)
    rejected = alice.post(api_path(f"/playlist/{TARGET}/pair/archive"), json=payload)
    assert rejected.status_code == 502, rejected.text
    result = rejected.json()
    assert (result["status"], result["retryable"]) == ("failed", True)
    assert fake.uris(ARCHIVE) == []
    assert fake.playlist(TARGET) == confirmed_target

    completed = alice.ok("post", f"/playlist/{TARGET}/pair/archive", json=payload)
    assert completed["status"] == "complete"
    assert completed["affected_count"] == 2
    assert fake.uris(ARCHIVE) == payload["track_uris"]
    assert fake.playlist(TARGET) == confirmed_target, "source retry rewrote the target"
    before_inspect = fake.playlist(ARCHIVE)
    inspected = alice.ok("post", f"/playlist/{TARGET}/pair/archive", json={**payload, "inspect_only": True})
    assert inspected["status"] == "not_required"
    assert inspected["affected_count"] == 0
    assert fake.playlist(ARCHIVE) == before_inspect, "inspection replayed duplicate additions"


def test_lost_archive_response_is_inspected_without_replaying_duplicates(alice, fake):
    payload = _archive_intent(alice, fake)
    confirmed_target = fake.playlist(TARGET)
    # The client times out at 30s; the fake still applies this exact write.
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="slow",
             playlist_id=ARCHIVE, delay=31)
    response = alice.post(api_path(f"/playlist/{TARGET}/pair/archive"), json=payload)
    assert response.status_code == 502, response.text
    assert (response.json()["status"], response.json()["retryable"]) == ("unknown", False)
    # The API's bounded request deadline can expire before the transport's
    # timeout. Settle the original late call without sending another write.
    fake.release()
    deadline = time.monotonic() + 5
    while fake.uris(ARCHIVE) != payload["track_uris"] and time.monotonic() < deadline:
        time.sleep(0.1)
    assert fake.uris(ARCHIVE) == payload["track_uris"]
    before_inspect = fake.playlist(ARCHIVE)
    checked = alice.ok("post", f"/playlist/{TARGET}/pair/archive", json={**payload, "inspect_only": True})
    assert checked["status"] == "not_required"
    assert checked["affected_count"] == 0
    assert fake.playlist(ARCHIVE) == before_inspect
    assert fake.playlist(TARGET) == confirmed_target
    assert fake.faults()[0]["fired"] == 1


def test_restore_removes_all_occurrences_and_already_absent_retry_does_not_write(alice, fake):
    _pair(alice)
    target = fake.uris(TARGET)
    repeated = target[0]
    _save(alice, ARCHIVE, [], [repeated, repeated, target[1]])
    payload = {"source_playlist_id": ARCHIVE, "target_track_uris": target, "track_uris": [repeated]}
    result = alice.ok("post", f"/playlist/{TARGET}/pair/finalize-restore", json=payload)
    assert result["status"] == "complete"
    assert result["affected_count"] == 2
    assert fake.uris(ARCHIVE) == [target[1]]
    before = fake.playlist(ARCHIVE)
    repeated_result = alice.ok("post", f"/playlist/{TARGET}/pair/finalize-restore", json=payload)
    assert repeated_result["status"] in ("complete", "not_required")
    assert fake.playlist(ARCHIVE) == before


def test_captured_archive_never_follows_a_relinked_pair(alice, fake):
    payload = _archive_intent(alice, fake)
    before = {playlist: fake.playlist(playlist) for playlist in (TARGET, ARCHIVE, OTHER)}
    alice.ok("delete", f"/playlist/{TARGET}/pair")
    _pair(alice, OTHER)
    response = alice.post(api_path(f"/playlist/{TARGET}/pair/archive"), json=payload)
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "source_changed"
    assert response.json()["retryable"] is False
    for playlist, unchanged in before.items():
        assert fake.playlist(playlist) == unchanged


# Exercise the actual review/result task view at several viewport widths.
@pytest.fixture(scope="module")
def recovery_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        yield browser
        browser.close()


class RecoveryVisit(Visit):
    """Only deliberately rejected source responses are expected errors."""

    expected_source_statuses = (502,)
    source_urls = {
        (APP + api_path(f'/playlist/{TARGET}/pair/archive')),
        (APP + api_path(f'/playlist/{TARGET}/pending-raids/finalize')),
    }

    def _check_response(self, response):
        if response.status in self.expected_source_statuses and response.url in self.source_urls:
            return
        super()._check_response(response)

    def _check_console(self, msg):
        if msg.type == "error" and any(msg.text.startswith(
            f"Failed to load resource: the server responded with a status of {status}"
        ) for status in self.expected_source_statuses) and msg.location.get("url") in self.source_urls:
            return
        super()._check_console(msg)


@pytest.mark.parametrize("width", [1440, 768, 375])
def test_named_source_recovery_is_reviewed_and_retried_in_browser(
    alice, fake, recovery_browser, tmp_path, width,
):
    _pair(alice)
    original = fake.uris(TARGET)
    visit = RecoveryVisit(recovery_browser, "desktop")
    visit.viewport = str(width)
    page = visit.page
    page.set_viewport_size({"width": width, "height": {1440: 900, 768: 1024, 375: 812}[width]})
    posts = []
    page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}?setup=archive", wait_until="networkidle")
        page.wait_for_function("() => workshopState.archivePair !== null")
        _close_tool(page, "archive-setup")
        _remove_row(page)
        page.click("#save-btn")
        page.locator("#workshop-save-panel").wait_for(state="visible")
        page.locator("#workshop-review-save").wait_for(state="visible")
        assert "Morning Mix" in page.locator("#workshop-review-summary").inner_text()
        assert "Morning Mix [Archive]" in page.locator("#workshop-save-panel").inner_text()
        assert fake.uris(TARGET) == original, "opening review wrote to the target"
        page.screenshot(path=tmp_path / f"review-{width}.png", full_page=True)
        fake.arm(operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=ARCHIVE)
        page.click("#workshop-review-save")
        page.wait_for_function("() => !workshopBusy() && workshopState.sourceRecovery.length === 1")
        assert fake.uris(TARGET) == original[1:]
        confirmed_target = fake.playlist(TARGET)
        assert fake.uris(ARCHIVE) == []
        assert page.locator("#workshop-review-save").is_hidden()
        assert page.locator("#workshop-review-refresh").is_hidden()
        if width < 1024:
            body = page.locator("#workshop-save-panel .sh-panel-body").bounding_box()
            footer = page.locator("#workshop-save-panel .sh-panel-footer").bounding_box()
            assert footer["y"] >= body["y"] + body["height"] - 1
        assert page.locator('[data-source-retry="archive"]').is_enabled()
        page.screenshot(path=tmp_path / f"attention-{width}.png", full_page=True)

        # Leave the results, make a separate local edit and discard it. The
        # saved target and its unfinished archive step must remain intact.
        page.click("#workshop-review-back")
        _remove_row(page)
        page.click("#undo-preview-btn")
        assert fake.playlist(TARGET) == confirmed_target
        assert page.evaluate("workshopState.sourceRecovery.length") == 1
        page.click("#workshop-open-results")
        page.click('[data-source-retry="archive"]')
        page.wait_for_function("() => !workshopBusy() && workshopState.sourceRecovery.length === 0")
        assert fake.uris(ARCHIVE) == [original[0]]
        assert fake.playlist(TARGET) == confirmed_target
        assert posts.count((APP + api_path(f'/workshop/{TARGET}/commit'))) == 1
        assert posts.count((APP + api_path(f'/playlist/{TARGET}/pair/archive'))) == 2
        assert not any("/unpromote" in path for path in posts)
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        page.screenshot(path=tmp_path / f"verified-{width}.png", full_page=True)
        assert visit.problems == []
    finally:
        visit.close()


@pytest.mark.parametrize("width", [1440, 768, 375])
def test_unknown_archive_browser_result_only_inspects_actual_saved_occurrence(
    alice, fake, recovery_browser, tmp_path, width,
):
    _pair(alice)
    original = fake.uris(TARGET)
    visit = RecoveryVisit(recovery_browser, "desktop")
    visit.viewport = str(width)
    page = visit.page
    page.set_viewport_size({"width": width, "height": {1440: 900, 768: 1024, 375: 812}[width]})
    dispatched = []
    archive_url = (APP + api_path(f'/playlist/{TARGET}/pair/archive'))

    def lose_first_acknowledgement(route):
        payload = route.request.post_data_json
        dispatched.append(payload)
        if payload.get("inspect_only"):
            route.continue_()
            return
        # A local test proxy loses the acknowledgement after the real app
        # actually applies the source write. Redirects cannot escape the fake.
        actual = route.fetch(max_redirects=0)
        assert actual.status == 200
        route.fulfill(status=502, content_type="text/plain", body="Test-only lost acknowledgement")

    page.route(archive_url, lose_first_acknowledgement)
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}?setup=archive", wait_until="networkidle")
        page.wait_for_function("() => workshopState.archivePair !== null")
        _close_tool(page, "archive-setup")
        _remove_row(page)
        page.click("#save-btn")
        page.locator("#workshop-review-save").wait_for(state="visible")
        page.click("#workshop-review-save")
        page.wait_for_function("() => !workshopBusy() && workshopState.sourceRecovery.length === 1")
        assert page.evaluate("workshopState.sourceRecovery[0].status") == "unknown"
        assert page.locator('[data-source-retry="archive"]').count() == 0
        assert fake.uris(ARCHIVE) == [original[0]]
        before = {playlist: fake.playlist(playlist) for playlist in (TARGET, ARCHIVE)}
        page.screenshot(path=tmp_path / f"unknown-{width}.png", full_page=True)
        page.click('[data-source-inspect="archive"]')
        page.wait_for_function("() => !workshopBusy() && workshopState.sourceRecovery.length === 0")
        assert len(dispatched) == 2 and dispatched[1]["inspect_only"] is True
        result_text = page.locator("#workshop-source-archive").inner_text()
        assert "Complete — verified" in result_text
        assert "no change needed" not in result_text
        for playlist, unchanged in before.items():
            assert fake.playlist(playlist) == unchanged
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        page.screenshot(path=tmp_path / f"inspected-{width}.png", full_page=True)
        assert visit.problems == []
    finally:
        visit.close()


def test_stale_target_recovery_controls_remain_usable_in_phone_browser(
    alice, fake, recovery_browser, tmp_path,
):
    original = fake.uris(TARGET)
    visit = Visit(recovery_browser, "phone")
    page = visit.page
    page.set_viewport_size({"width": 375, "height": 812})
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
        _remove_row(page)
        page.click("#save-btn")
        page.locator("#workshop-review-save").wait_for(state="visible")
        current = list(reversed(original))
        _save(alice, TARGET, original, current)
        page.click("#workshop-review-save")
        page.wait_for_function("() => !workshopBusy() && workshopState.saveAttention !== null")
        assert page.locator("#workshop-check-current").is_visible()
        assert page.locator("#workshop-use-current").is_visible()
        page.click("#workshop-check-current")
        page.wait_for_function("() => !workshopBusy() && workshopState.observedCurrent !== null")
        assert page.evaluate("workshopState.workingUris") == original[1:]
        assert fake.uris(TARGET) == current
        page.screenshot(path=tmp_path / "stale-target-phone.png", full_page=True)
        page.click("#workshop-use-current")
        assert page.evaluate("workshopState.workingUris") == current
        assert fake.uris(TARGET) == current
        page.click("#workshop-review-back")
        assert page.locator("#track-list").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    finally:
        visit.close()


@pytest.mark.parametrize("reject_removal", [False, True])
def test_shared_collection_archive_browser_orders_all_source_effects(
    alice, fake, recovery_browser, reject_removal,
):
    _pair(alice)
    alice.ok("post", f"/playlist/{TARGET}/raid-link", json={
        "create_new": False, "raid_playlist_id": ARCHIVE,
        "raid_playlist_name": "Morning Mix [Archive]",
    })
    alice.ok("post", f"/playlist/{TARGET}/raid-watch", json={
        "source_playlist_id": RAID_SOURCE, "source_playlist_name": "Bob's Finds",
        "auto_schedule": False,
    })
    alice.ok("post", f"/playlist/{TARGET}/raid-now", json={})
    pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
    assert len(pending) == 5
    original = fake.uris(TARGET)
    source = fake.uris(ARCHIVE)
    # Include a second occurrence: raid cleanup removes both before append.
    _save(alice, ARCHIVE, source, source + [source[0]])
    shared_baseline = fake.playlist(ARCHIVE)
    visit = RecoveryVisit(recovery_browser, "desktop")
    page = visit.page
    posts = []
    page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}?setup=archive", wait_until="networkidle")
        page.wait_for_function("() => workshopState.archivePair !== null")
        _close_tool(page, "archive-setup")
        _remove_row(page)
        _open_inbox(page)
        page.wait_for_function("() => pendingRaidsState.tracks.length === 5")
        page.click('[data-action-click="wk-047"]')
        page.click("#save-btn")
        page.locator("#workshop-review-save").wait_for(state="visible")
        assert fake.uris(TARGET) == original
        if reject_removal:
            fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=ARCHIVE)
        page.click("#workshop-review-save")
        page.wait_for_function("() => !workshopBusy()")
        target_saved = fake.playlist(TARGET)
        assert target_saved["uris"] == original[1:] + [t["track_uri"] for t in pending]
        if reject_removal:
            assert page.evaluate("workshopState.sourceRecovery.length") == 2
            assert fake.playlist(ARCHIVE) == shared_baseline
            assert posts.count((APP + api_path(f'/playlist/{TARGET}/pair/archive'))) == 0
            assert page.locator('[data-source-retry="archive"]').count() == 0
            page.click('[data-source-retry="raids"]')
            page.wait_for_function("() => !workshopBusy() && workshopState.sourceRecovery.length === 1")
            assert fake.uris(ARCHIVE) == []
            page.click('[data-source-retry="archive"]')
            page.wait_for_function("() => !workshopBusy() && workshopState.sourceRecovery.length === 0")
        else:
            assert page.evaluate("workshopState.sourceRecovery.length") == 0
        assert fake.uris(ARCHIVE) == [original[0]]
        assert fake.playlist(TARGET) == target_saved
        assert posts.count((APP + api_path(f'/workshop/{TARGET}/commit'))) == 1
        assert posts.count((APP + api_path(f'/playlist/{TARGET}/pair/archive'))) == 1
        assert alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"] == []
        assert visit.problems == []
    finally:
        visit.close()


def test_bulk_captured_inbox_above_two_hundred_finalizes_without_target_replay(alice, fake):
    alice.ok("post", f"/playlist/{TARGET}/raid-link", json={
        "create_new": False, "raid_playlist_id": ARCHIVE,
    })
    source = alice.ok("post", f"/playlist/{TARGET}/raid-watch", json={
        "source_playlist_id": pid("alice-long-haul"),
        "source_playlist_name": "Long Haul", "auto_schedule": False,
    })["source"]
    alice.ok("put", f"/playlist/{TARGET}/raid-source-count", json={
        "source_id": source["id"], "raid_count": 100,
    })
    for _ in range(3):
        alice.ok("post", f"/playlist/{TARGET}/raid-now", json={})
    pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
    assert len(pending) == 250
    baseline = fake.uris(TARGET)
    desired = baseline + [track["track_uri"] for track in pending]
    _save(alice, TARGET, baseline, desired)
    saved_target = fake.playlist(TARGET)
    result = alice.ok("post", f"/playlist/{TARGET}/pending-raids/finalize-all", json={
        "source_playlist_id": ARCHIVE, "target_track_uris": desired,
        "track_ids": [track["id"] for track in pending],
    })
    assert result["status"] == "complete"
    assert result["promoted_count"] == 250
    assert fake.playlist(TARGET) == saved_target
    assert fake.uris(ARCHIVE) == []
    assert alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"] == []


class CsrfRecoveryVisit(RecoveryVisit):
    expected_source_statuses = (400, 502)


def test_security_rejection_keeps_archive_retry_known_unchanged(
    alice, fake, recovery_browser,
):
    _pair(alice)
    original = fake.uris(TARGET)
    visit = CsrfRecoveryVisit(recovery_browser, "desktop")
    page = visit.page
    archive_url = (APP + api_path(f'/playlist/{TARGET}/pair/archive'))
    requests = []
    page.on("request", lambda req: requests.append(req.url) if req.method == "POST" else None)
    try:
        visit.log_in()
        page.goto(f"{APP}/workshop/{TARGET}?setup=archive", wait_until="networkidle")
        page.wait_for_function("() => workshopState.archivePair !== null")
        _close_tool(page, "archive-setup")
        _remove_row(page)
        page.click("#save-btn")
        page.locator("#workshop-review-save").wait_for(state="visible")
        fake.arm(operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=ARCHIVE)
        page.click("#workshop-review-save")
        page.wait_for_function("() => !workshopBusy() && workshopState.sourceRecovery.length === 1")
        confirmed_target = fake.playlist(TARGET)
        assert confirmed_target["uris"] == original[1:]

        # Exercise real Flask-WTF rejection before the finalizer route runs.
        # Expiry itself is covered by signed expired-token route regressions.
        def omit_security_token(route):
            headers = {key: value for key, value in route.request.all_headers().items()
                       if key.lower() not in ("x-csrftoken", "x-csrf-token")}
            route.continue_(headers=headers)

        page.route(archive_url, omit_security_token)
        page.click('[data-source-retry="archive"]')
        page.wait_for_function("""() => !workshopBusy() &&
            workshopState.sourceRecovery[0].result.error_code === 'session_verification_expired'""")
        state = page.evaluate("""() => {
            const task = workshopState.sourceRecovery[0];
            return {status: task.status, retryable: task.retryable, uncertain: !!task.uncertainAppend};
        }""")
        assert state == {"status": "failed", "retryable": False, "uncertain": False}
        assert page.locator('[data-source-retry="archive"]').count() == 0
        message = page.locator("#workshop-source-archive").inner_text().lower()
        assert "this attempt" in message and "reload" in message
        assert fake.uris(ARCHIVE) == []
        assert fake.playlist(TARGET) == confirmed_target
        assert requests.count((APP + api_path(f'/workshop/{TARGET}/commit'))) == 1
        assert requests.count(archive_url) == 2
        assert visit.problems == []
    finally:
        visit.close()
