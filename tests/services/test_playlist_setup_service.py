"""Shared playlist setup: revision, context, impact and the one-transaction Save."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

from shuffify.enums import ActivityType
from shuffify.models.db import ActivityLog, PlaylistPair, RaidPlaylistLink, Schedule, UpstreamSource, db
from shuffify.schemas import SetupImpactRequest, SetupSaveRequest
from shuffify.services import activity_log_service
from shuffify.services import playlist_setup_service as setup
from shuffify.services.executors.raid_executor import _load_sources
from shuffify.services.live_review_scope import STEP_ROWS
from shuffify.services.playlist_lock import playlist_configuration_lock, playlist_lock
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistNotFoundError
from shuffify.services.playlist_setup_service import PlaylistSetupService
from shuffify.services.user_service import UserService
from shuffify.spotify.exceptions import SpotifyForbiddenError, SpotifyRateLimitError
from tests.playlist_setup_fixtures import (
    TARGET,
    SetupSpotify,
    add_link,
    add_pair,
    add_routine,
    add_source,
    assert_never_scheduled,
)

pytestmark = pytest.mark.usefixtures("no_scraping")


@pytest.fixture
def user(db_app):
    return UserService.upsert_from_spotify({"id": "user123", "display_name": "Test User", "images": []}).user


@pytest.fixture
def other_user(db_app):
    return UserService.upsert_from_spotify({"id": "user456", "display_name": "Other", "images": []}).user


@pytest.fixture
def api():
    return SetupSpotify()


def revision(user, target=TARGET):
    return setup.configuration_revision(setup.read_state(user.id, target))


def review(api, user, *changes, target=TARGET, **extra):
    request = SetupImpactRequest.model_validate(
        {"configuration_revision": revision(user, target), "changes": list(changes), **extra}
    )
    return PlaylistSetupService.impact(api, user, target, request)


def saved(api, user, *changes, target=TARGET, acknowledgment=None, **extra):
    """Review the changes, then Save them with the review's acknowledgment: (review, save) replies."""
    reviewed, status = review(api, user, *changes, target=target)
    assert status == 200, reviewed
    body = {
        "configuration_revision": reviewed["configuration_revision"], "changes": list(changes),
        "acknowledgment": reviewed["acknowledgment"] if acknowledgment is None else acknowledgment, **extra,
    }
    return reviewed, PlaylistSetupService.save(api, user, target, SetupSaveRequest.model_validate(body))


def save_body(reviewed, changes, **extra):
    return SetupSaveRequest.model_validate({
        "configuration_revision": reviewed["configuration_revision"], "changes": list(changes),
        "acknowledgment": reviewed["acknowledgment"], **extra,
    })


def add_playlist(playlist_id):
    return {"op": "add_playlist_source", "playlist_id": playlist_id}


def add_search(query):
    return {"op": "add_search_source", "search_query": query}


def routine_ids(schedule):
    return db.session.get(Schedule, schedule.id).source_playlist_ids


# --- Revision and state digest --------------------------------------------------


def test_the_revision_ignores_run_tracking_names_and_timestamps(user):
    schedule = add_routine(user, "raid", ["ext1"])
    source = add_source(user, "ext1", name="Indie Finds")
    link = add_link(user)
    add_pair(user)
    before = revision(user)
    stamped = schedule.updated_at
    schedule.last_status, schedule.last_run_at, schedule.last_error = "success", datetime.now(timezone.utc), None
    schedule.target_playlist_name = "Renamed"
    source.last_resolved_at, source.last_resolve_status, source.last_track_count = datetime.now(timezone.utc), "ok", 9
    source.source_name, link.raid_playlist_name = "Renamed source", "Renamed collection"
    db.session.commit()
    assert db.session.get(Schedule, schedule.id).updated_at != stamped
    assert revision(user) == before


@pytest.mark.parametrize("section, mutate", [
    ("sources", lambda rows: setattr(rows["source"], "raid_count", 9)),
    ("sources", lambda rows: setattr(rows["source"], "source_playlist_id", "ext2")),
    ("sources", lambda rows: setattr(rows["source"], "source_type", "own")),
    ("sources", lambda rows: setattr(rows["search"], "search_query", "jazz")),
    ("sources", lambda rows: db.session.delete(rows["search"])),
    ("collection", lambda rows: setattr(rows["link"], "raid_playlist_id", "col2")),
    ("collection", lambda rows: setattr(rows["link"], "drip_enabled", True)),
    ("collection", lambda rows: setattr(rows["link"], "drip_count", 7)),
    ("archive", lambda rows: setattr(rows["pair"], "archive_playlist_id", "arc2")),
    ("archive", lambda rows: setattr(rows["pair"], "auto_archive_on_remove", False)),
    ("routines", lambda rows: setattr(rows["routine"], "is_enabled", False)),
    ("routines", lambda rows: setattr(rows["routine"], "schedule_value", "weekly")),
    ("routines", lambda rows: setattr(rows["routine"], "schedule_type", "cron")),
    ("routines", lambda rows: setattr(rows["routine"], "source_playlist_ids", ["ext1", "ext2"])),
    ("routines", lambda rows: setattr(rows["routine"], "algorithm_name", "BasicShuffle")),
    ("routines", lambda rows: setattr(rows["routine"], "algorithm_params", {"drip_count": 2})),
    ("routines", lambda rows: setattr(rows["routine"], "job_type", "raid_and_shuffle")),
])
def test_the_revision_and_one_section_digest_change_with_each_definitional_field(user, section, mutate):
    rows = {
        "source": add_source(user, "ext1"), "search": add_source(user, query="lofi"),
        "link": add_link(user), "pair": add_pair(user), "routine": add_routine(user, "raid", ["ext1"]),
    }
    state = setup.read_state(user.id, TARGET)
    before, digests = setup.configuration_revision(state), setup.section_digests(state)
    mutate(rows)
    db.session.commit()
    state = setup.read_state(user.id, TARGET)
    after = setup.section_digests(state)
    assert setup.configuration_revision(state) != before
    assert [name for name in setup.SECTIONS if after[name] != digests[name]] == [section]


def test_read_state_is_fresh_and_scoped_to_the_user_and_target(user, other_user):
    schedule = add_routine(user, "raid", ["ext1"])
    add_source(user, "ext1")
    add_source(user, "ext2", target="elsewhere")
    add_source(other_user, "ext2")
    add_routine(other_user, "raid", ["ext2"])
    add_routine(user, "raid", ["ext2"], target="elsewhere")
    state = setup.read_state(user.id, TARGET)
    assert [s.source_playlist_id for s in state.sources] == ["ext1"]
    assert [s.id for s in state.schedules] == [schedule.id]
    db.session.execute(text("UPDATE schedules SET is_enabled = 0 WHERE id = :id"), {"id": schedule.id})
    assert setup.read_state(user.id, TARGET).schedules[0].is_enabled is False


def test_read_state_reads_the_link_pair_and_sources_fresh_as_a_run_does(user):
    source, link, pair = add_source(user, "ext1"), add_link(user), add_pair(user)
    state = setup.read_state(user.id, TARGET)
    assert (state.link, state.pair, state.sources) == (link, pair, (source,))
    db.session.execute(text("UPDATE raid_playlist_links SET drip_count = 9 WHERE id = :id"), {"id": link.id})
    db.session.execute(text("UPDATE playlist_pairs SET auto_archive_on_remove = 0 WHERE id = :id"), {"id": pair.id})
    db.session.execute(text("UPDATE upstream_sources SET raid_count = 8 WHERE id = :id"), {"id": source.id})
    state = setup.read_state(user.id, TARGET)
    assert (state.link.drip_count, state.pair.auto_archive_on_remove, state.sources[0].raid_count) == (9, False, 8)


def test_the_state_digest_ignores_row_ids_and_routine_list_order(user):
    add_routine(user, "raid", ["ext1", "ext2"])
    first = add_source(user, "ext1")
    add_link(user)
    state = setup.read_state(user.id, TARGET)
    digest, before = setup.state_digest(state), setup.configuration_revision(state)
    db.session.delete(first)
    db.session.commit()
    add_source(user, "ext1")
    link = RaidPlaylistLink.query.one()
    db.session.delete(link)
    db.session.commit()
    add_link(user)
    Schedule.query.one().source_playlist_ids = ["ext2", "ext1"]
    db.session.commit()
    state = setup.read_state(user.id, TARGET)
    assert setup.configuration_revision(state) != before
    assert setup.state_digest(state) == digest


def test_the_state_digest_ignores_the_order_sources_were_added_in(user):
    first, second = add_source(user, "ext1"), add_source(user, query="lofi")
    digest = setup.state_digest(setup.read_state(user.id, TARGET))
    db.session.delete(first)
    db.session.delete(second)
    db.session.commit()
    add_source(user, query="lofi")
    add_source(user, "ext1")
    assert setup.state_digest(setup.read_state(user.id, TARGET)) == digest


# --- Context ---------------------------------------------------------------------------


def test_the_context_names_sources_links_routines_and_limits(user, api, scheduler_spies):
    collect = add_routine(user, "raid", ["ext1", "ghost1"], kind="cron", value="30 7 * * *")
    adds = add_routine(user, "raid_and_drip", ["ghost1"], enabled=False, params={"drip_count": 2})
    shuffle = add_routine(user, "shuffle", ["ghost2"], value="every_6h")
    playlist = add_source(user, "ext1", name="Indie Finds", count=7)
    search = add_source(user, query="lofi", name=None)
    add_link(user, "col1", name=None, enabled=True, count=4)
    add_pair(user, "arc1", auto=False)
    context = PlaylistSetupService.get_context(user, TARGET, api)
    assert context["version"] == 1 and context["target"] == {"id": TARGET, "name": "Morning Mix"}
    assert context["sources"] == [
        {"id": playlist.id, "type": "external", "playlist_id": "ext1", "search_query": None,
         "name": "Indie Finds", "raid_count": 7},
        {"id": search.id, "type": "search_query", "playlist_id": None, "search_query": "lofi",
         "name": "lofi", "raid_count": 5},
    ]
    assert context["routine_only_sources"] == [{"playlist_id": "ghost1", "routine_ids": [collect.id, adds.id]}]
    assert context["collection"] == {"playlist_id": "col1", "name": "col1", "drip_enabled": True, "drip_count": 4}
    assert context["archive"] == {"playlist_id": "arc1", "name": "Archive One", "auto_archive_on_remove": False}
    assert context["routines"] == [
        {"id": collect.id, "job_type": "raid", "operation": "Collect",
         "timing_text": "Every day at 07:30 timezone unavailable",
         "is_enabled": True, "uses": {"sources": True, "collection": True, "drip": False, "archive": True},
         "drip_count_override": None},
        {"id": adds.id, "job_type": "raid_and_drip", "operation": "Collect and add", "timing_text": "Every day",
         "is_enabled": False, "uses": {"sources": True, "collection": True, "drip": True, "archive": True},
         "drip_count_override": 2},
        {"id": shuffle.id, "job_type": "shuffle", "operation": "Shuffle", "timing_text": "Every 6 hours",
         "is_enabled": True, "uses": {"sources": False, "collection": False, "drip": False, "archive": False},
         "drip_count_override": None},
    ]
    assert context["limits"] == {"max_sources": 10, "raid_count": [1, 100], "drip_count": [1, 50]}
    state = setup.read_state(user.id, TARGET)
    assert context["configuration_revision"] == setup.configuration_revision(state)
    assert context["section_digests"] == setup.section_digests(state)
    assert context["state_digest"] == setup.state_digest(state)
    # The target's editability is one lean read: its name, owner and collaborative flag, never cached.
    assert api.writes == [] and api.calls == [("get_playlist_summary", TARGET)]
    assert not db.session.dirty and not db.session.new and ActivityLog.query.count() == 0
    assert_never_scheduled(scheduler_spies)


@pytest.mark.parametrize("job_type, uses", [
    ("raid", {"sources": True, "collection": True, "drip": False, "archive": True}),
    ("raid_and_shuffle", {"sources": True, "collection": True, "drip": False, "archive": True}),
    ("raid_and_drip", {"sources": True, "collection": True, "drip": True, "archive": True}),
    ("drip", {"sources": False, "collection": True, "drip": True, "archive": False}),
    ("shuffle", {"sources": False, "collection": False, "drip": False, "archive": False}),
    ("rotate", {"sources": False, "collection": False, "drip": False, "archive": True}),
])
def test_what_each_job_type_uses_is_what_its_steps_read(user, api, job_type, uses):
    add_routine(user, job_type, params={"drip_count": 4})
    (routine,) = PlaylistSetupService.get_context(user, TARGET, api)["routines"]
    assert routine["uses"] == uses
    assert routine["drip_count_override"] == (4 if uses["drip"] else None)


def test_a_routines_own_drip_count_is_reported_as_its_runs_add_it(user, api):
    for params in ({"drip_count": None}, {"drip_count": "lots"}, {"drip_count": "7"}, {"drip_count": 0}, None):
        add_routine(user, "drip", params=params)
    routines = PlaylistSetupService.get_context(user, TARGET, api)["routines"]
    assert [routine["drip_count_override"] for routine in routines] == [None, None, 7, 1, None]
    add_link(user)
    reply, _ = review(api, user, {"op": "set_drip", "drip_count": 5})
    assert [routine["reason"] for routine in reply["unaffected_routines"]] == [
        "keeps its own count of None per run", "keeps its own count of lots per run",
        "keeps its own count of 7 per run", "keeps its own count of 1 per run",
    ]
    # Adding is off on this collection, so the count is not claimed as what saved runs add.
    assert [routine["effects"] for routine in reply["affected_routines"]] == [
        ["will add 5 per run when adding is on"]]


def test_each_step_that_reads_an_asset_has_its_wording():
    for role, row in (("collection", "link"), ("archive", "pair")):
        assert set(setup.ASSET_EFFECTS[role]) == {step for step, rows in STEP_ROWS.items() if row in rows}


def test_the_context_refuses_a_target_the_user_cannot_edit(user, api):
    api.playlists["refused"] = {"name": "Refused"}
    api.read_failures["refused"] = SpotifyForbiddenError("API error 403")
    for target, refused, message in (
        ("missing", PlaylistNotFoundError, setup.TARGET_NOT_FOUND),
        ("not-an-id", PlaylistNotFoundError, setup.TARGET_NOT_FOUND),
        ("foreign", PlaylistAccessError, setup.TARGET_NOT_EDITABLE),
        ("refused", PlaylistAccessError, setup.TARGET_NOT_EDITABLE),
    ):
        with pytest.raises(refused, match=message):
            PlaylistSetupService.get_context(user, target, api)
    assert [call for call in api.calls if "not-an-id" in call] == []
    assert PlaylistSetupService.get_context(user, "collab", api)["target"] == {"id": "collab", "name": "Shared Collab"}


# --- Impact ------------------------------------------------------------------------------


def affected_by_id(reply):
    return {routine["id"]: routine for routine in reply["affected_routines"]}


def test_a_source_change_affects_every_raid_family_routine_paused_ones_too(user, api, scheduler_spies):
    raid = add_routine(user, "raid", ["ext1"])
    composite = add_routine(user, "raid_and_shuffle", ["ext1"], enabled=False, kind="cron", value="0 6 * * 0")
    drips = add_routine(user, "raid_and_drip", [])
    add_routine(user, "shuffle")
    add_routine(user, "drip")
    add_routine(user, "rotate")
    add_source(user, "ext1", name="Indie Finds")
    reply, status = review(api, user, {"op": "add_playlist_source", "playlist_id": "ext2"})
    assert status == 200
    assert affected_by_id(reply) == {
        raid.id: {"id": raid.id, "operation": "Collect", "timing_text": "Every day", "is_enabled": True,
                  "effects": ["will use Jazz Picks on its next run"]},
        composite.id: {"id": composite.id, "operation": "Collect and shuffle",
                       "timing_text": "Every Monday at 06:00 timezone unavailable", "is_enabled": False,
                       "effects": ["will use Jazz Picks on its next run"]},
        drips.id: {"id": drips.id, "operation": "Collect and add", "timing_text": "Every day", "is_enabled": True,
                   "effects": ["will use Jazz Picks on its next run"]},
    }
    assert reply["unaffected_routines"] == [] and reply["notes"] == []
    assert reply["changes"] == [{
        "index": 0, "op": "add_playlist_source", "subject": "source:ext2",
        "summary": "Add source: Jazz Picks (playlist)", "playlist_id": "ext2", "name": "Jazz Picks",
        "source_type": "external",
    }]
    assert set(reply["acknowledgment"]) == {"version", "issued_at", "signature"}
    assert reply["configuration_revision"] == revision(user)
    assert api.writes == [] and UpstreamSource.query.count() == 1 and ActivityLog.query.count() == 0
    assert_never_scheduled(scheduler_spies)


def test_each_routine_appears_once_with_every_effect(user, api):
    raid = add_routine(user, "raid_and_drip", ["ext1"])
    add_source(user, "ext1", name="Indie Finds")
    add_link(user)
    add_pair(user)
    reply, _ = review(
        api, user,
        {"op": "set_source_count", "source_id": UpstreamSource.query.one().id, "raid_count": 9},
        {"op": "replace_collection", "playlist_id": "col2"},
        {"op": "set_drip", "drip_enabled": True, "drip_count": 6},
        {"op": "unpair_archive"},
    )
    assert [routine["id"] for routine in reply["affected_routines"]] == [raid.id]
    assert reply["affected_routines"][0]["effects"] == [
        "will stop skipping discoveries already in Archive One",
        "will collect 9 per run from Indie Finds",
        "will add discoveries to Collection Two instead of Collection One",
        "will add tracks from Collection Two instead of Collection One",
        "saved runs add collected tracks",
        "saved runs add 6 per run",
    ]
    assert [change["op"] for change in reply["changes"]] == [
        "unpair_archive", "set_source_count", "replace_collection", "set_drip",
    ]
    assert [change["index"] for change in reply["changes"]] == [3, 0, 1, 2]


def test_collection_changes_affect_collecting_and_adding_routines(user, api):
    raid = add_routine(user, "raid")
    drip = add_routine(user, "drip")
    add_routine(user, "rotate")
    add_routine(user, "shuffle")
    reply, _ = review(api, user, {"op": "link_collection", "playlist_id": "col1"})
    assert {key: value["effects"] for key, value in affected_by_id(reply).items()} == {
        raid.id: ["will add discoveries to Collection One"], drip.id: ["will add tracks from Collection One"],
    }
    add_link(user)
    reply, _ = review(api, user, {"op": "unlink_collection"})
    assert {key: value["effects"] for key, value in affected_by_id(reply).items()} == {
        raid.id: ["will stop adding discoveries to Collection One"], drip.id: ["will skip adding: no collection"],
    }
    assert reply["notes"] == [
        f"Saved runs of Add collected tracks (#{drip.id}) skip adding tracks until a collection is linked."
    ]


def test_archive_changes_affect_rotation_and_collection_deduplication(user, api):
    raid = add_routine(user, "raid_and_shuffle")
    rotate = add_routine(user, "rotate")
    add_routine(user, "drip")
    reply, _ = review(api, user, {"op": "pair_archive", "playlist_id": "arc1"})
    assert {key: value["effects"] for key, value in affected_by_id(reply).items()} == {
        raid.id: ["will skip discoveries already in Archive One"], rotate.id: ["will rotate tracks into Archive One"],
    }
    add_pair(user)
    reply, _ = review(api, user, {"op": "replace_archive", "playlist_id": "arc2"})
    assert affected_by_id(reply)[rotate.id]["effects"] == ["will rotate tracks into Archive Two instead of Archive One"]
    reply, _ = review(api, user, {"op": "unpair_archive"})
    assert affected_by_id(reply)[rotate.id]["effects"] == ["will have no archive to rotate into"]


def test_drip_settings_skip_routines_that_override_or_force_them(user, api):
    plain = add_routine(user, "drip")
    counted = add_routine(user, "raid_and_drip", params={"drip_count": 2})
    forced = add_routine(user, "drip", params={"force": True})
    # Any saved drip_count is the routine's own, as the executor reads it: it never falls back to the shared count.
    unusable = add_routine(user, "drip", params={"drip_count": None})
    add_routine(user, "raid")
    add_link(user)
    reply, _ = review(api, user, {"op": "set_drip", "drip_count": 5})
    # Adding is off: a forced routine adds anyway; the others add that count once adding is on.
    assert {key: value["effects"] for key, value in affected_by_id(reply).items()} == {
        plain.id: ["will add 5 per run when adding is on"], forced.id: ["saved runs add 5 per run"],
    }
    assert reply["unaffected_routines"] == [
        {"id": counted.id, "operation": "Collect and add", "reason": "keeps its own count of 2 per run"},
        {"id": unusable.id, "operation": "Add collected tracks", "reason": "keeps its own count of None per run"},
    ]
    reply, _ = review(api, user, {"op": "set_drip", "drip_enabled": False, "drip_count": 4})
    assert {key: value["effects"] for key, value in affected_by_id(reply).items()} == {
        plain.id: ["saved runs skip adding", "will add 4 per run when adding is on"],
        counted.id: ["saved runs skip adding", "keeps its own count of 2 per run"],
        forced.id: ["saved runs add 4 per run", "always adds collected tracks, whatever the Drip setting"],
        unusable.id: ["saved runs skip adding", "keeps its own count of None per run"],
    }


def test_auto_archive_affects_no_routine_and_says_so(user, api):
    add_routine(user, "rotate")
    add_routine(user, "raid")
    add_pair(user)
    reply, _ = review(api, user, {"op": "set_auto_archive", "value": False})
    assert reply["affected_routines"] == []
    assert reply["notes"] == [setup.AUTO_ARCHIVE_NOTE, setup.NO_ROUTINES]


def test_with_no_routine_the_review_says_nothing_is_scheduled(user, api):
    reply, status = review(api, user, {"op": "add_search_source", "search_query": "lofi"})
    assert status == 200 and reply["affected_routines"] == []
    assert reply["notes"] == ["No existing routines are affected. This does not schedule anything."]


def test_removing_the_last_source_explains_that_routines_stay_as_they_are(user, api):
    enabled = add_routine(user, "raid", ["ext1"])
    paused = add_routine(user, "raid_and_drip", ["ext1"], enabled=False)
    also = add_routine(user, "raid_and_shuffle", [])
    source = add_source(user, "ext1", name="Indie Finds")
    reply, _ = review(api, user, {"op": "remove_source", "source_id": source.id})
    effects = [routine["effects"] for routine in reply["affected_routines"]]
    assert effects == [["will stop collecting from Indie Finds"]] * 3
    assert reply["notes"] == [
        f"Collect (#{enabled.id}) and Collect and shuffle (#{also.id}) stay enabled but cannot collect discoveries "
        "until a source is added. "
        f"Collect and add (#{paused.id}) stays paused but cannot collect discoveries until a source is added. "
        "A composite routine still runs its other step."
    ]
    add_source(user, query="lofi")
    reply, _ = review(api, user, {"op": "remove_source", "source_id": source.id})
    assert reply["notes"] == []


def test_a_routine_only_source_lists_only_the_routines_that_use_it(user, api):
    first = add_routine(user, "raid", ["ghost", "ext1"])
    add_routine(user, "raid", ["ext1"])
    add_routine(user, "shuffle", ["ghost"])
    reply, status = review(api, user, {"op": "remove_routine_source", "playlist_id": "ghost"})
    assert status == 200
    assert affected_by_id(reply) == {first.id: {
        "id": first.id, "operation": "Collect", "timing_text": "Every day", "is_enabled": True,
        "effects": ["will stop collecting from ghost"],
    }}
    assert reply["changes"][0]["routine_ids"] == [first.id]


SUMMARIES = json.loads((Path(__file__).parents[1] / "fixtures" / "setup_change_summaries.json").read_text())


@pytest.mark.parametrize("saved, case", [
    *(("saved", case) for case in SUMMARIES["cases"]), *(("unsaved", case) for case in SUMMARIES["unsaved_cases"]),
], ids=lambda value: value if isinstance(value, str) else value["summary"])
def test_each_change_has_the_one_wording_the_page_shows(user, saved, case):
    """The normalized summary is the wording of tests/fixtures/setup_change_summaries.json, as the page's is."""
    names = SUMMARIES["playlists"]
    playlists = {TARGET: {"name": "Morning Mix"}, **{key: {"name": name} for key, name in names.items()}}
    api = SetupSpotify(playlists=playlists)
    if saved == "saved":
        for source_id, source in SUMMARIES["sources"].items():
            db.session.add(UpstreamSource(
                id=int(source_id), user_id=user.id, target_playlist_id=TARGET, source_playlist_id=f"src{source_id}",
                source_type="external", source_name=source["name"], raid_count=source["raid_count"],
            ))
        db.session.commit()
        collection, archive = SUMMARIES["saved"]["collection"], SUMMARIES["saved"]["archive"]
        add_link(user, collection, name=names[collection])
        add_pair(user, archive, name=names[archive])
        add_routine(user, "raid", ["ghost1"])
    reply, status = review(api, user, case["change"])
    assert status == 200, reply
    assert [change["summary"] for change in reply["changes"]] == [case["summary"]]


# --- Validation -----------------------------------------------------------------------------


def refusal(reply):
    payload, status = reply
    assert status == 400 and payload["error_code"] == "invalid_changes", payload
    return payload["errors"]


@pytest.mark.parametrize("change, field, message", [
    ({"op": "add_playlist_source", "playlist_id": TARGET}, "playlist_id", "This is the playlist being set up"),
    ({"op": "add_playlist_source", "playlist_id": "ext1"}, "playlist_id", "Already a source"),
    ({"op": "add_playlist_source", "playlist_id": "nowhere"}, "playlist_id", "Playlist not found"),
    ({"op": "add_search_source", "search_query": "lofi"}, "search_query", "Already a source"),
    ({"op": "remove_source", "source_id": 999}, "source_id", "Source not found"),
    ({"op": "set_source_count", "source_id": 999, "raid_count": 3}, "source_id", "Source not found"),
    ({"op": "remove_routine_source", "playlist_id": "ext1"}, "playlist_id",
     "This is a saved source. Remove it from the sources instead."),
    ({"op": "remove_routine_source", "playlist_id": "unused"}, "playlist_id", "Not used by any routine"),
    ({"op": "replace_collection", "playlist_id": "col1"}, "op", "No collection is linked"),
    ({"op": "unlink_collection"}, "op", "No collection is linked"),
    ({"op": "replace_archive", "playlist_id": "arc1"}, "op", "No archive is paired"),
    ({"op": "unpair_archive"}, "op", "No archive is paired"),
    ({"op": "set_drip", "drip_enabled": True}, "op", "Link a collection first"),
    ({"op": "set_auto_archive", "value": True}, "op", "Pair an archive first"),
    ({"op": "link_collection", "playlist_id": TARGET}, "playlist_id", "This is the playlist being set up"),
    ({"op": "pair_archive", "playlist_id": TARGET}, "playlist_id", "This is the playlist being set up"),
    ({"op": "link_collection", "playlist_id": "foreign"}, "playlist_id",
     "You can only use your own or collaborative playlists"),
    ({"op": "pair_archive", "playlist_id": "nowhere"}, "playlist_id", "Playlist not found"),
])
def test_each_rule_refuses_its_change_beside_its_field(user, api, change, field, message):
    add_source(user, "ext1")
    add_source(user, query="lofi")
    reply = review(api, user, {"op": "add_search_source", "search_query": "fine"}, change)
    assert refusal(reply) == [{"index": 1, "field": field, "message": message}]


def test_sources_of_another_user_or_target_are_not_found(user, other_user, api):
    theirs = add_source(other_user, "ext1")
    elsewhere = add_source(user, "ext2", target="elsewhere")
    reply = review(
        api, user, {"op": "remove_source", "source_id": theirs.id},
        {"op": "set_source_count", "source_id": elsewhere.id, "raid_count": 4},
    )
    assert refusal(reply) == [
        {"index": 0, "field": "source_id", "message": "Source not found"},
        {"index": 1, "field": "source_id", "message": "Source not found"},
    ]


def test_an_existing_link_or_pair_must_be_replaced_not_linked_again(user, api):
    add_link(user)
    add_pair(user)
    reply = review(
        api, user, {"op": "link_collection", "playlist_id": "col2"}, {"op": "pair_archive", "playlist_id": "arc2"}
    )
    assert refusal(reply) == [
        {"index": 0, "field": "op", "message": "A collection is already linked. Replace it instead."},
        {"index": 1, "field": "op", "message": "An archive is already paired. Replace it instead."},
    ]


def test_the_source_limit_counts_removals_first_and_every_addition(user, api):
    sources = [add_source(user, f"p{index}") for index in range(10)]
    reply, status = review(
        api, user, {"op": "add_playlist_source", "playlist_id": "ext1"},
        {"op": "remove_source", "source_id": sources[0].id},
    )
    assert status == 200 and [change["op"] for change in reply["changes"]] == ["remove_source", "add_playlist_source"]
    reply = review(
        api, user, add_playlist("ext1"), add_search("q"), {"op": "remove_source", "source_id": sources[0].id},
    )
    assert refusal(reply) == [
        {"index": 1, "field": "search_query", "message": "A playlist can have at most 10 sources"},
    ]
    lookups = api.calls.count(("get_playlist", "ext1", False))
    reply = review(api, user, add_playlist("ext1"))
    assert refusal(reply) == [{"index": 0, "field": "playlist_id", "message": "A playlist can have at most 10 sources"}]
    assert api.calls.count(("get_playlist", "ext1", False)) == lookups


def test_distinct_collection_and_archive_are_refused_only_when_a_change_introduces_it(user, api):
    add_pair(user, "col1", name="Collection One")
    reply = review(api, user, {"op": "link_collection", "playlist_id": "col1"})
    assert refusal(reply) == [
        {"index": 0, "field": "playlist_id", "message": "The collection and archive must be different playlists"},
    ]
    add_link(user, "col1")
    reply, status = review(api, user, {"op": "set_drip", "drip_count": 2})
    assert status == 200
    reply = review(
        api, user, {"op": "replace_archive", "playlist_id": "arc1"}, {"op": "replace_collection", "playlist_id": "arc1"}
    )
    assert refusal(reply) == [
        {"index": 0, "field": "playlist_id", "message": "The collection and archive must be different playlists"},
        {"index": 1, "field": "playlist_id", "message": "The collection and archive must be different playlists"},
    ]


def test_a_collection_or_archive_candidate_is_checked_with_one_lean_read(user, api):
    reply, status = review(
        api, user, {"op": "link_collection", "playlist_id": "col1"}, {"op": "pair_archive", "playlist_id": "arc1"},
    )
    assert status == 200
    assert [call for call in api.calls if call[1] in ("col1", "arc1")] == [
        ("get_playlist_summary", "col1"), ("get_playlist_summary", "arc1"),
    ]
    api.calls.clear()
    reply = review(
        api, user, {"op": "link_collection", "playlist_id": "col2"}, {"op": "pair_archive", "playlist_id": "col2"},
    )
    assert [error["message"] for error in refusal(reply)] == [setup.DISTINCT, setup.DISTINCT]
    assert api.calls.count(("get_playlist_summary", "col2")) == 1


def test_a_collection_or_archive_spotify_refuses_is_not_editable(user, api):
    api.read_failures["col2"] = SpotifyForbiddenError("API error 403")
    assert refusal(review(api, user, {"op": "pair_archive", "playlist_id": "col2"})) == [
        {"index": 0, "field": "playlist_id", "message": "You can only use your own or collaborative playlists"},
    ]


def test_a_forbidden_source_is_not_found_and_other_spotify_failures_refuse_the_review(user, api):
    api.read_failures["ext1"] = SpotifyForbiddenError("API error 403")
    assert refusal(review(api, user, {"op": "add_playlist_source", "playlist_id": "ext1"})) == [
        {"index": 0, "field": "playlist_id", "message": "Playlist not found"},
    ]
    api.read_failures["ext1"] = SpotifyRateLimitError("slow down", retry_after=7)
    payload, status, headers = review(api, user, add_playlist("ext1"))
    assert (status, payload["error_code"], payload["retry_after"]) == (429, "rate_limited", 7)
    assert headers == {"Retry-After": "7"}
    api.read_failures["col1"] = SpotifyRateLimitError("slow down")
    payload, status, *_ = review(api, user, {"op": "link_collection", "playlist_id": "col1"})
    assert (status, payload["error_code"], payload["retry_after"]) == (429, "rate_limited", None)


def test_a_stale_revision_is_a_conflict_that_names_changed_sections(user, api):
    add_source(user, "ext1")
    state = setup.read_state(user.id, TARGET)
    stale, digests = setup.configuration_revision(state), setup.section_digests(state)
    add_link(user)
    body = {"configuration_revision": stale, "changes": [{"op": "set_drip", "drip_count": 2}]}
    payload, status = PlaylistSetupService.impact(api, user, TARGET, SetupImpactRequest.model_validate(body))
    assert (status, payload["error_code"], payload["changed_sections"]) == (409, "configuration_changed", None)
    assert payload["context"]["configuration_revision"] == revision(user)
    request = SetupImpactRequest.model_validate({**body, "section_digests": digests})
    payload, status = PlaylistSetupService.impact(api, user, TARGET, request)
    assert payload["changed_sections"] == ["collection"]


# --- Save ---------------------------------------------------------------------------------


def definition(schedule):
    row = db.session.get(Schedule, schedule.id)
    return (row.job_type, row.schedule_type, row.schedule_value, row.is_enabled, row.algorithm_params)


def test_removing_a_source_prunes_it_from_every_raid_family_routine_and_nothing_else(
    user, other_user, api, scheduler_spies
):
    first = add_routine(user, "raid", ["a", "ext1", "b"], kind="cron", value="0 6 * * 2")
    second = add_routine(user, "raid_and_shuffle", ["ext1"], enabled=False, params={"keep_first": 2})
    unrelated = add_routine(user, "raid_and_drip", ["c"])
    shuffle = add_routine(user, "shuffle", ["ext1"])
    theirs = add_routine(other_user, "raid", ["ext1"])
    source = add_source(user, "ext1", name="Indie Finds")
    before = {schedule.id: definition(schedule) for schedule in (first, second, unrelated, shuffle)}
    reviewed, (reply, status) = saved(api, user, {"op": "remove_source", "source_id": source.id})
    assert status == 200 and reply["saved"] is True
    assert reply["affected_schedule_ids"] == [first.id, second.id, unrelated.id]
    assert routine_ids(first) == ["a", "b"] and routine_ids(second) == []
    assert routine_ids(unrelated) == ["c"] and routine_ids(shuffle) == ["ext1"] and routine_ids(theirs) == ["ext1"]
    assert {schedule.id: definition(schedule) for schedule in (first, second, unrelated, shuffle)} == before
    assert Schedule.query.count() == 5 and UpstreamSource.query.count() == 0
    for schedule in (first, second, unrelated):
        loaded = _load_sources(routine_ids(schedule), user.id, TARGET)
        assert "ext1" not in [s.source_playlist_id for s in loaded]
    assert reply["context"]["state_digest"] == reviewed["expected_state_digest"]
    assert reply["configuration_revision"] == reply["context"]["configuration_revision"] == revision(user)
    assert api.writes == []
    assert_never_scheduled(scheduler_spies)


def test_additions_append_to_raid_family_lists_and_a_search_source_changes_none(user, api, scheduler_spies):
    first = add_routine(user, "raid", ["a"])
    second = add_routine(user, "raid_and_drip", None)
    rotate = add_routine(user, "rotate", [])
    reviewed, (reply, status) = saved(
        api, user, {"op": "add_playlist_source", "playlist_id": "ext1"},
        {"op": "add_search_source", "search_query": "lofi", "source_name": "Lo-fi"},
        {"op": "add_playlist_source", "playlist_id": "mine1"},
    )
    assert status == 200
    assert routine_ids(first) == ["a", "ext1", "mine1"] and routine_ids(second) == ["ext1", "mine1"]
    assert routine_ids(rotate) == []
    rows = [(s.source_type, s.source_playlist_id, s.search_query, s.source_name, s.raid_count)
            for s in UpstreamSource.query.order_by(UpstreamSource.id)]
    assert rows == [
        ("external", "ext1", None, "Indie Finds", 5), ("search_query", None, "lofi", "Lo-fi", 5),
        ("own", "mine1", None, "My Finds", 5),
    ]
    assert reply["context"]["state_digest"] == reviewed["expected_state_digest"]
    _, (reply, status) = saved(api, user, {"op": "add_search_source", "search_query": "jazz"})
    assert status == 200 and routine_ids(first) == ["a", "ext1", "mine1"] and routine_ids(second) == ["ext1", "mine1"]
    assert UpstreamSource.query.filter_by(search_query="jazz").one().source_name == "jazz"
    assert_never_scheduled(scheduler_spies)


def test_removing_a_routine_only_source_prunes_it_so_no_raid_resurrects_it(user, api, scheduler_spies):
    first = add_routine(user, "raid", ["ghost", "ext1"])
    second = add_routine(user, "raid_and_shuffle", ["ghost"])
    add_source(user, "ext1")
    assert [s.source_playlist_id for s in _load_sources(routine_ids(second), user.id, TARGET)] == ["ext1", "ghost"]
    reviewed, (reply, status) = saved(api, user, {"op": "remove_routine_source", "playlist_id": "ghost"})
    assert status == 200 and reply["affected_schedule_ids"] == [first.id, second.id]
    assert routine_ids(first) == ["ext1"] and routine_ids(second) == []
    for schedule in (first, second):
        assert [s.source_playlist_id for s in _load_sources(routine_ids(schedule), user.id, TARGET)] == ["ext1"]
    assert reply["context"]["routine_only_sources"] == []
    assert reply["context"]["state_digest"] == reviewed["expected_state_digest"]
    assert_never_scheduled(scheduler_spies)


def test_removals_free_their_slots_before_additions(user, api):
    sources = [add_source(user, f"p{index}") for index in range(10)]
    _, (reply, status) = saved(
        api, user, add_search("lofi"), {"op": "remove_source", "source_id": sources[3].id},
    )
    assert status == 200 and UpstreamSource.query.count() == 10
    assert UpstreamSource.query.filter_by(search_query="lofi").count() == 1
    assert db.session.get(UpstreamSource, sources[3].id) is None


def test_a_source_removed_and_added_back_is_kept_in_every_raid_list(user, api):
    first = add_routine(user, "raid", ["ext1", "a"])
    source = add_source(user, "ext1", count=9)
    reviewed, (reply, status) = saved(
        api, user, {"op": "remove_source", "source_id": source.id}, add_playlist("ext1"),
    )
    assert status == 200 and routine_ids(first) == ["a", "ext1"]
    assert UpstreamSource.query.one().raid_count == 5
    assert reply["context"]["state_digest"] == reviewed["expected_state_digest"]


def test_counts_and_replacements_keep_the_other_settings_in_place(user, api):
    source = add_source(user, "ext1")
    link = add_link(user, enabled=True, count=7)
    pair = add_pair(user, auto=False)
    reviewed, (reply, status) = saved(
        api, user, {"op": "set_source_count", "source_id": source.id, "raid_count": 12},
        {"op": "replace_collection", "playlist_id": "col2"}, {"op": "replace_archive", "playlist_id": "collab"},
    )
    assert status == 200
    assert db.session.get(UpstreamSource, source.id).raid_count == 12
    link, pair = RaidPlaylistLink.query.one(), PlaylistPair.query.one()
    assert (link.id, link.raid_playlist_id, link.raid_playlist_name, link.drip_enabled, link.drip_count) == (
        link.id, "col2", "Collection Two", True, 7)
    assert (pair.archive_playlist_id, pair.archive_playlist_name, pair.auto_archive_on_remove) == (
        "collab", "Shared Collab", False)
    assert reply["context"]["state_digest"] == reviewed["expected_state_digest"]


def test_new_links_start_as_the_legacy_creates_and_take_settings_from_the_same_list(user, api):
    _, (reply, status) = saved(
        api, user, {"op": "set_drip", "drip_enabled": True, "drip_count": 9},
        {"op": "link_collection", "playlist_id": "col1"}, {"op": "pair_archive", "playlist_id": "arc1"},
    )
    assert status == 200
    link, pair = RaidPlaylistLink.query.one(), PlaylistPair.query.one()
    assert (link.raid_playlist_id, link.raid_playlist_name, link.target_playlist_name) == (
        "col1", "Collection One", "Morning Mix")
    assert (link.drip_enabled, link.drip_count) == (True, 9)
    assert (pair.archive_playlist_id, pair.production_playlist_name, pair.auto_archive_on_remove) == (
        "arc1", "Morning Mix", True)
    _, (reply, status) = saved(api, user, {"op": "unlink_collection"}, {"op": "set_auto_archive", "value": False})
    assert status == 200 and RaidPlaylistLink.query.count() == 0
    assert PlaylistPair.query.one().auto_archive_on_remove is False


def test_a_new_link_and_pair_are_written_with_the_settings_the_review_simulates(user, api, monkeypatch):
    _, (reply, status) = saved(
        api, user, {"op": "link_collection", "playlist_id": "col1"}, {"op": "pair_archive", "playlist_id": "arc1"},
    )
    assert status == 200
    link, pair = RaidPlaylistLink.query.one(), PlaylistPair.query.one()
    assert (link.drip_enabled, link.drip_count, pair.auto_archive_on_remove) == (False, 3, True)
    db.session.delete(link)
    db.session.delete(pair)
    db.session.commit()
    monkeypatch.setattr(setup, "NEW_SETTINGS", {
        "collection": {"drip_enabled": True, "drip_count": 7}, "archive": {"auto_archive_on_remove": False},
    })
    reviewed, (reply, status) = saved(
        api, user, {"op": "link_collection", "playlist_id": "col1"}, {"op": "pair_archive", "playlist_id": "arc1"},
    )
    link, pair = RaidPlaylistLink.query.one(), PlaylistPair.query.one()
    assert (link.drip_enabled, link.drip_count, pair.auto_archive_on_remove) == (True, 7, False)
    assert reply["context"]["state_digest"] == reviewed["expected_state_digest"]


def test_a_review_stages_its_writes_and_only_save_runs_them(user, api):
    source = add_source(user, "ext1")
    add_link(user)
    state = setup.read_state(user.id, TARGET)
    changes = [
        SetupImpactRequest.model_validate({"configuration_revision": "a" * 64, "changes": [change]}).changes[0]
        for change in (
            {"op": "remove_source", "source_id": source.id}, {"op": "set_drip", "drip_count": 4},
            {"op": "add_search_source", "search_query": "lofi"},
        )
    ]
    review = setup._Review(api, user, TARGET, "Morning Mix", state).run(list(enumerate(changes)))
    assert [(write.func, write.keywords["commit"]) for write in review.writes] == [
        (setup.UpstreamSourceService.delete_source, False), (setup.RaidLinkService.update_link, False),
        (setup.UpstreamSourceService.add_search_source, False),
    ]
    assert UpstreamSource.query.count() == 1 and RaidPlaylistLink.query.one().drip_count == 3


def test_a_legacy_shared_collection_and_archive_is_kept_until_changed(user, api):
    add_link(user, "col1")
    add_pair(user, "col1", name="Collection One")
    _, (reply, status) = saved(api, user, {"op": "set_drip", "drip_count": 2})
    assert status == 200
    assert RaidPlaylistLink.query.one().raid_playlist_id == PlaylistPair.query.one().archive_playlist_id == "col1"
    _, (reply, status) = saved(api, user, {"op": "replace_archive", "playlist_id": "arc1"})
    assert status == 200 and PlaylistPair.query.one().archive_playlist_id == "arc1"


def test_save_logs_one_activity_and_commits_once(user, api):
    first = add_routine(user, "raid", ["ext1"])
    add_routine(user, "shuffle")
    source = add_source(user, "ext1", name="Indie Finds")
    changes = ({"op": "remove_source", "source_id": source.id}, {"op": "add_search_source", "search_query": "lofi"})
    reviewed, _ = review(api, user, *changes)
    body = {"configuration_revision": reviewed["configuration_revision"], "changes": list(changes),
            "acknowledgment": reviewed["acknowledgment"]}
    with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
        reply, status = PlaylistSetupService.save(api, user, TARGET, SetupSaveRequest.model_validate(body))
    assert status == 200 and commit.call_count == 1
    activity = ActivityLog.query.one()
    assert (activity.activity_type, activity.playlist_id, activity.playlist_name) == (
        ActivityType.PLAYLIST_SETUP_SAVE, TARGET, "Morning Mix")
    assert activity.description == "Saved playlist settings: Remove source: Indie Finds; Add source: “lofi” (search)"
    assert activity.metadata_json == {
        "changes": ["Remove source: Indie Finds", "Add source: “lofi” (search)"],
        "affected_schedule_ids": [first.id],
        "revision_before": reviewed["configuration_revision"],
        "revision_after": reply["configuration_revision"],
    }
    assert reply["configuration_revision"] == revision(user) != reviewed["configuration_revision"]


@pytest.mark.parametrize("failure", ["write", "activity", "commit"])
def test_a_failed_save_rolls_back_every_staged_change(user, api, failure):
    first = add_routine(user, "raid", ["ext1"])
    add_source(user, "ext1")
    add_link(user)
    source = UpstreamSource.query.one()
    before = revision(user)
    changes = (
        {"op": "remove_source", "source_id": source.id}, {"op": "set_drip", "drip_enabled": True},
        {"op": "add_search_source", "search_query": "lofi"},
    )
    reviewed, _ = review(api, user, *changes)
    body = {"configuration_revision": before, "changes": list(changes), "acknowledgment": reviewed["acknowledgment"]}
    target = {
        "write": (setup.RaidLinkService, "update_link"),
        "activity": (activity_log_service, "ActivityLog"),
        "commit": (setup, "safe_commit"),
    }[failure]
    with patch.object(*target, side_effect=RuntimeError("disk full")):
        reply, status = PlaylistSetupService.save(api, user, TARGET, SetupSaveRequest.model_validate(body))
    assert (status, reply["error_code"], reply["message"]) == (500, "save_failed", "Could not confirm settings save.")
    assert revision(user) == before
    assert UpstreamSource.query.one().source_playlist_id == "ext1" and routine_ids(first) == ["ext1"]
    assert RaidPlaylistLink.query.one().drip_enabled is False and ActivityLog.query.count() == 0


def test_save_reports_the_save_when_its_fresh_context_cannot_be_read(user, api):
    with patch.object(setup, "_context", side_effect=RuntimeError("read failed")):
        _, (reply, status) = saved(api, user, {"op": "add_search_source", "search_query": "lofi"})
    assert status == 200 and reply["saved"] is True and reply["context"] is None
    assert reply["configuration_revision"] == revision(user)
    assert UpstreamSource.query.one().search_query == "lofi"


def test_a_routine_run_between_review_and_save_is_not_a_conflict(user, api):
    schedule = add_routine(user, "raid", ["ext1"])
    source = add_source(user, "ext1")
    changes = ({"op": "set_source_count", "source_id": source.id, "raid_count": 3},)
    reviewed, _ = review(api, user, *changes)
    schedule.last_status, schedule.last_run_at = "success", datetime.now(timezone.utc)
    source.last_resolved_at, source.last_track_count = datetime.now(timezone.utc), 40
    db.session.commit()
    body = {"configuration_revision": reviewed["configuration_revision"], "changes": list(changes),
            "acknowledgment": reviewed["acknowledgment"]}
    reply, status = PlaylistSetupService.save(api, user, TARGET, SetupSaveRequest.model_validate(body))
    assert status == 200 and db.session.get(UpstreamSource, source.id).raid_count == 3


def test_a_stale_save_is_a_conflict_with_fresh_context_and_applies_nothing(user, api, scheduler_spies):
    source = add_source(user, "ext1")
    state = setup.read_state(user.id, TARGET)
    digests = setup.section_digests(state)
    changes = [{"op": "set_source_count", "source_id": source.id, "raid_count": 3}]
    reviewed, _ = review(api, user, *changes)
    add_routine(user, "raid", ["ext2"])
    body = {"configuration_revision": reviewed["configuration_revision"], "changes": changes,
            "acknowledgment": reviewed["acknowledgment"], "section_digests": digests}
    payload, status = PlaylistSetupService.save(api, user, TARGET, SetupSaveRequest.model_validate(body))
    assert (status, payload["error_code"], payload["changed_sections"]) == (409, "configuration_changed", ["routines"])
    assert payload["context"]["configuration_revision"] == revision(user)
    only = [{"playlist_id": "ext2", "routine_ids": [Schedule.query.one().id]}]
    assert payload["context"]["routine_only_sources"] == only
    assert db.session.get(UpstreamSource, source.id).raid_count == 5 and ActivityLog.query.count() == 0
    assert_never_scheduled(scheduler_spies)


def test_save_refuses_an_expired_mismatched_or_malformed_acknowledgment(user, api):
    source = add_source(user, "ext1")
    reviewed_change = {"op": "set_source_count", "source_id": source.id, "raid_count": 3}
    reviewed, _ = review(api, user, reviewed_change)
    token = reviewed["acknowledgment"]

    def save(changes, acknowledgment, *, now=None):
        body = {"configuration_revision": reviewed["configuration_revision"], "changes": changes,
                "acknowledgment": acknowledgment}
        with patch("shuffify.services.signed_digest.time.time", return_value=now or token["issued_at"] + 60):
            return PlaylistSetupService.save(api, user, TARGET, SetupSaveRequest.model_validate(body))

    cases = [
        (save([reviewed_change], token, now=token["issued_at"] + 1801), "expired"),
        (save([{**reviewed_change, "raid_count": 4}], token), "mismatch"),
        (save([reviewed_change, {"op": "add_search_source", "search_query": "x"}], token), "mismatch"),
        (save([reviewed_change], {**token, "signature": "0" * 64}), "mismatch"),
        (save([reviewed_change], {"version": 1}), "malformed"),
        (save([reviewed_change], "token"), "malformed"),
    ]
    for (payload, status), reason in cases:
        assert (status, payload["error_code"], payload["reason"]) == (409, "acknowledgment_invalid", reason)
    assert db.session.get(UpstreamSource, source.id).raid_count == 5 and ActivityLog.query.count() == 0
    payload, status = save([reviewed_change], token, now=token["issued_at"] + 1800)
    assert status == 200 and db.session.get(UpstreamSource, source.id).raid_count == 3


def test_save_checks_its_acknowledgment_before_reading_any_change_from_spotify(user, api):
    change = {"op": "link_collection", "playlist_id": "col1"}
    reviewed, _ = review(api, user, change)
    api.calls.clear()
    body = save_body(reviewed, [change])
    body.acknowledgment = {**body.acknowledgment, "signature": "0" * 64}
    payload, status = PlaylistSetupService.save(api, user, TARGET, body)
    assert (status, payload["error_code"], payload["reason"]) == (409, "acknowledgment_invalid", "mismatch")
    assert api.calls == [("get_playlist_summary", TARGET)] and RaidPlaylistLink.query.count() == 0


def test_a_review_replayed_for_another_user_or_target_is_refused(user, other_user, api):
    reviewed, _ = review(api, user, add_search("lofi"))
    other_api = SetupSpotify(owner="user456")
    # The revision itself is per user and target, so the replay is stale before its signature is checked.
    for spotify, who, target in ((other_api, other_user, TARGET), (api, user, "col1")):
        payload, status = PlaylistSetupService.save(spotify, who, target, save_body(reviewed, [add_search("lofi")]))
        assert (status, payload["error_code"]) == (409, "configuration_changed")
    assert UpstreamSource.query.count() == 0
    payload = setup._ack_payload(user, TARGET, reviewed["configuration_revision"], "c" * 64)
    assert set(payload) == {"user_id", "target_id", "configuration_revision", "change_hash"}
    assert (payload["user_id"], payload["target_id"]) == (user.id, TARGET)


@pytest.mark.parametrize("target_lock", [playlist_lock, playlist_configuration_lock])
def test_save_is_busy_while_the_target_is_locked(user, api, scheduler_spies, monkeypatch, target_lock):
    monkeypatch.setattr(setup, "LOCK_WAIT_S", 0.01)
    reviewed, _ = review(api, user, add_search("lofi"))
    body = save_body(reviewed, [add_search("lofi")])
    calls = len(api.calls)
    entered, release = threading.Event(), threading.Event()

    def hold_target():
        with target_lock(TARGET) as acquired:
            assert acquired is True
            entered.set()
            assert release.wait(2)

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_target)
        assert entered.wait(2)
        try:
            payload, status = PlaylistSetupService.save(api, user, TARGET, body)
        finally:
            release.set()
        holder.result(timeout=2)
    assert (status, payload["error_code"], payload["retryable"]) == (409, "playlist_busy", True)
    assert payload["message"] == "A routine or save is running on this playlist. Try again when it finishes."
    assert len(api.calls) == calls and UpstreamSource.query.count() == 0
    payload, status = PlaylistSetupService.save(api, user, TARGET, body)
    assert status == 200 and UpstreamSource.query.count() == 1
    assert_never_scheduled(scheduler_spies)


def test_save_checks_the_changes_again_and_refuses_without_writing(user, api):
    reviewed, _ = review(api, user, add_playlist("ext1"))
    del api.playlists["ext1"]
    payload, status = PlaylistSetupService.save(api, user, TARGET, save_body(reviewed, [add_playlist("ext1")]))
    assert (status, payload["errors"]) == (400, [{"index": 0, "field": "playlist_id", "message": "Playlist not found"}])
    assert UpstreamSource.query.count() == 0 and ActivityLog.query.count() == 0
