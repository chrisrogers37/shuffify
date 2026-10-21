"""Fingerprinted inputs shared by executor reviews and their runs."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from shuffify.enums import JobType, LockTier, RotationMode
from shuffify.models.db import PlaylistPair, RaidPlaylistLink, Schedule, TrackLock, UpstreamSource, db
from shuffify.services.live_review_scope import (
    DIRECT_DRIP_PARAMS,
    JOB_STEPS,
    STEP_ROWS,
    build_part,
    direct_parts,
    direct_scope,
    drip_forced,
    drip_gate,
    drip_override,
    effective_drip_count,
    fresh_reads,
    read_inputs,
    rotation_settings,
    routine_parts,
    schedule_scope,
)
from shuffify.services.user_service import UserService


@pytest.fixture
def owner(db_app):
    return UserService.upsert_from_spotify({"id": "scope-owner", "display_name": "Owner", "images": []}).user


@pytest.fixture
def saved(owner):
    link = RaidPlaylistLink(
        user_id=owner.id, target_playlist_id="target", raid_playlist_id="collection", drip_count=2,
        drip_enabled=False, raid_playlist_name="Collection",
    )
    pair = PlaylistPair(user_id=owner.id, production_playlist_id="target", archive_playlist_id="archive")
    sources = [
        UpstreamSource(user_id=owner.id, target_playlist_id="target", source_playlist_id=f"source{i}",
                       source_type="own", raid_count=5, source_name=f"Source {i}")
        for i in range(2)
    ]
    db.session.add_all([link, pair, *sources])
    db.session.commit()
    return SimpleNamespace(link=link, pair=pair, sources=sources)


def raid_scope(owner, extras=()):
    rows = read_inputs(("raid",), owner.id, "target")
    return direct_scope("raid", "target", direct_parts("raid", rows, list(extras)))


def test_executor_steps_are_the_runner_order(owner):
    assert JOB_STEPS[JobType.RAID_AND_DRIP] == ("raid", "drip")
    assert JOB_STEPS[JobType.RAID_AND_SHUFFLE] == ("raid", "shuffle")
    assert set(JOB_STEPS) == set(JobType)


def test_equivalent_inputs_share_one_scope(owner, saved):
    first = raid_scope(owner, ["b", "a", "a"])
    reordered = build_part("raid", {**read_inputs(("raid",), owner.id, "target"),
                                    "sources": list(reversed(saved.sources))}, extras=["a", "b", "a"])
    assert direct_scope("raid", "target", {"raid": reordered}) == first
    # Duplicate extras add a source each time in the executor, so they count.
    assert raid_scope(owner, ["a", "b"]) != first


def test_names_status_and_tracking_are_not_inputs(owner, saved):
    before = raid_scope(owner)
    saved.link.raid_playlist_name = "Renamed"
    saved.pair.archive_playlist_name = "Renamed"
    for source in saved.sources:
        source.source_name, source.last_resolve_status, source.last_track_count = "Renamed", "failed", 9
    db.session.commit()
    assert raid_scope(owner) == before


@pytest.mark.parametrize("change", ["relink", "unlink", "pair", "unpair", "watch", "unwatch", "count", "query",
                                    "type", "playlist"])
def test_each_raid_input_changes_the_scope(owner, saved, change):
    before = raid_scope(owner)
    source = saved.sources[0]
    if change == "relink":
        saved.link.raid_playlist_id = "other"
    elif change == "unlink":
        db.session.delete(saved.link)
    elif change == "pair":
        saved.pair.archive_playlist_id = "other"
    elif change == "unpair":
        db.session.delete(saved.pair)
    elif change == "watch":
        db.session.add(UpstreamSource(user_id=owner.id, target_playlist_id="target", search_query="late",
                                      source_type="search_query", raid_count=5))
    elif change == "unwatch":
        db.session.delete(source)
    elif change == "count":
        source.raid_count = 6
    elif change == "query":
        source.search_query = "late"
    elif change == "type":
        source.source_type = "external"
    else:
        source.source_playlist_id = "renamed-source"
    db.session.commit()
    assert raid_scope(owner) != before


def test_a_new_link_row_with_the_same_collection_is_a_new_review():
    def part(link_id):
        link = SimpleNamespace(id=link_id, raid_playlist_id="collection")
        return build_part("raid", {"link": link, "pair": None, "sources": []})

    assert part(1) != part(2)


def test_direct_drip_is_forced_and_binds_the_count_it_will_use(owner, saved):
    rows = read_inputs(("drip",), owner.id, "target")
    part = build_part("drip", rows, params=DIRECT_DRIP_PARAMS)
    assert part == {"link": {"id": saved.link.id, "collection_playlist_id": "collection"}, "count": 2,
                    "gate": "forced"}
    # The run's transient Drip carries the count read before its lock.
    assert build_part("drip", rows, params={"force": True, "drip_count": 2}) == part
    assert build_part("drip", rows, params={"force": True, "drip_count": 3})["count"] == 3
    saved.link.drip_enabled = True
    db.session.commit()
    assert build_part("drip", read_inputs(("drip",), owner.id, "target"), params=DIRECT_DRIP_PARAMS) == part


def test_saved_drip_uses_the_executor_gate_and_override(owner, saved):
    link = saved.link
    assert (drip_gate({}, link), effective_drip_count({}, link)) == ("closed", 2)
    assert (drip_gate({"drip_count": "5"}, link), effective_drip_count({"drip_count": "5"}, link)) == ("closed", 5)
    assert effective_drip_count({"drip_count": 0}, link) == 1
    link.drip_enabled = True
    assert drip_gate(None, link) == "open"
    assert drip_gate({"force": True}, link) == "forced"
    with pytest.raises(ValueError):
        effective_drip_count({"drip_count": "lots"}, link)
    # A saved null is the routine's own count too: the executor never falls back to the link's.
    with pytest.raises(TypeError):
        effective_drip_count({"drip_count": None}, link)
    assert build_part("drip", {"link": None}) == {"link": None, "count": None, "gate": None}


def test_fresh_reads_reread_cached_rows_only_inside_the_block(owner, saved):
    from sqlalchemy import text

    def drip_count():
        return RaidPlaylistLink.query.filter_by(id=saved.link.id).one().drip_count

    assert drip_count() == 2
    db.session.execute(text("UPDATE raid_playlist_links SET drip_count = 9 WHERE id = :id"), {"id": saved.link.id})
    assert drip_count() == 2
    with fresh_reads():
        assert drip_count() == 9
    db.session.execute(text("UPDATE raid_playlist_links SET drip_count = 7 WHERE id = :id"), {"id": saved.link.id})
    assert drip_count() == 9


@pytest.mark.parametrize("params, own", [
    (None, "none"), ({}, "none"), ({"force": True}, "none"),
    ({"drip_count": 2}, 2), ({"drip_count": None}, None), ({"drip_count": "lots"}, "lots"), ({"drip_count": 0}, 0),
])
def test_a_routines_own_drip_count_is_any_saved_value(params, own):
    assert drip_override(params, "none") == own
    assert drip_override(params) == (None if own == "none" else own)


@pytest.mark.parametrize("params, forced", [
    (None, False), ({}, False), ({"force": False}, False), ({"force": 0}, False), ({"force": True}, True),
    ({"force": 1}, True), ({"drip_count": 3}, False),
])
def test_a_forced_drip_is_any_truthy_saved_force(params, forced):
    assert drip_forced(params) is forced


def test_each_step_reads_its_rows():
    assert STEP_ROWS == {
        "raid": ("link", "pair", "sources"), "drip": ("link",), "shuffle": ("locks",), "rotate": ("pair", "locked"),
    }
    assert set(STEP_ROWS) == {step for steps in JOB_STEPS.values() for step in steps}


def test_rotation_settings_normalize_as_the_executor_does():
    assert rotation_settings(None) == (RotationMode.SWAP, 5, None, 0)
    assert rotation_settings({"rotation_count": "0", "target_size": "0", "protect_count": -3}) == (
        RotationMode.SWAP, 1, 1, 0
    )
    with pytest.raises(ValueError):
        rotation_settings({"target_size": "big"})


def test_routine_parts_bind_algorithm_params_and_locks(owner, saved):
    routine = Schedule(user_id=owner.id, job_type=JobType.RAID_AND_SHUFFLE, target_playlist_id="target",
                       source_playlist_ids=["source1", "extra"], algorithm_name="BasicShuffle",
                       algorithm_params={"keep_first": 1})
    parts = routine_parts(routine, JOB_STEPS[routine.job_type])
    assert parts["raid"]["extras"] == ["extra", "source1"]
    assert parts["shuffle"] == {"algorithm": "BasicShuffle", "params": {"keep_first": 1}, "locks": []}
    db.session.add(TrackLock(user_id=owner.id, spotify_playlist_id="target", track_uri="spotify:track:" + "1" * 22,
                             position=3, lock_tier=LockTier.SUPER))
    db.session.commit()
    assert routine_parts(routine, ("shuffle",))["shuffle"]["locks"] == [[3, "spotify:track:" + "1" * 22]]
    rotate = routine_parts(
        Schedule(user_id=owner.id, job_type=JobType.ROTATE, target_playlist_id="target",
                 algorithm_params={"target_size": 4}), ("rotate",),
    )["rotate"]
    assert rotate == {"pair": {"id": saved.pair.id, "archive_playlist_id": "archive"}, "mode": "swap", "count": 5,
                      "cap": 4, "protect": 0, "locked": ["spotify:track:" + "1" * 22]}


@pytest.mark.parametrize("rows", ["locks", "locked", "link", "sources"])
def test_unreadable_rows_fail_closed_never_empty(owner, rows):
    def unavailable(*args):
        raise OperationalError("SELECT", {}, Exception("unavailable"))

    steps = {"locks": ("shuffle",), "locked": ("rotate",), "link": ("drip",), "sources": ("raid",)}[rows]
    with patch.dict("shuffify.services.live_review_scope._READERS", {rows: unavailable}):
        with pytest.raises(OperationalError):
            read_inputs(steps, owner.id, "target")


def test_lock_reads_use_the_fail_closed_readers():
    from shuffify.services import live_review_scope
    from shuffify.services.track_lock_service import TrackLockService

    assert live_review_scope._READERS["locks"] is TrackLockService.get_locked_positions
    assert live_review_scope._READERS["locked"] is TrackLockService.get_locked_uris


def test_schedule_scope_binds_identity_job_and_target():
    parts = {"shuffle": {"algorithm": "BasicShuffle", "params": {}, "locks": []}}
    base = schedule_scope(1, JobType.SHUFFLE, "target", parts)
    assert base == schedule_scope(1, "shuffle", "target", parts)
    assert len({base, schedule_scope(2, "shuffle", "target", parts), schedule_scope(1, "rotate", "target", parts),
                schedule_scope(1, "shuffle", "other", parts)}) == 4
