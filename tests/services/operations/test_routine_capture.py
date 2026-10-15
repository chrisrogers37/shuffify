"""Database-only captured commands bind owned definitions and execution inputs."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from shuffify.models.db import PlaylistPair, RaidPlaylistLink, Schedule, TrackLock, UpstreamSource, User, db
from shuffify.services.live_review_scope import direct_parts, direct_scope, routine_parts, schedule_scope
from shuffify.spotify.api import SpotifyAPI
from tests.services.operations.test_crash_recovery import uri


@pytest.fixture
def saved(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'capture.sqlite'}")
    db.metadata.create_all(engine)
    with Session(engine) as storage:
        storage.add_all([User(id=1, spotify_id="owner"), User(id=2, spotify_id="other")])
        storage.flush()
        schedule = Schedule(user_id=1, job_type="raid_and_drip", target_playlist_id="target",
                            target_playlist_name="Target", algorithm_name="BasicShuffle", algorithm_params={},
                            source_playlist_ids=["extra"], is_enabled=False)
        link = RaidPlaylistLink(user_id=1, target_playlist_id="target", target_playlist_name="Target",
                                raid_playlist_id="collection", raid_playlist_name="Collection",
                                drip_count=3, drip_enabled=False)
        pair = PlaylistPair(user_id=1, production_playlist_id="target", production_playlist_name="Target",
                            archive_playlist_id="archive", archive_playlist_name="Archive")
        sources = [UpstreamSource(user_id=1, target_playlist_id="target", source_type="external",
                                  source_playlist_id="catalog", source_name="Catalog", raid_count=5,
                                  source_url="https://open.spotify.com/playlist/catalog"),
                   UpstreamSource(user_id=1, target_playlist_id="target", source_type="search_query",
                                  search_query="instrumental", source_name="Discovery", raid_count=2)]
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        locks = [TrackLock(user_id=1, spotify_playlist_id="target", track_uri=uri(1), position=0),
                 TrackLock(user_id=1, spotify_playlist_id="target", track_uri=uri(2), position=4,
                           expires_at=now + timedelta(seconds=1)),
                 TrackLock(user_id=1, spotify_playlist_id="target", track_uri=uri(3), position=5,
                           expires_at=now),
                 TrackLock(user_id=1, spotify_playlist_id="target", track_uri=uri(4), position=6,
                           expires_at=now - timedelta(days=1))]
        storage.add_all([schedule, link, pair, *sources, *locks])
        storage.commit()
        yield SimpleNamespace(storage=storage, engine=engine, schedule=schedule, link=link, pair=pair,
                              sources=sources, locks=locks, now=now)
    engine.dispose()


def capture(saved, **kwargs):
    from shuffify.services.operations.commands.routine_capture import capture_schedule

    return capture_schedule(saved.storage, 1, saved.schedule.id, now=saved.now, **kwargs)


@pytest.mark.parametrize("job_type,targets", [("raid", ["archive", "collection", "target"]),
                                             ("shuffle", ["target"]),
                                             ("raid_and_shuffle", ["archive", "collection", "target"]),
                                             ("raid_and_drip", ["archive", "collection", "target"]),
                                             ("rotate", ["archive", "target"]),
                                             ("drip", ["collection", "target"])])
def test_all_six_declared_routines_capture_complete_sorted_targets(saved, job_type, targets):
    saved.schedule.job_type = job_type
    saved.storage.commit()

    command = capture(saved)

    assert command.definition.schedule_id == saved.schedule.id
    assert command.definition.job_type == job_type
    assert command.reserved_targets == targets
    assert command.playlist_id == command.definition.target_playlist_id == "target"
    assert command.config.link.id == saved.link.id
    assert command.config.pair.id == saved.pair.id
    assert [source.source_type for source in command.config.sources] == ["external", "search_query"]
    assert [(lock.position, lock.track_uri) for lock in command.config.locks] == [(0, uri(1)), (4, uri(2))]


def test_capture_uses_only_explicit_session_selects_without_spotify_or_autoflush(saved):
    statements = []
    event.listen(saved.engine, "before_cursor_execute", lambda connection, cursor, statement, *args:
                 statements.append(statement))
    saved.schedule.id  # Resolve expired fixture attributes before dirtying the caller's session.
    owner = saved.storage.get(User, 1)
    owner.display_name = "Unflushed local change"
    statements.clear()
    with patch.object(SpotifyAPI, "__init__", autospec=True, side_effect=AssertionError("Spotify constructor")) as api:
        command = capture(saved)
    assert command.definition.target_playlist_name == "Target"
    api.assert_not_called()
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert owner.display_name == "Unflushed local change"
    assert owner in saved.storage.dirty


@pytest.mark.parametrize("owner,schedule_id", [(2, None), (1, 999999)])
def test_missing_or_foreign_schedule_is_not_found(saved, owner, schedule_id):
    from shuffify.services.operations.commands.routine_capture import CaptureNotFound, capture_schedule

    with pytest.raises(CaptureNotFound):
        capture_schedule(saved.storage, owner, schedule_id or saved.schedule.id, now=saved.now)


def test_disabled_manual_schedule_keeps_raw_settings_and_does_not_capture_ui_or_credentials(saved):
    saved.schedule.algorithm_params = ["invalid", {"nested": [True, None, 3]}]
    saved.schedule.source_playlist_ids = {"invalid": [False, 3.5]}
    saved.storage.commit()

    document = capture(saved).model_dump(mode="json")

    assert document["triggered_by"] == "manual"
    assert document["definition"]["algorithm_params"] == ["invalid", {"nested": [True, None, 3]}]
    assert document["definition"]["source_playlist_ids"] == {"invalid": [False, 3.5]}
    for key in ("is_enabled", "schedule_value", "last_run_at", "user_id", "spotify_id", "encrypted_refresh_token"):
        assert key not in document and key not in document["definition"]


def test_foreign_associations_and_other_target_rows_never_enter_configuration(saved):
    saved.storage.delete(saved.link)
    saved.storage.delete(saved.pair)
    for source in saved.sources:
        saved.storage.delete(source)
    saved.storage.add_all([
        RaidPlaylistLink(user_id=2, target_playlist_id="target", raid_playlist_id="foreign-collection"),
        PlaylistPair(user_id=2, production_playlist_id="target", archive_playlist_id="foreign-archive"),
        UpstreamSource(user_id=2, target_playlist_id="target", source_playlist_id="foreign-source"),
        UpstreamSource(user_id=1, target_playlist_id="other-target", source_playlist_id="wrong-target-source"),
        TrackLock(user_id=2, spotify_playlist_id="target", track_uri=uri(9), position=9),
    ])
    saved.storage.commit()

    command = capture(saved)

    assert command.config.link is command.config.pair is None
    assert command.config.sources == []
    assert command.reserved_targets == ["target"]
    assert all(lock.track_uri != uri(9) for lock in command.config.locks)


@pytest.mark.parametrize("kind,review_kind,targets", [("raid", "raid", ["archive", "collection", "target"]),
                                                      ("drip", "drip", ["collection", "target"]),
                                                      ("raid_and_drip", "raid_and_drip",
                                                       ["archive", "collection", "target"])])
def test_direct_review_kind_and_forced_drip_preserve_link(saved, kind, review_kind, targets):
    from shuffify.services.operations.commands.routine_capture import capture_direct, command_parts, command_scope

    command = capture_direct(saved.storage, 1, "target", kind, source_playlist_ids=["extra"], now=saved.now)
    rows = {"link": saved.link, "pair": saved.pair, "sources": saved.sources}
    expected = direct_parts(kind, rows, ["extra"])
    assert command.review_kind == review_kind
    assert command.definition.schedule_id is None
    assert command.reserved_targets == targets
    assert command_parts(command) == expected
    assert command_scope(command) == direct_scope(kind, "target", expected)
    if kind != "raid":
        assert command.definition.algorithm_params == {"force": True}
        assert command_parts(command)["drip"]["gate"] == "forced"
    assert saved.link.drip_enabled is False


def test_review_scope_uses_the_original_saved_routine_builders(saved):
    from shuffify.services.operations.commands.routine_capture import command_parts, command_scope, require_review

    command = capture(saved)
    rows = {"link": saved.link, "pair": saved.pair, "sources": saved.sources}
    expected = routine_parts(saved.schedule, ("raid", "drip"), rows)
    scope = schedule_scope(saved.schedule.id, saved.schedule.job_type, "target", expected)
    assert command_parts(command) == expected
    assert command_scope(command) == scope
    reviewed = capture(saved, reviewed={"reviewed_scope": scope})
    assert require_review(reviewed) == expected
    changed = capture(saved, reviewed={"reviewed_scope": "0" * 64})
    from shuffify.services.live_review_scope import ReviewChanged
    with pytest.raises(ReviewChanged):
        require_review(changed)


def test_rotate_digest_normalizes_counts_and_ignores_irrelevant_changes(saved):
    from shuffify.services.operations.commands.routine_capture import assert_revalidated, configuration_digest

    saved.schedule.job_type = "rotate"
    saved.schedule.algorithm_params = {"rotation_count": "05", "target_size": "010", "protect_count": -2}
    saved.storage.commit()
    before = capture(saved)
    saved.schedule.algorithm_params = {"rotation_count": 5, "target_size": 10, "protect_count": 0, "ignored": 1}
    saved.schedule.target_playlist_name = "Renamed"
    saved.schedule.schedule_value = "weekly"
    saved.schedule.is_enabled = True
    saved.schedule.last_status = "failed"
    saved.pair.archive_playlist_name = "Renamed Archive"
    saved.link.drip_count = 7
    saved.storage.commit()
    after = capture(saved)
    assert configuration_digest(before) == configuration_digest(after)
    assert_revalidated(before, after)


def test_direct_forced_drip_digest_ignores_saved_gate_but_tracks_effective_count(saved):
    from shuffify.services.operations.commands.routine_capture import (
        CaptureChanged,
        assert_revalidated,
        capture_direct,
        configuration_digest,
    )

    before = capture_direct(saved.storage, 1, "target", "drip", now=saved.now)
    saved.link.drip_enabled = True
    saved.storage.commit()
    gate = capture_direct(saved.storage, 1, "target", "drip", now=saved.now)
    assert configuration_digest(before) == configuration_digest(gate)
    saved.link.drip_count = 4
    saved.storage.commit()
    count = capture_direct(saved.storage, 1, "target", "drip", now=saved.now)
    with pytest.raises(CaptureChanged):
        assert_revalidated(before, count)


def test_per_invalid_step_fallback_preserves_invalid_params_and_valid_raid_equivalences(saved):
    from shuffify.services.operations.commands.routine_capture import assert_revalidated, configuration_digest

    saved.schedule.job_type = "raid_and_shuffle"
    saved.schedule.algorithm_params = ["invalid"]
    saved.schedule.source_playlist_ids = ["b", "a"]
    saved.storage.commit()
    before = capture(saved)
    saved.schedule.source_playlist_ids = ["a", "b"]
    saved.link.drip_enabled = True
    saved.link.drip_count = 9
    saved.storage.commit()
    after = capture(saved)
    assert before.definition.algorithm_params == ["invalid"]
    assert configuration_digest(before) == configuration_digest(after)
    assert_revalidated(before, after)


def test_missing_cap_stays_capturable_and_raw_semantic_fallback_ignores_unused_params(saved):
    from shuffify.services.operations.commands.routine_capture import configuration_digest

    saved.schedule.job_type = "rotate"
    saved.schedule.algorithm_params = {"rotation_count": 2}
    saved.storage.commit()
    before = capture(saved)
    saved.schedule.algorithm_params = {"rotation_count": 2, "unused": "changed"}
    saved.storage.commit()
    after = capture(saved)
    assert configuration_digest(before) == configuration_digest(after)


def test_dependent_step_revalidation_ignores_already_completed_inputs_but_rejects_its_new_target(saved):
    from shuffify.services.operations.commands.routine_capture import CaptureChanged, assert_revalidated

    saved.schedule.job_type = "raid_and_shuffle"
    saved.storage.commit()
    before = capture(saved)
    saved.pair.archive_playlist_id = "different-archive"
    saved.storage.commit()
    after = capture(saved)
    assert_revalidated(before, after, steps=("shuffle",))
    with pytest.raises(CaptureChanged):
        assert_revalidated(before, after)


def test_recapture_reads_current_database_even_when_explicit_session_identity_map_is_stale(saved):
    from shuffify.services.operations.commands.routine_capture import CaptureChanged, recapture

    before = capture(saved)
    with Session(saved.engine) as another, another.begin():
        link = another.scalar(select(RaidPlaylistLink).where(RaidPlaylistLink.id == saved.link.id))
        link.raid_playlist_id = "replacement"
    assert saved.link.raid_playlist_id == "collection"
    with pytest.raises(CaptureChanged):
        recapture(saved.storage, 1, before, now=saved.now)


def test_direct_missing_link_or_sources_has_typed_rejection_and_reviewed_loss_stays_comparable(saved):
    from shuffify.services.operations.commands.routine_capture import CaptureRejected, capture_direct

    saved.storage.delete(saved.link)
    for source in saved.sources:
        saved.storage.delete(source)
    saved.storage.commit()
    with pytest.raises(CaptureRejected) as link:
        capture_direct(saved.storage, 1, "target", "drip", now=saved.now)
    assert link.value.code == "link_required"
    with pytest.raises(CaptureRejected) as sources:
        capture_direct(saved.storage, 1, "target", "raid", now=saved.now)
    assert sources.value.code == "no_sources"
    reviewed = capture_direct(saved.storage, 1, "target", "drip", reviewed={"reviewed_scope": "0" * 64},
                              now=saved.now)
    assert reviewed.config.link is None


def test_direct_none_and_explicit_empty_sources_keep_the_existing_distinction(saved):
    from shuffify.services.operations.commands.routine_capture import CaptureRejected, capture_direct

    default = capture_direct(saved.storage, 1, "target", "raid", now=saved.now)
    assert default.definition.source_playlist_ids is None
    with pytest.raises(CaptureRejected) as empty:
        capture_direct(saved.storage, 1, "target", "raid", source_playlist_ids=[], now=saved.now)
    assert empty.value.code == "no_sources"


def test_unreviewed_composite_without_link_can_collect_before_its_drip_failure(saved):
    from shuffify.services.operations.commands.routine_capture import capture_direct

    saved.storage.delete(saved.link)
    saved.storage.commit()

    command = capture_direct(saved.storage, 1, "target", "raid_and_drip", now=saved.now)

    assert command.config.link is None
    assert command.reserved_targets == ["archive", "target"]
    assert command.steps == ("raid", "drip")


@pytest.mark.parametrize("field,value", [("user_id", 2), ("method", "DELETE"), ("path", "/me/playlists"),
                                        ("access_token", "not-a-token"), ("reserved_targets", ["foreign"])])
def test_private_command_rejects_unrecognized_client_intent_fields(saved, field, value):
    command = capture(saved)
    with pytest.raises(ValidationError):
        type(command).model_validate({**command.model_dump(), field: value})
    with pytest.raises(ValidationError):
        type(command).model_validate({"playlist_id": "target"})


def test_closed_nested_models_strict_ids_bind_main_target_and_review_kind(saved):
    command = capture(saved)
    payload = command.model_dump()
    payload["definition"]["schedule_id"] = str(saved.schedule.id)
    with pytest.raises(ValidationError):
        type(command).model_validate(payload)
    payload = command.model_dump()
    payload["definition"]["target_playlist_id"] = "other-target"
    with pytest.raises(ValidationError):
        type(command).model_validate(payload)
    payload = command.model_dump()
    payload["config"]["link"]["user_id"] = 2
    with pytest.raises(ValidationError):
        type(command).model_validate(payload)
    with pytest.raises(ValidationError):
        capture(saved, reviewed={"reviewed_scope": "0" * 64, "reviewed_history": "1" * 64})


def test_source_and_definition_mutation_after_capture_cannot_change_saved_command(saved):
    command = capture(saved)
    saved.schedule.algorithm_params["changed"] = True
    saved.sources[0].source_name = "Changed"
    assert command.definition.algorithm_params == {}
    assert command.config.sources[0].source_name == "Catalog"


def test_changed_declared_job_type_is_a_typed_configuration_rejection(saved):
    from shuffify.services.operations.commands.routine_capture import CaptureChanged, recapture

    before = capture(saved)
    saved.schedule.job_type = "shuffle"
    saved.storage.commit()

    with pytest.raises(CaptureChanged):
        recapture(saved.storage, 1, before, steps=("drip",), now=saved.now)


def test_direct_drip_retains_owned_cached_target_name_before_any_spotify_read(saved):
    from shuffify.services.operations.commands.routine_capture import capture_direct

    command = capture_direct(saved.storage, 1, "target", "drip", now=saved.now)

    assert command.definition.target_playlist_name == "Target"


@pytest.mark.parametrize("job_type", ["raid", "shuffle", "raid_and_shuffle", "raid_and_drip", "rotate", "drip"])
def test_all_saved_review_parts_and_scopes_match_the_existing_builders(saved, job_type):
    from shuffify.services.operations.commands.routine_capture import command_parts, command_scope

    saved.schedule.job_type = job_type
    saved.schedule.algorithm_params = {"target_size": 10, "rotation_count": "2"} if job_type == "rotate" else {}
    saved.storage.commit()
    command = capture(saved)
    rows = {"link": saved.link, "pair": saved.pair, "sources": saved.sources,
            "locks": {0: uri(1), 4: uri(2)}, "locked": {uri(1), uri(2)}}
    expected = routine_parts(saved.schedule, command.steps, rows)

    assert command_parts(command) == expected
    assert command_scope(command) == schedule_scope(saved.schedule.id, job_type, "target", expected)


def test_expiring_active_lock_changes_only_steps_which_consume_locks(saved):
    from shuffify.services.operations.commands.routine_capture import CaptureChanged, recapture

    saved.schedule.job_type = "raid_and_shuffle"
    saved.storage.commit()
    before = capture(saved)
    later = saved.now + timedelta(seconds=2)
    assert recapture(saved.storage, 1, before, steps=("raid",), now=later).config.locks != before.config.locks
    with pytest.raises(CaptureChanged):
        recapture(saved.storage, 1, before, steps=("shuffle",), now=later)


def test_rotation_lock_positions_do_not_change_its_uri_only_scope(saved):
    from shuffify.services.operations.commands.routine_capture import assert_revalidated, configuration_digest

    saved.schedule.job_type = "rotate"
    saved.schedule.algorithm_params = {"target_size": 10}
    saved.storage.commit()
    before = capture(saved)
    saved.locks[0].position = 10
    saved.storage.commit()
    after = capture(saved)

    assert configuration_digest(before) == configuration_digest(after)
    assert_revalidated(before, after)


def test_source_display_and_resolution_metadata_do_not_change_execution_scope(saved):
    from shuffify.services.operations.commands.routine_capture import assert_revalidated

    before = capture(saved)
    saved.sources[0].source_name = "Renamed"
    saved.sources[0].source_url = "https://open.spotify.com/playlist/renamed"
    saved.sources[0].last_track_count = 999
    saved.sources[0].last_resolve_status = "failed"
    saved.storage.commit()

    assert_revalidated(before, capture(saved))


def test_invalid_saved_count_remains_capturable_with_closed_gate_and_selective_fallback(saved):
    from shuffify.services.operations.commands.routine_capture import configuration_digest

    saved.schedule.job_type = "drip"
    saved.schedule.algorithm_params = {"drip_count": "invalid"}
    saved.storage.commit()
    before = capture(saved)
    saved.schedule.algorithm_params = {"drip_count": "invalid", "unused": {"changed": True}}
    saved.storage.commit()
    after = capture(saved)

    assert before.definition.algorithm_params == {"drip_count": "invalid"}
    assert before.config.link.drip_enabled is False
    assert configuration_digest(before) == configuration_digest(after)


def test_dependent_direct_drip_recapture_does_not_reapply_completed_collect_admission_guards(saved):
    from shuffify.services.operations.commands.routine_capture import capture_direct, recapture

    before = capture_direct(saved.storage, 1, "target", "raid_and_drip", now=saved.now)
    for source in saved.sources:
        saved.storage.delete(source)
    saved.storage.commit()

    after = recapture(saved.storage, 1, before, steps=("drip",), now=saved.now)

    assert after.config.sources == []
    assert after.config.link == before.config.link


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), {"invalid": float("inf")}])
def test_private_raw_params_still_require_canonical_finite_json(saved, invalid):
    command = capture(saved)
    document = command.model_dump()
    document["definition"]["algorithm_params"] = invalid
    with pytest.raises(ValidationError):
        type(command).model_validate(document)
