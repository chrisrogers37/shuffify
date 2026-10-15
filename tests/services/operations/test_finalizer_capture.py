"""Finalizer commands capture exact owned local state, without provider or session effects."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from shuffify.models.db import PendingRaidTrack, PlaylistPair, RaidPlaylistLink, User, db
from shuffify.services.auth_service import AuthService
from shuffify.spotify.api import SpotifyAPI

A, B, C = ["spotify:track:" + value * 22 for value in "abc"]


@pytest.fixture
def saved(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'finalizers.sqlite'}")
    db.metadata.create_all(engine)
    with Session(engine) as storage:
        storage.add_all([User(id=1, spotify_id="owner"), User(id=2, spotify_id="other"),
                         User(id=3, spotify_id="third")])
        storage.flush()
        link = RaidPlaylistLink(id=10, user_id=1, target_playlist_id="target", raid_playlist_id="collection")
        pair = PlaylistPair(id=20, user_id=1, production_playlist_id="target", archive_playlist_id="archive")
        rows = [PendingRaidTrack(id=31, user_id=1, target_playlist_id="target", track_uri=A,
                                 track_name="A", source_playlist_id="discovery", status="pending"),
                PendingRaidTrack(id=32, user_id=1, target_playlist_id="target", track_uri=B,
                                 track_name="B", status="promoted"),
                PendingRaidTrack(id=33, user_id=2, target_playlist_id="target", track_uri=A,
                                 track_name="Other owner", status="pending"),
                PendingRaidTrack(id=34, user_id=1, target_playlist_id="other", track_uri=A,
                                 track_name="Other target", status="pending"),
                PendingRaidTrack(id=35, user_id=1, target_playlist_id="target", track_uri=C,
                                 track_name="Dismissed", status="dismissed")]
        storage.add_all([link, pair, *rows])
        storage.commit()
        yield SimpleNamespace(engine=engine, storage=storage, link=link, pair=pair, rows=rows)
    engine.dispose()


def capture(saved, kind="raids", **overrides):
    from shuffify.services.operations.commands.finalizer_capture import capture_append, capture_raids, capture_restore

    values = {"source_playlist_id": "collection" if kind == "raids" else "archive", "target_track_uris": [A, B, A]}
    values.update({"track_ids": [32, 31, 31]} if kind == "raids" else {"track_uris": [A, A, C]})
    if kind == "append":
        values["baseline_track_uris"] = [C, C]
    values.update(overrides)
    return {"raids": capture_raids, "append": capture_append, "restore": capture_restore}[kind](
        saved.storage, 1, "target", **values,
    )


@pytest.mark.parametrize("kind,targets,binding", [
    ("raids", ["collection", "target"], 10),
    ("append", ["archive", "target"], 20),
    ("restore", ["archive", "target"], 20),
])
def test_commands_capture_only_the_required_owned_binding_and_sorted_targets(saved, kind, targets, binding):
    command = capture(saved, kind)
    assert command.reserved_targets == targets
    assert command.playlist_id == "target"
    assert command.source_binding.id == binding
    assert command.source_binding.playlist_id == command.source_playlist_id == command.linked_source_playlist_id
    assert command.target_track_uris == [A, B, A]
    assert command.version == 1
    assert not {"user_id", "session", "api", "method", "path", "inspect_only"}.intersection(command.model_fields)


def test_raids_selection_is_exact_sorted_unique_ids_with_owned_uri_status_and_no_provenance_target(saved):
    command = capture(saved)
    assert [row.model_dump() for row in command.pending_rows] == [
        {"id": 31, "track_uri": A, "status": "pending"},
        {"id": 32, "track_uri": B, "status": "promoted"},
    ]
    assert command.track_ids == [31, 32]
    assert "discovery" not in str(command.model_dump())
    assert "archive" not in command.reserved_targets


@pytest.mark.parametrize("kind", ["append", "restore"])
def test_archive_commands_preserve_order_and_every_duplicate_occurrence(saved, kind):
    command = capture(saved, kind)
    assert command.track_uris == [A, A, C]
    if kind == "append":
        assert command.baseline_track_uris == [C, C]


def test_null_source_without_owned_link_is_valid_db_only_pending_finalize(saved):
    saved.storage.delete(saved.link)
    saved.storage.commit()
    command = capture(saved, source_playlist_id=None)
    assert command.source_playlist_id is command.linked_source_playlist_id is command.source_binding is None
    assert command.reserved_targets == ["target"]
    assert command.track_ids == [31, 32]


@pytest.mark.parametrize("kind,source", [("raids", None), ("raids", "discovery"), ("append", "collection"),
                                        ("restore", "other")])
def test_supplied_source_never_grants_write_access_outside_owned_binding(saved, kind, source):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected

    with pytest.raises(FinalizerCaptureRejected) as caught:
        capture(saved, kind, source_playlist_id=source)
    assert (caught.value.code, caught.value.http_status, caught.value.retryable) == ("source_changed", 409, False)


@pytest.mark.parametrize("kind", ["append", "restore"])
def test_foreign_archive_association_is_not_a_source_binding(saved, kind):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected

    saved.pair.user_id = 2
    saved.storage.commit()
    with pytest.raises(FinalizerCaptureRejected, match="linked source"):
        capture(saved, kind)


@pytest.mark.parametrize("kind", ["raids", "append", "restore"])
def test_source_equal_to_target_is_rejected_before_any_worker_reads(saved, kind):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected

    if kind == "raids":
        saved.link.raid_playlist_id = "target"
    else:
        saved.pair.archive_playlist_id = "target"
    saved.storage.commit()
    with pytest.raises(FinalizerCaptureRejected) as caught:
        capture(saved, kind, source_playlist_id="target")
    assert caught.value.code == "invalid_source"


@pytest.mark.parametrize("ids", [[], [999], [31, 999], [33], [34], [35]])
def test_any_missing_foreign_wrong_target_or_dismissed_selected_row_rejects_entire_selection(saved, ids):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected

    with pytest.raises(FinalizerCaptureRejected) as caught:
        capture(saved, track_ids=ids)
    assert (caught.value.code, caught.value.http_status) == ("invalid_track_selection", 409)


def test_capture_is_select_only_without_spotify_auth_flush_commit_or_identity_map_refresh(saved):
    owner = saved.storage.get(User, 1)
    owner.display_name = "Unflushed local value"
    statements = []
    event.listen(saved.engine, "before_cursor_execute", lambda connection, cursor, statement, *args:
                 statements.append(statement))
    with (patch.object(SpotifyAPI, "__init__", autospec=True, side_effect=AssertionError("Spotify")) as api,
          patch.object(AuthService, "get_authenticated_api", autospec=True, side_effect=AssertionError("Auth")) as auth,
          patch.object(saved.storage, "commit", side_effect=AssertionError("Commit"))):
        capture(saved)
        capture(saved, "append")
        capture(saved, "restore")
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert owner in saved.storage.dirty
    assert owner.display_name == "Unflushed local value"
    api.assert_not_called()
    auth.assert_not_called()


def test_database_failure_has_authored_error_without_private_sql_or_exception(saved):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected

    with patch.object(saved.storage, "execute", side_effect=OperationalError("private SQL", {}, Exception("secret"))):
        with pytest.raises(FinalizerCaptureRejected) as caught:
            capture(saved)
    error = caught.value
    assert (error.code, error.http_status, error.retryable) == ("database_read_failed", 502, True)
    assert "secret" not in str(error) and "private" not in str(error)


@pytest.mark.parametrize("kind", ["raids", "append", "restore"])
def test_revalidation_rejects_deleted_relinked_or_replaced_association_identity(saved, kind):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected, recapture

    command = capture(saved, kind)
    row = saved.link if kind == "raids" else saved.pair
    saved.storage.delete(row)
    saved.storage.flush()
    replacement = (RaidPlaylistLink(id=11, user_id=1, target_playlist_id="target", raid_playlist_id="collection")
                   if kind == "raids" else
                   PlaylistPair(id=21, user_id=1, production_playlist_id="target", archive_playlist_id="archive"))
    saved.storage.add(replacement)
    saved.storage.commit()
    with pytest.raises(FinalizerCaptureRejected) as caught:
        recapture(saved.storage, 1, command)
    assert caught.value.code == "source_changed"


@pytest.mark.parametrize("change", ["dismissed", "deleted", "owner", "target", "uri"])
def test_revalidation_refuses_selected_row_drift_without_expanding_selection(saved, change):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected, recapture

    command = capture(saved)
    row = saved.rows[0]
    if change == "deleted":
        saved.storage.delete(row)
    else:
        attribute = {
            "dismissed": "status", "owner": "user_id", "target": "target_playlist_id", "uri": "track_uri",
        }[change]
        value = {"dismissed": "dismissed", "owner": 3, "target": "changed", "uri": "spotify:track:" + "e" * 22}[change]
        setattr(row, attribute, value)
    saved.storage.commit()
    with pytest.raises(FinalizerCaptureRejected) as caught:
        recapture(saved.storage, 1, command)
    assert caught.value.code == "invalid_track_selection"


def test_pending_to_promoted_is_valid_before_first_prepare_and_does_not_mutate_admission_capture(saved):
    from shuffify.services.operations.commands.finalizer_capture import recapture

    command = capture(saved)
    saved.rows[0].status = "promoted"
    saved.storage.commit()
    current = recapture(saved.storage, 1, command)
    assert current.pending_rows[0].status == "promoted"
    assert command.pending_rows[0].status == "pending"
    assert current.track_ids == command.track_ids


def test_promoted_to_pending_reverse_transition_is_refused(saved):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected, recapture

    command = capture(saved)
    saved.rows[1].status = "pending"
    saved.storage.commit()
    with pytest.raises(FinalizerCaptureRejected) as caught:
        recapture(saved.storage, 1, command)
    assert caught.value.code == "invalid_track_selection"


def test_new_inbox_rows_do_not_expand_a_captured_finalize_all_selection(saved):
    from shuffify.services.operations.commands.finalizer_capture import recapture

    command = capture(saved)
    saved.storage.add(PendingRaidTrack(user_id=1, target_playlist_id="target", track_uri="spotify:track:" + "d" * 22,
                                      track_name="Later", status="pending"))
    saved.storage.commit()
    assert recapture(saved.storage, 1, command).track_ids == [31, 32]


@pytest.mark.parametrize("kind", ["raids", "append", "restore"])
def test_names_statistics_and_unrelated_rows_are_not_configuration_changes(saved, kind):
    from shuffify.services.operations.commands.finalizer_capture import recapture

    command = capture(saved, kind)
    saved.link.raid_playlist_name = saved.pair.archive_playlist_name = "Renamed"
    saved.link.drip_count = 17
    saved.rows[0].source_playlist_id = "newprovenance"
    saved.storage.commit()
    assert recapture(saved.storage, 1, command) == command


@pytest.mark.parametrize("kind", ["raids", "append", "restore"])
@pytest.mark.parametrize("field,value", [("user_id", 9), ("method", "DELETE"), ("session", {}), ("inspect_only", True),
                                        ("version", True), ("playlist_id", "target/path")])
def test_private_commands_reject_raw_authority_fields_inspection_or_invalid_target(saved, kind, field, value):
    command = capture(saved, kind)
    with pytest.raises(ValidationError):
        type(command).model_validate({**command.model_dump(), field: value})


def test_private_rows_reject_coercion_extra_authority_noncanonical_selection_and_bad_uris(saved):
    command = capture(saved)
    for values in ([{**command.pending_rows[0].model_dump(), "id": True}],
                   [{**command.pending_rows[0].model_dump(), "user_id": 1}],
                   [{**command.pending_rows[0].model_dump(), "track_uri": "invalid"}],
                   [row.model_dump() for row in reversed(command.pending_rows)],
                   [command.pending_rows[0].model_dump()] * 2):
        with pytest.raises(ValidationError):
            type(command).model_validate({**command.model_dump(), "pending_rows": values})


def test_capture_does_not_read_or_flush_pending_local_binding_edits(saved):
    command = capture(saved)
    saved.link.raid_playlist_id = "unflushed"
    assert capture(saved) == command
    assert saved.link in saved.storage.dirty
    assert saved.link.raid_playlist_id == "unflushed"
    saved.storage.rollback()
    assert saved.storage.scalar(select(RaidPlaylistLink.raid_playlist_id)) == "collection"


def test_more_than_two_hundred_exact_rows_are_captured_once_without_implicit_other_selection(saved):
    rows = [PendingRaidTrack(user_id=1, target_playlist_id="target", track_uri="spotify:track:" + str(n).zfill(22),
                            track_name="Selected", status="pending") for n in range(201)]
    saved.storage.add_all(rows)
    saved.storage.commit()
    ids = [row.id for row in rows]
    command = capture(saved, track_ids=list(reversed(ids)) + ids[:1])
    assert command.track_ids == ids
    assert len(command.pending_rows) == 201
    assert not {31, 32, 35}.intersection(command.track_ids)


@pytest.mark.parametrize("kind", ["raids", "append", "restore"])
def test_source_playlist_change_or_deletion_is_refused_before_using_replacement(saved, kind):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected, recapture

    command = capture(saved, kind)
    row = saved.link if kind == "raids" else saved.pair
    setattr(row, "raid_playlist_id" if kind == "raids" else "archive_playlist_id", "replacement")
    saved.storage.commit()
    with pytest.raises(FinalizerCaptureRejected) as caught:
        recapture(saved.storage, 1, command)
    assert caught.value.code == "source_changed"
    saved.storage.delete(row)
    saved.storage.commit()
    with pytest.raises(FinalizerCaptureRejected) as caught:
        recapture(saved.storage, 1, command)
    assert caught.value.code == "source_changed"


@pytest.mark.parametrize("user_id", [True, 0, -1, 999])
def test_unknown_or_invalid_user_has_authored_local_not_found(saved, user_id):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureNotFound, capture_raids

    with pytest.raises(FinalizerCaptureNotFound) as caught:
        capture_raids(saved.storage, user_id, "target", source_playlist_id="collection",
                      target_track_uris=[A, B], track_ids=[31])
    assert caught.value.http_status == 404


@pytest.mark.parametrize("kind", ["raids", "append", "restore"])
def test_command_lists_and_binding_are_detached_from_caller_values(saved, kind):
    target, payload, baseline = [A, B, A], [B, B], [C]
    values = {"target_track_uris": target}
    if kind != "raids":
        values["track_uris"] = payload
    if kind == "append":
        values["baseline_track_uris"] = baseline
    command = capture(saved, kind, **values)
    target.clear()
    payload.clear()
    baseline.clear()
    assert command.target_track_uris == [A, B, A]
    if kind != "raids":
        assert command.track_uris == [B, B]
    if kind == "append":
        assert command.baseline_track_uris == [C]
    with pytest.raises(ValidationError):
        type(command).model_validate({**command.model_dump(), "source_binding": {
            **command.source_binding.model_dump(), "user_id": 1,
        }})


def test_db_read_wrapper_preserves_chained_durable_control_instead_of_authored_fallback(saved):
    from shuffify.services.operations.commands.finalizer_capture import capture_raids
    from shuffify.services.operations.repository import LeaseLost

    failure = OperationalError("SELECT", {}, Exception("driver wrapper"))
    failure.__cause__ = LeaseLost("Lease lost")
    with patch.object(saved.storage, "execute", side_effect=failure):
        with pytest.raises(LeaseLost):
            capture_raids(saved.storage, 1, "target", source_playlist_id="collection",
                          target_track_uris=[A], track_ids=[31])


def test_db_only_capture_cannot_follow_a_link_created_after_admission(saved):
    from shuffify.services.operations.commands.finalizer_capture import FinalizerCaptureRejected, recapture

    saved.storage.delete(saved.link)
    saved.storage.commit()
    command = capture(saved, source_playlist_id=None)
    saved.storage.add(RaidPlaylistLink(id=11, user_id=1, target_playlist_id="target",
                                      raid_playlist_id="latecollection"))
    saved.storage.commit()
    with pytest.raises(FinalizerCaptureRejected) as caught:
        recapture(saved.storage, 1, command)
    assert caught.value.code == "source_changed"
    assert command.reserved_targets == ["target"]


@pytest.mark.parametrize("kind", ["raids", "append", "restore"])
def test_commands_round_trip_through_actual_versioned_envelope_and_exact_lease_validation(saved, kind):
    from shuffify.services.operations.commands.envelope import validate_command

    command = capture(saved, kind)
    operation = SimpleNamespace(payload={"version": 1, "command": command.model_dump(mode="json")},
                                kind=command.kind, targets=command.reserved_targets)
    runtime = SimpleNamespace(operation=operation, lease=SimpleNamespace(targets=tuple(command.reserved_targets)))
    assert validate_command(runtime, type(command)) == command
    operation.targets = ["target"]
    with pytest.raises(ValueError, match="lease"):
        validate_command(runtime, type(command))


@pytest.mark.parametrize("version", [True, "1", 2, None])
def test_envelope_rejects_noncanonical_version_before_any_provider_access(saved, version):
    from shuffify.services.operations.commands.envelope import validate_command

    command = capture(saved)
    operation = SimpleNamespace(payload={"version": version, "command": command.model_dump(mode="json")})
    with pytest.raises(ValueError, match="envelope"):
        validate_command(SimpleNamespace(operation=operation), type(command))


def test_capture_defers_actual_target_order_and_membership_proofs_to_worker(saved):
    command = capture(saved, target_track_uris=[], track_ids=[32])
    assert command.target_track_uris == []
    assert command.pending_rows[0].track_uri == B
