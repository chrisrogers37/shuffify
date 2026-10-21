"""Source finalizers observe actual fake Spotify state without replaying targets.

Run only through the canonical harness. Every playlist/account is fictional.
"""

import time

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.e2e.conftest import APP, pid
from tests.e2e.test_pages import CHROMIUM_ARGS, Visit
from tests.paths import api_path
from tests.routine_flow import add_playlist_source

TARGET = pid("alice-morning-mix")
ARCHIVE = pid("alice-morning-archive")
OTHER = pid("alice-old-faithful")
RAID_SOURCE = pid("bob-finds")


def _pair(alice, archive=ARCHIVE):
    alice.write(
        f"/playlist/{TARGET}/pair",
        json={
            "archive_playlist_id": archive,
            "archive_playlist_name": "Browser-supplied name is not authoritative",
            "production_playlist_name": "Morning Mix",
        },
    )


def _save(alice, playlist, baseline, desired):
    return alice.write(
        f"/workshop/{playlist}/commit",
        json={
            "baseline_track_uris": baseline,
            "track_uris": desired,
        },
    )


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
    rejected = alice.queued(f"/playlist/{TARGET}/pair/archive", json=payload)
    assert rejected["state"] == "failed", rejected
    result = rejected["result"]
    assert (result["status"], result["retryable"]) == ("failed", True)
    assert fake.uris(ARCHIVE) == []
    assert fake.playlist(TARGET) == confirmed_target

    completed = alice.write(f"/playlist/{TARGET}/pair/archive", json=payload)
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
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="slow", playlist_id=ARCHIVE, delay=31)
    response = alice.queued(f"/playlist/{TARGET}/pair/archive", json=payload)
    assert response["state"] == "needs_attention", response
    assert (response["result"]["status"], response["result"]["retryable"]) == ("unknown", False)
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
    result = alice.write(f"/playlist/{TARGET}/pair/finalize-restore", json=payload)
    assert result["status"] == "complete"
    assert result["affected_count"] == 2
    assert fake.uris(ARCHIVE) == [target[1]]
    before = fake.playlist(ARCHIVE)
    repeated_result = alice.write(f"/playlist/{TARGET}/pair/finalize-restore", json=payload)
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
        (APP + api_path(f"/playlist/{TARGET}/pair/archive")),
        (APP + api_path(f"/playlist/{TARGET}/pending-raids/finalize")),
    }

    def _check_response(self, response):
        if response.status in self.expected_source_statuses and response.url in self.source_urls:
            return
        super()._check_response(response)

    def _check_console(self, msg):
        if (
            msg.type == "error"
            and any(
                msg.text.startswith(f"Failed to load resource: the server responded with a status of {status}")
                for status in self.expected_source_statuses
            )
            and msg.location.get("url") in self.source_urls
        ):
            return
        super()._check_console(msg)


def _ui():
    # Imported after collection to keep shared fixture modules independent.
    from tests.e2e import test_playlist_workspace

    return test_playlist_workspace


def _start(visit, width=1440, *, archive=True):
    page = visit.page
    visit.viewport = str(width)
    page.set_viewport_size({"width": width, "height": {1440: 900, 768: 1024, 375: 812}[width]})
    visit.log_in()
    page.goto(f"{APP}/workshop/{TARGET}" + ("?setup=archive" if archive else ""), wait_until="networkidle")
    if archive:
        expect(_ui()._panel(page).get_by_role("heading", name="Archive setup", exact=True)).to_be_visible()
        expect(_ui()._panel(page)).to_contain_text(ARCHIVE)
        _ui()._close_tool(page)
    expect(_ui()._rows(page)).to_have_count(40)
    return page


def _save_review(page):
    with page.expect_response(
        lambda response: (
            response.url == APP + api_path(f"/workshop/{TARGET}/commit") and response.request.method == "POST"
        )
    ) as reply:
        _ui()._panel(page).get_by_role("button", name="Save to Spotify", exact=True).click()
    assert reply.value.status == 202, reply.value.text()
    expect(page.get_by_role("region", name="Retained operation")).to_contain_text(
        "Playlist saved to Spotify!", timeout=45000
    )
    _ui()._close_tool(page)
    return reply.value.json()


def _source_task(page, label="Finish archiving removed tracks", tool="Archive"):
    _ui()._open_tool(page, tool)
    card = (
        _ui()
        ._panel(page)
        .locator("article.sh-sources-card")
        .filter(has=page.get_by_role("heading", name=label, exact=True))
    )
    expect(card).to_be_visible()
    return card


def _review_retry(card):
    card.get_by_role("button", name="Review failed cleanup again", exact=True).click()
    expect(card.get_by_role("button", name="Run reviewed cleanup", exact=True)).to_be_visible()


@pytest.mark.parametrize("width", [1440, 768, 375])
def test_named_source_recovery_is_reviewed_and_retried_in_browser(alice, fake, recovery_browser, tmp_path, width):
    _pair(alice)
    original = fake.uris(TARGET)
    visit = RecoveryVisit(recovery_browser, "desktop")
    posts = []
    visit.page.on(
        "request",
        lambda req: (
            posts.append(
                (
                    req.url,
                    req.post_data_json,
                    req.headers.get("idempotency-key"),
                )
            )
            if req.method == "POST"
            else None
        ),
    )
    try:
        page = _start(visit, width)
        _ui()._remove_row(page)
        review = _ui()._review(page)
        assert "Morning Mix" in review and "Morning Mix [Archive]" in review
        assert fake.uris(TARGET) == original
        page.screenshot(path=tmp_path / f"review-{width}.png", full_page=True)
        fake.arm(operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=ARCHIVE)
        _save_review(page)
        card = _source_task(page)
        expect(card.get_by_role("button", name="Review failed cleanup again", exact=True)).to_be_visible(timeout=45000)
        confirmed_target = fake.playlist(TARGET)
        assert confirmed_target["uris"] == original[1:] and fake.uris(ARCHIVE) == []
        page.screenshot(path=tmp_path / f"attention-{width}.png", full_page=True)
        before_posts = list(posts)
        _review_retry(card)
        assert posts == before_posts, "fresh source review dispatched before confirmation"
        card.get_by_role("button", name="Run reviewed cleanup", exact=True).click()
        expect(card).to_contain_text("1 selected · complete", timeout=45000)
        assert fake.uris(ARCHIVE) == [original[0]] and fake.playlist(TARGET) == confirmed_target
        commits = [body for url, body, _ in posts if url == APP + api_path(f"/workshop/{TARGET}/commit")]
        archives = [body for url, body, _ in posts if url == APP + api_path(f"/playlist/{TARGET}/pair/archive")]
        keys = [key for url, _, key in posts if url == APP + api_path(f"/playlist/{TARGET}/pair/archive")]
        assert len(commits) == 1 and len(archives) == 2 and archives[0] == archives[1]
        assert keys[0] and keys[1] and keys[0] != keys[1]
        assert not any("/unpromote" in url for url, _, _ in posts)
        assert _ui()._no_sideways_scroll(page)
        page.screenshot(path=tmp_path / f"verified-{width}.png", full_page=True)
        assert visit.problems == []
    finally:
        visit.close()


@pytest.mark.parametrize("width", [1440, 768, 375])
def test_unknown_archive_browser_result_only_inspects_actual_saved_occurrence(
    alice,
    fake,
    recovery_browser,
    tmp_path,
    width,
):
    _pair(alice)
    original = fake.uris(TARGET)
    visit = RecoveryVisit(recovery_browser, "desktop")
    page = visit.page
    dispatched = []
    archive_url = APP + api_path(f"/playlist/{TARGET}/pair/archive")

    def lose_first_acknowledgement(route):
        payload = route.request.post_data_json
        dispatched.append(payload)
        if payload.get("inspect_only"):
            route.continue_()
            return
        actual = route.fetch(max_redirects=0)
        assert actual.status == 202
        accepted = actual.json()
        settled = alice.wait_operation(actual.headers["location"], accepted["operation_id"])
        assert settled["state"] == "succeeded"
        route.fulfill(status=502, content_type="text/plain", body="Local proxy lost acknowledgement")

    try:
        page.route(archive_url, lose_first_acknowledgement)
        _start(visit, width)
        _ui()._remove_row(page)
        _ui()._review(page)
        _save_review(page)
        card = _source_task(page)
        expect(card).to_contain_text("1 selected · unknown", timeout=45000)
        assert fake.uris(TARGET) == original[1:] and fake.uris(ARCHIVE) == [original[0]]
        before = {playlist: fake.playlist(playlist) for playlist in (TARGET, ARCHIVE)}
        card.get_by_role("button", name="Inspect captured source", exact=True).click()
        inspection = card.get_by_role("region", name="Separate source inspection")
        expect(inspection).to_contain_text(
            "This observation does not replace the saved operation receipt", timeout=15000
        )
        expect(card).to_contain_text("1 selected · unknown")
        expect(card.get_by_role("button", name="Check the same request", exact=True)).to_be_visible()
        expect(card.get_by_role("button", name="Review failed cleanup again", exact=True)).to_have_count(0)
        assert len(dispatched) == 2 and dispatched[1]["inspect_only"] is True
        for playlist, unchanged in before.items():
            assert fake.playlist(playlist) == unchanged
        page.screenshot(path=tmp_path / f"inspected-{width}.png", full_page=True)
        assert visit.problems == []
    finally:
        visit.close()


def test_stale_target_recovery_controls_remain_usable_in_phone_browser(alice, fake, recovery_browser, tmp_path):
    original = fake.uris(TARGET)
    visit = Visit(recovery_browser, "phone")
    try:
        page = _start(visit, 375, archive=False)
        _ui()._remove_row(page)
        _ui()._review(page)
        current = list(reversed(original))
        _save(alice, TARGET, original, current)
        with page.expect_response(
            lambda response: (
                response.url == APP + api_path(f"/workshop/{TARGET}/commit") and response.request.method == "POST"
            )
        ) as reply:
            _ui()._panel(page).get_by_role("button", name="Save to Spotify", exact=True).click()
        assert reply.value.status == 202
        expect(_ui()._panel(page).get_by_role("region", name="Retained operation")).to_contain_text(
            "failed", timeout=45000
        )
        _ui()._panel(page).get_by_role("button", name="Check current Spotify", exact=True).click()
        expect(_ui()._panel(page)).to_contain_text("Current order differs from this draft.")
        assert _ui()._uris(page) == original[1:] and fake.uris(TARGET) == current
        page.screenshot(path=tmp_path / "stale-target-phone.png", full_page=True)
        _ui()._panel(page).get_by_role("button", name="Use current Spotify as baseline", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="Use current order", exact=True).click()
        expect(_ui()._rows(page)).to_have_count(len(current))
        assert _ui()._uris(page) == current and fake.uris(TARGET) == current
        _ui()._close_tool(page)
        assert _ui()._no_sideways_scroll(page) and visit.problems == []
    finally:
        visit.close()


@pytest.mark.parametrize("reject_removal", [False, True])
def test_shared_collection_archive_browser_orders_all_source_effects(alice, fake, recovery_browser, reject_removal):
    _pair(alice)
    alice.write(f"/playlist/{TARGET}/raid-link", json={"create_new": False, "raid_playlist_id": ARCHIVE})
    add_playlist_source(alice, TARGET, RAID_SOURCE)
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
    assert len(pending) == 5
    original, source = fake.uris(TARGET), fake.uris(ARCHIVE)
    _save(alice, ARCHIVE, source, source + [source[0]])
    shared_baseline = fake.playlist(ARCHIVE)
    visit = RecoveryVisit(recovery_browser, "desktop")
    posts = []
    visit.page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
    try:
        page = _start(visit)
        _ui()._remove_row(page)
        _ui()._open_inbox(page)
        expect(_ui()._panel(page).locator("[data-pending-id]")).to_have_count(5)
        _ui()._panel(page).get_by_role("button", name="Add all to draft", exact=True).click()
        _ui()._close_tool(page)
        _ui()._review(page)
        assert fake.uris(TARGET) == original
        if reject_removal:
            fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=ARCHIVE)
        _save_review(page)
        target_saved = fake.playlist(TARGET)
        assert target_saved["uris"] == original[1:] + [row["track_uri"] for row in pending]
        raids = _source_task(page, "Finish pending tracks", "Inbox")
        if reject_removal:
            expect(raids).to_contain_text("Review failed cleanup again", timeout=45000)
            expect(raids.get_by_role("button", name="Review failed cleanup again", exact=True)).to_be_visible(
                timeout=45000
            )
            assert fake.playlist(ARCHIVE) == shared_baseline
            assert posts.count(APP + api_path(f"/playlist/{TARGET}/pair/archive")) == 0
            _review_retry(raids)
            raids.get_by_role("button", name="Run reviewed cleanup", exact=True).click()
            expect(raids).to_contain_text("5 selected · complete", timeout=45000)
        else:
            expect(raids).to_contain_text("5 selected · complete", timeout=45000)
        _ui()._close_tool(page)
        archive = _source_task(page)
        # Only an explicit retry chain may need its reserved dependent confirmed again.
        if reject_removal:
            expect(archive.get_by_role("button", name="Run reviewed cleanup", exact=True)).to_be_visible()
            archive.get_by_role("button", name="Run reviewed cleanup", exact=True).click()
        expect(archive).to_contain_text("1 selected · complete", timeout=45000)
        assert fake.uris(ARCHIVE) == [original[0]] and fake.playlist(TARGET) == target_saved
        assert posts.count(APP + api_path(f"/workshop/{TARGET}/commit")) == 1
        assert posts.count(APP + api_path(f"/playlist/{TARGET}/pair/archive")) == 1
        assert alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"] == []
        assert visit.problems == []
    finally:
        visit.close()


def test_bulk_captured_inbox_above_two_hundred_finalizes_without_target_replay(alice, fake):
    alice.write(
        f"/playlist/{TARGET}/raid-link",
        json={
            "create_new": False,
            "raid_playlist_id": ARCHIVE,
        },
    )
    source = add_playlist_source(alice, TARGET, pid("alice-long-haul"))
    alice.ok(
        "put",
        f"/playlist/{TARGET}/raid-source-count",
        json={
            "source_id": source["id"],
            "raid_count": 100,
        },
    )
    for _ in range(3):
        alice.write(f"/playlist/{TARGET}/raid-now", json={})
    pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
    assert len(pending) == 250
    baseline = fake.uris(TARGET)
    desired = baseline + [track["track_uri"] for track in pending]
    _save(alice, TARGET, baseline, desired)
    saved_target = fake.playlist(TARGET)
    result = alice.write(
        f"/playlist/{TARGET}/pending-raids/finalize",
        json={
            "source_playlist_id": ARCHIVE,
            "target_track_uris": desired,
            "track_ids": [track["id"] for track in pending],
        },
    )
    assert result["status"] == "complete"
    assert result["promoted_count"] == 250
    assert fake.playlist(TARGET) == saved_target
    assert fake.uris(ARCHIVE) == []
    assert alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"] == []


class CsrfRecoveryVisit(RecoveryVisit):
    expected_source_statuses = (400, 502)


def test_security_rejection_keeps_archive_retry_known_unchanged(alice, fake, recovery_browser):
    _pair(alice)
    original = fake.uris(TARGET)
    visit = CsrfRecoveryVisit(recovery_browser, "desktop")
    archive_url = APP + api_path(f"/playlist/{TARGET}/pair/archive")
    requests = []
    visit.page.on("request", lambda req: requests.append(req.url) if req.method == "POST" else None)
    try:
        page = _start(visit)
        _ui()._remove_row(page)
        _ui()._review(page)
        fake.arm(operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=ARCHIVE)
        _save_review(page)
        card = _source_task(page)
        expect(card.get_by_role("button", name="Review failed cleanup again", exact=True)).to_be_visible(timeout=45000)
        confirmed_target = fake.playlist(TARGET)
        assert confirmed_target["uris"] == original[1:]
        _review_retry(card)

        def omit_security_token(route):
            headers = {
                key: value
                for key, value in route.request.all_headers().items()
                if key.lower() not in ("x-csrftoken", "x-csrf-token")
            }
            route.continue_(headers=headers)

        page.route(archive_url, omit_security_token)
        card.get_by_role("button", name="Run reviewed cleanup", exact=True).click()
        expect(card).to_contain_text("session_verification_expired")
        expect(card.get_by_role("button", name="Review failed cleanup again", exact=True)).to_be_visible()
        expect(card.get_by_role("button", name="Check the same request", exact=True)).to_have_count(0)
        assert fake.uris(ARCHIVE) == [] and fake.playlist(TARGET) == confirmed_target
        assert requests.count(APP + api_path(f"/workshop/{TARGET}/commit")) == 1
        assert requests.count(archive_url) == 2
        assert visit.problems == []
    finally:
        visit.close()
