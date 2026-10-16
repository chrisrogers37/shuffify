"""Guided routine forms against the fake provider, including exact admission counts."""

import json
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP, pid
from tests.e2e.test_compatibility_and_dialogs import SIZES, _close_all_panels, _open_tool, _panel, _remove_row
from tests.e2e.test_compatibility_and_dialogs import open_session as open_session
from tests.e2e.test_direct_live_actions import direct_browser as direct_browser
from tests.e2e.test_live_operation_outcomes import _collection, _create_schedule, _registration
from tests.paths import api_path
from tests.routine_flow import add_playlist_source

TARGET = pid("alice-morning-mix")
TASK_PATH = f"/playlist/{TARGET}/routines/new"
TASK = f"{APP}{TASK_PATH}"
ACTIVATE = api_path(f"/playlist/{TARGET}/routines/activate")
REVIEW = api_path(f"/playlist/{TARGET}/routines/review")
CONTINUATION = "shuffify:react-routine-continuation:v1"


def _capture(page, path):
    page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    page.screenshot(path=path, full_page=True)


def _no_sideways_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def _focused_control_is_clear(page):
    page.wait_for_function("""() => {
        const r = document.activeElement.getBoundingClientRect();
        return r.top >= 0 && r.bottom <= innerHeight + 1;
    }""", timeout=5000)
    box = page.evaluate("""() => {
        const el = document.activeElement, r = el.getBoundingClientRect();
        return {top: r.top, bottom: r.bottom, height: innerHeight,
                outline: getComputedStyle(el).outlineStyle,
                shadow: getComputedStyle(el).boxShadow};
    }""")
    assert box["top"] >= 0 and box["bottom"] <= box["height"] + 1, box
    assert box["outline"] != "none" or box["shadow"] != "none", box


def _rows(alice):
    return alice.ok("get", f"/playlist/{TARGET}/routines/context")["routines"]


def _begin(page, operation="shuffle"):
    page.goto(TASK, wait_until="networkidle")
    page.get_by_label("Operation", exact=True).select_option(operation)
    expect(page.get_by_role("heading", name="Connections", exact=True)).to_be_visible()


def _review(page):
    with page.expect_response(APP + REVIEW) as response:
        page.get_by_role("button", name="Review routine", exact=True).click()
    assert response.value.status == 200, response.value.text()
    expect(page.get_by_role("heading", name="Review and activate", exact=True)).to_be_visible()
    return response.value.json()


def _activate(page):
    with page.expect_response(APP + ACTIVATE) as response:
        page.get_by_role("button", name=re.compile(r"^(Activate routine|Save routine)$")).click()
    assert response.value.status == 200, response.value.text()
    accepted = response.value.json()
    expect(page.get_by_role("heading", name=f"Routine #{accepted['schedule_id']} saved", exact=True)).to_be_visible()
    return accepted


def _use_existing(page, purpose, playlist):
    with page.expect_response(
        lambda response: response.request.method == "GET" and "/setup/playlists?refresh=1" in response.url
    ) as response:
        page.get_by_role("button", name=f"Refresh my playlists for {purpose}", exact=True).click()
    assert response.value.status == 200, response.value.text()
    page.get_by_label(f"Your playlists for {purpose}", exact=True).select_option(playlist)
    page.get_by_role("button", name=f"Use existing {purpose}", exact=True).click()


def _review_setup(page):
    path = api_path(f"/playlist/{TARGET}/setup/impact")
    with page.expect_response(APP + path) as response:
        page.get_by_role("button", name="Review playlist settings", exact=True).click()
    assert response.value.status == 200, response.value.text()
    expect(page.get_by_role("heading", name="Review shared impact", exact=True)).to_be_visible()
    return response.value.json()


def _save_setup(page):
    page.get_by_label("I understand these settings affect existing routines", exact=True).check()
    path = api_path(f"/playlist/{TARGET}/setup/settings")
    with page.expect_response(APP + path) as response:
        page.get_by_role("button", name="Save playlist settings", exact=True).click()
    assert response.value.status == 200, response.value.text()
    expect(page.locator("#main-content").get_by_text(
        "Playlist settings saved. These settings apply to existing routines on their next run.", exact=True
    )).to_be_visible()
    return response.value.json()


@pytest.mark.parametrize("width,height", SIZES)
def test_guided_create_keeps_choices_and_activates_once_after_read_only_review(
    alice, fake, open_session, tmp_path, width, height
):
    session = open_session(width, height)
    page = session.page
    before = fake.playlist(TARGET)
    _begin(page)
    assert set(
        page.locator("#routine-operation option").evaluate_all("options => options.map(option => option.value)")
    ) == {
        "raid",
        "shuffle",
        "raid_and_shuffle",
        "raid_and_drip",
        "drip",
        "rotate",
    }
    page.get_by_label("Shuffle algorithm", exact=True).select_option("BasicShuffle")
    page.get_by_label("keep first", exact=True).fill("2")
    page.get_by_label("Timing type", exact=True).select_option("weekly")
    page.get_by_label("Weekday", exact=True).select_option("fri")
    page.get_by_label("Local time", exact=True).fill("09:35")
    page.get_by_label("IANA timezone", exact=True).fill("America/New_York")
    expect(page.get_by_label("keep first", exact=True)).to_have_value("2")
    assert _rows(alice) == [] and session.writes("POST", ACTIVATE) == []
    assert session.writes("POST", api_path("/schedules/create")) == []
    _review(page)
    payload = json.loads(session.writes("POST", REVIEW)[0])
    assert payload["definition"]["timing"] == {
        "kind": "weekly",
        "weekday": "fri",
        "time": "09:35",
        "timezone": "America/New_York",
    }
    assert _rows(alice) == [] and fake.playlist(TARGET) == before
    _no_sideways_scroll(page)
    activate = page.get_by_role("button", name="Activate routine", exact=True)
    activate.focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    expect(activate).to_be_focused()
    _focused_control_is_clear(page)
    _capture(page, tmp_path / f"routine-review-{width}.png")
    with page.expect_response(APP + ACTIVATE) as response:
        activate.evaluate("button => { button.click(); button.click(); }")
    assert response.value.status == 200, response.value.text()
    accepted = response.value.json()
    expect(page.get_by_role("heading", name=f"Routine #{accepted['schedule_id']} saved", exact=True)).to_be_visible()
    assert len(session.writes("POST", ACTIVATE)) == 1
    assert accepted["state"] == "Active" and accepted["registration"]["status"] == "registered"
    saved = _rows(alice)
    assert len(saved) == 1 and saved[0]["schedule_id"] == accepted["schedule_id"]
    definition = saved[0]["definition"]
    assert definition["algorithm_params"] == {"keep_first": 2}
    assert definition["timezone"] == "America/New_York"
    assert definition["schedule_value"].split()[-1] == "fri"
    assert [int(part) for part in definition["schedule_value"].split()[:2]] == [35, 9]
    assert fake.playlist(TARGET) == before
    result_link = page.get_by_role("link", name="View exact routine", exact=True)
    result_link.focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    expect(result_link).to_be_focused()
    _focused_control_is_clear(page)
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"routine-created-{width}.png")
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_guided_duplicate_choice_edits_exact_paused_routine_without_resuming(
    alice, fake, open_session, tmp_path, width, height
):
    first = _create_schedule(alice, "shuffle", TARGET)["id"]
    second = _create_schedule(alice, "shuffle", TARGET)["id"]
    second_row = next(row for row in _rows(alice) if row["schedule_id"] == second)
    alice.ok(
        "put",
        f"/routines/{second}/state",
        json={
            "desired_enabled": False,
            "expected_revision": second_row["schedule_revision"],
        },
    )
    before = {row["schedule_id"]: row for row in _rows(alice)}
    session = open_session(width, height)
    page = session.page
    _begin(page)
    for sid in (first, second):
        expect(page.get_by_role("button", name=re.compile(rf"^Edit routine #{sid} ·"))).to_be_visible()
    # Read-only review reports the ambiguous candidates before an exact selection.
    with page.expect_response(APP + REVIEW) as response:
        page.get_by_role("button", name="Review routine", exact=True).click()
    assert response.value.status == 200, response.value.text()
    assert set(response.value.json()["candidate_schedule_ids"]) == {first, second}
    expect(
        page.locator("#main-content").get_by_text(
            "Choose an exact routine or Create another routine, then review again.", exact=True
        )
    ).to_be_visible()
    assert session.writes("POST", ACTIVATE) == []
    page.get_by_role("button", name=re.compile(rf"^Edit routine #{second} ·")).click()
    page.get_by_label("keep first", exact=True).fill("3")
    _review(page)
    request = json.loads(session.writes("POST", REVIEW)[-1])
    assert request["schedule_id"] == second
    assert "desired_enabled" not in request and "timing" not in request["definition"]
    accepted = _activate(page)
    assert accepted["schedule_id"] == second and accepted["state"] == "Paused"
    assert _registration(second) is None
    after = {row["schedule_id"]: row for row in _rows(alice)}
    assert len(after) == 2 and after[first]["schedule_revision"] == before[first]["schedule_revision"]
    assert after[second]["definition"]["algorithm_params"] == {"keep_first": 3}
    assert after[second]["definition"]["timezone"] == before[second]["definition"]["timezone"]
    assert after[second]["definition"]["schedule_value"] == before[second]["definition"]["schedule_value"]
    assert len(session.writes("POST", ACTIVATE)) == 1
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"routine-exact-paused-{width}.png")
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_guided_unknown_activation_replays_identical_key_and_does_not_create_twice(
    alice, fake, open_session, tmp_path, width, height
):
    session = open_session(width, height)
    page = session.page
    _begin(page)
    _review(page)
    requests = []

    def lose_first_reply(route):
        requests.append(route.request.post_data_json)
        response = route.fetch()
        assert response.status == 200, response.text()
        if len(requests) == 1:
            route.fulfill(status=200, content_type="application/json", body="")
        else:
            route.fulfill(response=response)

    page.route(APP + ACTIVATE, lose_first_reply)
    page.get_by_role("button", name="Activate routine", exact=True).click()
    expect(page.get_by_role("button", name="Check activation", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Activate routine", exact=True)).to_be_disabled()
    assert len(_rows(alice)) == 1
    sid = _rows(alice)[0]["schedule_id"]
    registered = _registration(sid)
    assert page.evaluate("key => sessionStorage.getItem(key)", CONTINUATION) is None
    _capture(page, tmp_path / f"routine-unknown-{width}.png")
    page.get_by_role("button", name="Check activation", exact=True).click()
    expect(page.get_by_role("heading", name=f"Routine #{sid} saved", exact=True)).to_be_visible()
    assert len(requests) == 2 and requests[0] == requests[1]
    assert len(_rows(alice)) == 1 and _rows(alice)[0]["schedule_id"] == sid
    assert _registration(sid) == registered
    _no_sideways_scroll(page)
    session.assert_clean()


def test_settings_logout_clears_same_tab_routine_continuation_before_same_user_login(alice, fake, open_session):
    session = open_session(375, 812)
    page = session.page
    page.goto(f"{APP}/settings", wait_until="networkidle")
    page.evaluate(
        "key => { sessionStorage.setItem(key, 'local routine choices'); "
        "sessionStorage.setItem('unrelated-test', 'keep'); }",
        CONTINUATION,
    )
    page.get_by_role("button", name="Log out", exact=True).click()
    page.wait_for_url(f"{APP}/")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("key => sessionStorage.getItem(key)", CONTINUATION) is None
    assert page.evaluate("sessionStorage.getItem('unrelated-test')") == "keep"
    session.visit.log_in()
    assert page.evaluate("key => sessionStorage.getItem(key)", CONTINUATION) is None
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_setup_return_preserves_local_choices_and_cancel_keeps_explicit_shared_save(
    alice, fake, open_session, tmp_path, width, height
):
    source = pid("bob-finds")
    add_playlist_source(alice, TARGET, source)
    existing = _create_schedule(alice, "raid", TARGET)["id"]
    registration = _registration(existing)
    before_playlists = {item["id"] for item in fake.playlists()}
    session = open_session(width, height)
    page = session.page
    _begin(page, "raid_and_shuffle")
    page.get_by_label("Shuffle algorithm", exact=True).select_option("BasicShuffle")
    page.get_by_label("keep first", exact=True).fill("4")
    page.get_by_role("button", name="Open playlist setup", exact=True).click()
    page.wait_for_url(f"{APP}/playlist/{TARGET}/setup?return_target=routine")
    continuation = page.evaluate("key => JSON.parse(sessionStorage.getItem(key))", CONTINUATION)
    assert continuation["ownerId"] == "alice" and continuation["targetId"] == TARGET
    assert continuation["draft"]["job_type"] == "raid_and_shuffle"
    assert continuation["draft"]["algorithm_params"] == {"keep_first": 4}
    assert not {"intent", "request_key", "definition", "sources", "activationRequest"} & continuation.keys()
    expect(page.get_by_role("link", name="Return to routine draft", exact=True)).to_be_visible()
    name = "Guided routine collection"
    page.get_by_label("Name a new collection playlist", exact=True).fill(name)
    create_path = api_path(f"/playlist/{TARGET}/setup/spotify-playlists")
    with page.expect_response(APP + create_path) as response:
        page.get_by_role("button", name="Create collection playlist in Spotify", exact=True).click()
    assert response.value.status == 202, response.value.text()
    operation_id = response.value.json()["operation_id"]
    assert alice.wait_operation(api_path(f"/operations/{operation_id}"), operation_id)["state"] == "succeeded"
    expect(page.locator("#setup-collection")).to_contain_text(f"Created: {name}")
    created = [item for item in fake.playlists() if item["id"] not in before_playlists]
    assert len(created) == 1 and created[0]["name"] == name
    page.get_by_role("button", name="Use created collection", exact=True).click()
    reviewed = _review_setup(page)
    expect(page.get_by_role("button", name="Save playlist settings", exact=True)).to_be_disabled()
    _save_setup(page)
    settings = json.loads(session.writes("PUT", api_path(f"/playlist/{TARGET}/setup/settings"))[0])
    assert settings["changes"] == [{"op": "link_collection", "playlist_id": created[0]["id"]}]
    assert settings["acknowledgment"] == reviewed["acknowledgment"]
    page.get_by_role("link", name="Return to routine draft", exact=True).click()
    page.wait_for_url(re.compile(re.escape(TASK) + r"\?origin=playlist$"))
    expect(page.get_by_label("keep first", exact=True)).to_have_value("4")
    expect(page.get_by_text(f"Collection: {name}", exact=True)).to_be_visible()
    assert session.writes("POST", REVIEW) == [] and session.writes("POST", ACTIVATE) == []
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"routine-setup-return-{width}.png")
    page.get_by_role("link", name="Back to playlist", exact=True).click()
    dialog = page.get_by_role("dialog", name="Leave this page?", exact=True)
    dialog.get_by_role("button", name="Stay", exact=True).click()
    expect(page.get_by_label("keep first", exact=True)).to_have_value("4")
    page.get_by_role("link", name="Back to playlist", exact=True).click()
    page.get_by_role("dialog").get_by_role("button", name="Leave page", exact=True).click()
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
    alice, fake, open_session, tmp_path, width, height
):
    collection = _collection(alice)
    alice.ok("put", f"/playlist/{TARGET}/raid-link", json={"drip_enabled": True, "drip_count": 2})
    sid = _create_schedule(alice, "raid_and_drip", TARGET)["id"]
    fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=collection)
    operation = alice.queued(f"/schedules/{sid}/run")
    assert operation["state"] == "rolled_back", operation
    execution_id = operation["id"]
    assert operation["result"]["execution_id"] == execution_id
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    session = open_session(width, height)
    page = session.page
    page.goto(f"{APP}/activity#run-{execution_id}", wait_until="networkidle")
    expect(page.locator(".sh-activity-run")).to_have_count(1)
    row = page.locator(f"#run-{execution_id}")
    expect(row).to_be_focused()
    result_text = row.locator(".sh-result").inner_text()
    assert "5 collected for review" in result_text
    assert result_text.index("Collect — Completed") < result_text.index("Add to playlist —")
    assert "99 tracks" not in result_text
    page.locator("#activity-operation-filter").select_option("shuffle")
    expect(page.get_by_text("0 of 1 loaded runs shown", exact=True)).to_be_visible()
    page.locator("#activity-operation-filter").select_option("all")
    page.locator("#activity-event-filter").select_option("manual")
    events = alice.ok("get", "/activity")["activities"]
    manual_ids = {event["id"] for event in events if event["triggered_by"] == "manual"}
    visible = page.locator(".sh-activity-event:visible")
    assert visible.count() > 0
    assert {int(item.get_attribute("id").removeprefix("event-")) for item in visible.all()} == manual_ids
    for item in visible.all():
        expect(item.locator(".sh-activity-manual")).to_have_text("Manual action")
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"activity-composite-{width}.png")
    page.goto(f"{APP}/routines?routine_id={sid}&view=history", wait_until="networkidle")
    history = page.locator(f"#routine-{sid}-history")
    expect(history).to_be_visible()
    expect(history.locator(".sh-result")).to_have_count(1)
    assert history.locator(".sh-result").inner_text() == result_text
    _no_sideways_scroll(page)
    _capture(page, tmp_path / f"routine-history-{width}.png")
    assert session.writes("POST", api_path(f"/schedules/{sid}/run")) == []
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_registration_attention_retries_only_the_exact_saved_routine(
    alice, fake, open_session, tmp_path, width, height
):
    session = open_session(width, height)
    page = session.page
    _begin(page)
    _review(page)

    def show_registration_failure(route):
        response = route.fetch()
        assert response.status == 200, response.text()
        body = response.json()
        body["state"] = "Saved—registration needs attention"
        body["registration"] = {"status": "needs_attention", "next_run_at": None}
        route.fulfill(response=response, json=body)

    page.route(APP + ACTIVATE, show_registration_failure)
    accepted = _activate(page)
    sid = accepted["schedule_id"]
    expect(
        page.get_by_text("The definition is saved. Check its scheduling status in Routines.", exact=True)
    ).to_be_visible()
    _capture(page, tmp_path / f"routine-registration-attention-{width}.png")
    page.get_by_role("link", name="View exact routine", exact=True).click()
    page.wait_for_url(f"{APP}/routines#routine-{sid}")
    expect(page.locator(f"#routine-{sid}-title")).to_be_focused()
    # Registration management belongs to the saved exact routine; the current UI exposes its Pause/Resume controls.
    card = page.locator(f"#routine-{sid}")
    for label, enabled in (("Pause", False), ("Resume", True)):
        card.get_by_role("button", name=label, exact=True).click()
        expect(card.locator(".sh-badge")).to_have_text("Active" if enabled else "Paused")
    assert len(session.writes("POST", ACTIVATE)) == 1
    states = session.writes("PUT", api_path(f"/routines/{sid}/state"))
    assert len(states) == 2 and [json.loads(body)["desired_enabled"] for body in states] == [False, True]
    assert all(json.loads(body)["expected_revision"] for body in states)
    assert [row["schedule_id"] for row in _rows(alice)] == [sid]
    assert _registration(sid) is not None
    session.assert_clean()


@pytest.mark.parametrize("width,height", SIZES)
def test_workshop_routine_entry_resolves_draft_before_leaving(alice, fake, open_session, width, height):
    session = open_session(width, height)
    page = session.page
    original = fake.playlist(TARGET)
    page.goto(f"{APP}/workshop/{TARGET}", wait_until="networkidle")
    _remove_row(page)
    _open_tool(page, "routines")
    page.evaluate("window.__sameRoutineEntryDocument = true")
    _panel(page).get_by_role("link", name="Create routine", exact=True).click()
    dialog = page.get_by_role("dialog", name="Keep your local draft?", exact=True)
    dialog.get_by_role("button", name="Stay", exact=True).click()
    assert page.evaluate("window.__sameRoutineEntryDocument") is True
    assert fake.playlist(TARGET) == original
    _close_all_panels(page)
    expect(page.locator(".sh-workshop-track")).to_have_count(len(original["uris"]) - 1)
    _open_tool(page, "routines")
    _panel(page).get_by_role("link", name="Create routine", exact=True).click()
    page.get_by_role("dialog").get_by_role("button", name="Discard", exact=True).click()
    page.wait_for_url(f"{TASK}?from=playlist")
    expect(page.get_by_role("heading", name="Create a routine", exact=True)).to_be_visible()
    assert fake.playlist(TARGET) == original and _rows(alice) == []
    assert session.writes("POST", ACTIVATE) == []
    session.assert_clean()


@pytest.mark.parametrize("operation", ["raid", "shuffle", "raid_and_shuffle", "raid_and_drip", "drip", "rotate"])
def test_all_six_guided_operations_preserve_expert_options_and_search_only_sources(
    alice, fake, open_session, operation
):
    session = open_session(375, 812)
    page = session.page
    before = fake.playlists()
    page.goto(f"{APP}/playlist/{TARGET}/setup", wait_until="networkidle")
    page.get_by_label("Add by search words", exact=True).fill("late night jazz")
    page.get_by_role("button", name="Add search source", exact=True).click()
    _use_existing(page, "collection", pid("alice-old-faithful"))
    _use_existing(page, "archive", pid("alice-morning-archive"))
    _review_setup(page)
    _save_setup(page)
    page.get_by_label("Enable adding collected tracks", exact=True).check()
    _review_setup(page)
    _save_setup(page)
    _begin(page, operation)
    params = {}
    if operation in ("shuffle", "raid_and_shuffle"):
        page.get_by_label("Shuffle algorithm", exact=True).select_option("BasicShuffle")
        page.get_by_label("keep first", exact=True).fill("2")
        params = {"keep_first": 2}
    elif operation in ("drip", "raid_and_drip"):
        page.get_by_label("Tracks per run (optional override)", exact=True).fill("3")
        params = {"drip_count": 3}
    elif operation == "rotate":
        page.get_by_label("Tracks to rotate", exact=True).fill("2")
        page.get_by_label("Playlist size cap", exact=True).fill("18")
        page.get_by_label("Protected first tracks", exact=True).fill("1")
        params = {"rotation_mode": "swap", "rotation_count": 2, "target_size": 18, "protect_count": 1}
    context = alice.ok("get", f"/playlist/{TARGET}/routines/context")
    assert len(context["prerequisites"]["sources"]) == 1
    assert context["prerequisites"]["sources"][0]["search_query"] == "late night jazz"
    assert context["capabilities"][operation]["available"], context["capabilities"][operation]
    if operation.startswith("raid"):
        expect(page.get_by_text("1 saved source", exact=True)).to_be_visible()
    page.get_by_label("Timing type", exact=True).select_option("advanced")
    page.get_by_label("Five-field cron expression", exact=True).fill("15 8 * * 2")
    page.get_by_label("IANA timezone", exact=True).fill("America/New_York")
    _review(page)
    payload = json.loads(session.writes("POST", REVIEW)[0])
    assert payload["definition"]["algorithm_params"] == params
    assert payload["definition"]["timing"] == {
        "kind": "advanced",
        "expression": "15 8 * * 2",
        "timezone": "America/New_York",
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
