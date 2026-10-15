"""Guided routine tasks in the canonical fake-only browser, including exact write counts."""

import json

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, pid
from tests.e2e.test_compatibility_and_dialogs import SIZES
from tests.e2e.test_compatibility_and_dialogs import open_session as open_session
from tests.e2e.test_direct_live_actions import _open_tool, _remove_row
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.e2e.test_live_operation_outcomes import _collection, _create_schedule, _registration
from tests.e2e.test_playlist_setup import _review as _review_setup
from tests.e2e.test_playlist_setup import _save as _save_setup
from tests.e2e.test_playlist_setup import _use_existing
from tests.e2e.test_playlist_workspace import _no_sideways_scroll, _scroll_settled
from tests.paths import api_path

TARGET = pid("alice-morning-mix")
TASK_PATH = f"/playlist/{TARGET}/routines/new"
TASK = f"{APP}{TASK_PATH}"
ACTIVATE = api_path(f"/playlist/{TARGET}/routines/activate")
REVIEW = api_path(f"/playlist/{TARGET}/routines/review")
CONTINUATION = "shuffify:routine-continuation:v1"


def _capture(page, path):
    # Full-page captures place fixed navigation at the current scroll offset. Settle
    # at the top for reviewable evidence, without changing the focused control.
    page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    _scroll_settled(page)
    page.screenshot(path=path, full_page=True)


def _focused_control_is_clear(page):
    _scroll_settled(page)
    box = page.evaluate("""() => {
        const el = document.activeElement, footer = document.getElementById('routine-footer');
        const r = el.getBoundingClientRect(), f = footer.getBoundingClientRect();
        return {name: el.id, inside: footer.contains(el), top: r.top, bottom: r.bottom,
                footerTop: f.top, footerBottom: f.bottom, height: innerHeight,
                fixed: getComputedStyle(footer).position === 'fixed'};
    }""")
    assert box["top"] >= 0 and box["bottom"] <= box["height"] + 1, box
    if box["fixed"] and not box["inside"]:
        assert box["bottom"] <= box["footerTop"] + 1, box
    return box["name"]


def _rows(alice):
    return alice.ok("get", f"/playlist/{TARGET}/routines/context")["routines"]


def _begin(page, operation="shuffle"):
    page.goto(TASK, wait_until="networkidle")
    page.click('[data-goal="all"]')
    expect(page.locator("#routine-connections-step")).to_be_visible()
    page.select_option("#routine-operation", operation)


def _review(page):
    if page.locator("#routine-next").is_visible():
        page.click("#routine-next")
    expect(page.locator("#routine-timing-step")).to_be_visible()
    with page.expect_response(f"{APP}{REVIEW}") as response:
        page.click("#routine-review")
    assert response.value.status == 200, response.value.text()
    expect(page.locator("#routine-review-heading")).to_be_focused()


def _activate(page):
    with page.expect_response(f"{APP}{ACTIVATE}") as response:
        page.click("#routine-activate")
    assert response.value.status == 200, response.value.text()
    expect(page.locator("#routine-result-heading")).to_be_focused()
    return response.value.json()


@pytest.mark.parametrize("width,height", SIZES)
def test_guided_create_keeps_choices_and_activates_once_after_read_only_review(
    alice, fake, open_session, tmp_path, width, height,
):
    session = open_session(width, height)
    page = session.page
    before = fake.playlist(TARGET)
    _begin(page)
    assert set(page.locator("#routine-operation option").evaluate_all(
        "options => options.map(option => option.value)"
    )) == {"raid", "shuffle", "raid_and_shuffle", "raid_and_drip", "drip", "rotate"}
    page.select_option("#routine-algorithm", "BasicShuffle")
    page.fill("#routine-param-keep_first", "2")
    page.click("#routine-next")
    page.select_option("#routine-timing-kind", "weekly")
    page.select_option("#routine-weekday", "fri")
    page.fill("#routine-clock", "09:35")
    page.fill("#routine-timezone", "America/New_York")
    page.click("#routine-back")
    expect(page.locator("#routine-param-keep_first")).to_have_value("2")
    assert _rows(alice) == []
    assert session.writes("POST", ACTIVATE) == []
    assert not session.writes("POST", api_path("/schedules/create"))
    _review(page)
    payload = json.loads(session.writes("POST", REVIEW)[0])
    assert payload["definition"]["timing"] == {
        "kind": "weekly", "weekday": "fri", "time": "09:35", "timezone": "America/New_York",
    }
    assert _rows(alice) == [] and fake.playlist(TARGET) == before
    _no_sideways_scroll(page)
    assert _focused_control_is_clear(page) == "routine-review-heading"
    page.keyboard.press("Tab")
    _focused_control_is_clear(page)
    _capture(page, tmp_path / f"routine-review-{width}.png")
    # Two synchronous click events exercise the single-flight boundary before a reply.
    with page.expect_response(f"{APP}{ACTIVATE}") as response:
        page.locator("#routine-activate").evaluate("button => { button.click(); button.click(); }")
    assert response.value.status == 200
    expect(page.locator("#routine-result-heading")).to_be_focused()
    accepted = response.value.json()
    assert len(session.writes("POST", ACTIVATE)) == 1
    assert accepted["state"] == "Active"
    assert accepted["registration"]["status"] == "registered"
    saved = _rows(alice)
    assert len(saved) == 1 and saved[0]["schedule_id"] == accepted["schedule_id"]
    definition = saved[0]["definition"]
    assert definition["algorithm_params"] == {"keep_first": 2}
    assert definition["timezone"] == "America/New_York"
    assert definition["schedule_value"].split()[-1] == "fri"
    assert [int(part) for part in definition["schedule_value"].split()[:2]] == [35, 9]
    assert fake.playlist(TARGET) == before
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"routine-created-{width}.png")
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_guided_duplicate_choice_edits_exact_paused_routine_without_resuming(
    alice, fake, open_session, tmp_path, width, height,
):
    first = _create_schedule(alice, "shuffle", TARGET)["id"]
    second = _create_schedule(alice, "shuffle", TARGET)["id"]
    second_row = next(row for row in _rows(alice) if row["schedule_id"] == second)
    alice.ok("put", f"/routines/{second}/state", json={
        "desired_enabled": False, "expected_revision": second_row["schedule_revision"],
    })
    before = {row["schedule_id"]: row for row in _rows(alice)}
    session = open_session(width, height)
    page = session.page
    _begin(page)
    for sid in (first, second):
        expect(page.locator(f'[data-action-click="routine.choose"][data-schedule-id="{sid}"]')).to_be_visible()
    page.click("#routine-next")
    expect(page.locator("#routine-choice-error")).to_be_visible()
    assert session.writes("POST", REVIEW) == []
    page.click(f'[data-action-click="routine.choose"][data-schedule-id="{second}"]')
    page.fill("#routine-param-keep_first", "3")
    _review(page)
    request = json.loads(session.writes("POST", REVIEW)[0])
    assert request["schedule_id"] == second
    assert "desired_enabled" not in request and "timing" not in request["definition"]
    accepted = _activate(page)
    assert accepted["schedule_id"] == second and accepted["state"] == "Paused"
    assert _registration(second) is None
    after = {row["schedule_id"]: row for row in _rows(alice)}
    assert len(after) == 2
    assert after[first]["schedule_revision"] == before[first]["schedule_revision"]
    assert after[second]["definition"]["algorithm_params"] == {"keep_first": 3}
    assert after[second]["definition"]["timezone"] is None
    assert after[second]["definition"]["schedule_value"] == before[second]["definition"]["schedule_value"]
    assert len(session.writes("POST", ACTIVATE)) == 1
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"routine-exact-paused-{width}.png")
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_guided_unknown_activation_replays_identical_key_and_does_not_create_twice(
    alice, fake, open_session, tmp_path, width, height,
):
    session = open_session(width, height)
    page = session.page
    _begin(page)
    _review(page)
    requests = []

    def lose_first_reply(route):
        requests.append(route.request.post_data_json)
        response = route.fetch()
        assert response.status == 200
        if len(requests) == 1:
            route.fulfill(status=200, content_type="application/json", body="")
        else:
            route.fulfill(response=response)

    page.route(f"{APP}{ACTIVATE}", lose_first_reply)
    page.click("#routine-activate")
    expect(page.locator("#routine-check")).to_be_visible()
    expect(page.locator("#routine-activate")).to_be_hidden()
    assert len(_rows(alice)) == 1
    sid = _rows(alice)[0]["schedule_id"]
    registered = _registration(sid)
    assert page.evaluate("key => sessionStorage.getItem(key)", CONTINUATION) is None
    _capture(page, tmp_path / f"routine-unknown-{width}.png")
    page.click("#routine-check")
    expect(page.locator("#routine-result-heading")).to_be_focused()
    assert len(requests) == 2 and requests[0] == requests[1]
    assert len(_rows(alice)) == 1 and _rows(alice)[0]["schedule_id"] == sid
    assert _registration(sid) == registered
    _no_sideways_scroll(page)
    session.assert_clean()


def test_settings_logout_clears_same_tab_routine_continuation_before_same_user_login(
    alice, fake, open_session,
):
    session = open_session(375, 812)
    page = session.page
    page.goto(f"{APP}/settings", wait_until="networkidle")
    page.evaluate("key => { sessionStorage.setItem(key, 'local routine choices'); "
                  "sessionStorage.setItem('unrelated-test', 'keep'); }", CONTINUATION)
    page.locator("#settings-logout-form button").click()
    page.wait_for_url(f"{APP}/")
    assert page.evaluate("key => sessionStorage.getItem(key)", CONTINUATION) is None
    assert page.evaluate("sessionStorage.getItem('unrelated-test')") == "keep"
    session.visit.log_in()
    assert page.evaluate("key => sessionStorage.getItem(key)", CONTINUATION) is None
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_setup_return_preserves_local_choices_and_cancel_keeps_explicit_shared_save(
    alice, fake, open_session, tmp_path, width, height,
):
    source = pid("bob-finds")
    alice.ok("post", f"/playlist/{TARGET}/raid-watch", json={
        "source_playlist_id": source, "source_playlist_name": "Bob's Finds", "auto_schedule": False,
    })
    existing = _create_schedule(alice, "raid", TARGET)["id"]
    registration = _registration(existing)
    before_playlists = {item["id"] for item in fake.playlists()}
    session = open_session(width, height)
    page = session.page
    _begin(page, "raid_and_shuffle")
    page.select_option("#routine-algorithm", "BasicShuffle")
    page.fill("#routine-param-keep_first", "4")
    page.click('[data-action-click="routine.setup"][data-section="collection"]')
    page.wait_for_url(f"{APP}/playlist/{TARGET}/setup?*")
    continuation = page.evaluate("key => JSON.parse(sessionStorage.getItem(key))", CONTINUATION)
    assert continuation["targetId"] == TARGET and continuation["operation"] == "raid_and_shuffle"
    assert continuation["options"]["params"]["keep_first"] == 4
    assert not {"intent", "request_key", "definition", "sources", "activationRequest"} & continuation.keys()
    expect(page.locator("#setup-back")).to_have_text("Back to routine")
    name = "Guided routine collection"
    page.fill("#setup-collection-create-name", name)
    create_path = api_path(f"/playlist/{TARGET}/setup/spotify-playlists")
    with page.expect_response(f"{APP}{create_path}") as response:
        page.click("#setup-collection-create")
    assert response.value.status == 201
    created = [item for item in fake.playlists() if item["id"] not in before_playlists]
    assert len(created) == 1 and created[0]["name"] == name
    _review_setup(page)
    expect(page.locator("#setup-ack")).to_be_visible()
    page.check("#setup-ack")
    _save_setup(page)
    page.click("#setup-back")
    # Once linked and saved, leaving setup needs no discard confirmation.
    page.wait_for_url(TASK)
    expect(page.locator("#routine-connections-step")).to_be_visible()
    expect(page.locator("#routine-param-keep_first")).to_have_value("4")
    expect(page.locator("#routine-collection-summary")).to_contain_text(name)
    assert session.writes("POST", REVIEW) == [] and session.writes("POST", ACTIVATE) == []
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"routine-setup-return-{width}.png")
    page.click("#routine-close")
    expect(page.locator("#app-confirm")).to_be_visible()
    page.click("#app-confirm-ok")
    page.wait_for_url(f"{APP}/workshop/{TARGET}")
    assert [row["schedule_id"] for row in _rows(alice)] == [existing]
    assert _registration(existing) == registration
    context = alice.ok("get", f"/playlist/{TARGET}/setup/context")["context"]
    assert context["collection"]["playlist_id"] == created[0]["id"]
    assert fake.playlist(created[0]["id"])["name"] == name
    assert len(session.writes("POST", create_path)) == 1
    assert len(session.writes("PUT", api_path(f"/playlist/{TARGET}/setup/settings"))) == 1
    assert page.evaluate("key => sessionStorage.getItem(key)", CONTINUATION) is None
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_activity_and_exact_history_share_composite_results_and_keep_manual_events(
    alice, fake, open_session, tmp_path, width, height,
):
    collection = _collection(alice)
    alice.ok("put", f"/playlist/{TARGET}/raid-link", json={"drip_enabled": True, "drip_count": 2})
    sid = _create_schedule(alice, "raid_and_drip", TARGET)["id"]
    fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=collection)
    operation = alice.queued(f"/schedules/{sid}/run")
    assert operation["state"] == "rolled_back", operation
    execution_id = operation["result"]["result"]["execution_id"]
    # A separate manual collection event has no saved routine association.
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    session = open_session(width, height)
    page = session.page
    page.goto(f"{APP}/activity", wait_until="networkidle")
    expect(page.locator(".sh-activity-run")).to_have_count(1)
    row = page.locator(".sh-activity-run")
    assert json.loads(row.get_attribute("data-execution"))["id"] == execution_id
    result_text = row.locator(".sh-result").inner_text()
    assert "5 collected for review" in result_text
    assert result_text.index("Collect — Completed") < result_text.index("Add to playlist —")
    assert "99 tracks" not in result_text
    page.select_option("#activity-operation-filter", "shuffle")
    expect(page.locator("#activity-run-no-match")).to_be_visible()
    expect(page.locator("#activity-run-count")).to_have_text("0 of 1 loaded runs shown")
    page.select_option("#activity-operation-filter", "all")
    page.select_option("#activity-event-filter", "manual")
    visible_events = page.locator(".sh-activity-event:visible")
    assert visible_events.count() > 0
    for item in visible_events.all():
        assert item.get_attribute("data-triggered-by") == "manual"
        expect(item.locator(".sh-activity-manual")).to_be_visible()
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"activity-composite-{width}.png")
    page.goto(f"{APP}/schedules?routine_id={sid}&view=history", wait_until="networkidle")
    history = page.locator(f"#history-{sid}")
    expect(history).to_be_visible()
    expect(history.locator(".sh-result")).to_have_count(1)
    assert history.locator(".sh-result").inner_text() == result_text
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"routine-history-{width}.png")
    assert session.writes("POST", api_path(f"/schedules/{sid}/run")) == []
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_registration_attention_retries_only_the_exact_saved_routine(
    alice, fake, open_session, tmp_path, width, height,
):
    session = open_session(width, height)
    page = session.page
    _begin(page)
    _review(page)

    def show_registration_failure(route):
        response = route.fetch()
        assert response.status == 200
        body = response.json()
        body["state"] = "Saved—registration needs attention"
        body["registration"] = {"status": "needs_attention", "next_run_at": None}
        route.fulfill(response=response, json=body)

    # A controlled accepted DTO exercises the UI recovery while the actual harness
    # state endpoint still verifies the saved exact ID and registration.
    page.route(f"{APP}{ACTIVATE}", show_registration_failure)
    accepted = _activate(page)
    sid = accepted["schedule_id"]
    expect(page.locator("#routine-result-details")).to_contain_text("scheduling needs attention")
    expect(page.locator("#routine-try-registration")).to_be_visible()
    _capture(page, tmp_path / f"routine-registration-attention-{width}.png")
    page.click("#routine-try-registration")
    expect(page.locator("#routine-result-details")).to_contain_text("Active")
    expect(page.locator("#routine-try-registration")).to_be_hidden()
    assert len(session.writes("POST", ACTIVATE)) == 1
    state = session.writes("PUT", api_path(f"/routines/{sid}/state"))
    assert len(state) == 1 and json.loads(state[0]) == {
        "desired_enabled": True, "expected_revision": accepted["schedule_revision"],
    }
    assert [row["schedule_id"] for row in _rows(alice)] == [sid]
    assert _registration(sid) is not None
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_workshop_routine_entry_resolves_draft_before_leaving(
    alice, fake, open_session, width, height,
):
    session = open_session(width, height)
    page = session.page
    original = fake.playlist(TARGET)
    page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
    _remove_row(page)
    _open_tool(page, "routines")
    page.evaluate("window.__sameRoutineEntryDocument = true")
    page.click("#workshop-create-routine")
    expect(page.locator("#live-operation-leave")).to_be_visible()
    page.click("#live-operation-leave-stay")
    assert page.evaluate("window.__sameRoutineEntryDocument") is True
    assert page.evaluate("workshopState.isDirty") is True
    assert fake.playlist(TARGET) == original
    page.click("#workshop-create-routine")
    page.click("#live-operation-leave-discard")
    page.wait_for_url(f"{TASK}?from=playlist")
    expect(page.locator("#routine-goal-step")).to_be_visible()
    assert fake.playlist(TARGET) == original and _rows(alice) == []
    assert session.writes("POST", ACTIVATE) == []
    session.assert_clean()


@pytest.mark.parametrize("operation", ["raid", "shuffle", "raid_and_shuffle", "raid_and_drip", "drip", "rotate"])
def test_all_six_guided_operations_preserve_expert_options_and_search_only_sources(
    alice, fake, open_session, operation,
):
    session = open_session(375, 812)
    page = session.page
    before = fake.playlists()
    page.goto(f"{APP}/playlist/{TARGET}/setup", wait_until="networkidle")
    page.fill("#setup-add-search-input", "late night jazz")
    page.click("#setup-add-search")
    _use_existing(page, "collection", pid("alice-old-faithful"))
    page.check("#setup-drip-enabled")
    _use_existing(page, "archive", pid("alice-morning-archive"))
    _review_setup(page)
    page.check("#setup-ack")
    _save_setup(page)
    _begin(page, operation)
    params = {}
    if operation in ("shuffle", "raid_and_shuffle"):
        page.select_option("#routine-algorithm", "BasicShuffle")
        page.fill("#routine-param-keep_first", "2")
        params = {"keep_first": 2}
    elif operation in ("drip", "raid_and_drip"):
        page.fill("#routine-drip-count", "3")
        params = {"drip_count": 3}
    elif operation == "rotate":
        page.select_option("#routine-rotation-mode", "swap")
        page.fill("#routine-rotation-count", "2")
        page.fill("#routine-target-size", "18")
        page.fill("#routine-protect-count", "1")
        params = {"rotation_mode": "swap", "rotation_count": 2, "target_size": 18, "protect_count": 1}
    if operation.startswith("raid"):
        expect(page.locator("#routine-sources")).to_contain_text("late night jazz")
    page.click("#routine-next")
    page.select_option("#routine-timing-kind", "advanced")
    page.fill("#routine-expression", "15 8 * * 2")
    page.fill("#routine-timezone", "America/New_York")
    _review(page)
    payload = json.loads(session.writes("POST", REVIEW)[0])
    assert payload["definition"]["algorithm_params"] == params
    assert payload["definition"]["timing"] == {
        "kind": "advanced", "expression": "15 8 * * 2", "timezone": "America/New_York",
    }
    assert fake.playlists() == before and _rows(alice) == []
    _activate(page)
    saved = _rows(alice)
    assert len(saved) == 1
    assert saved[0]["definition"]["job_type"] == operation
    assert saved[0]["definition"]["algorithm_params"] == params
    assert saved[0]["definition"]["source_playlist_ids"] == []
    assert saved[0]["definition"]["schedule_type"] == "cron"
    assert saved[0]["definition"]["timezone"] == "America/New_York"
    assert len(session.writes("POST", ACTIVATE)) == 1 and fake.playlists() == before
    _no_sideways_scroll(page)
    session.assert_clean()
