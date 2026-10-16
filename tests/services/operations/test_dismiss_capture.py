"""Inbox dismissal captures exact owned identities and applies one local receipt."""

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, PendingRaidTrack, RaidPlaylistLink, User, db
from shuffify.services.auth_service import AuthService
from shuffify.spotify.api import SpotifyAPI

A, B, C, D = ["spotify:track:" + value * 22 for value in "abcd"]
NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def capture_module():
    return importlib.import_module("shuffify.services.operations.commands.dismiss_capture")


def schemas():
    return importlib.import_module("shuffify.services.operations.commands.dismiss_schemas")


@pytest.fixture
def saved(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dismiss.sqlite'}")
    db.metadata.create_all(engine)
    with Session(engine) as storage:
        storage.add_all([User(id=1, spotify_id="owner"), User(id=2, spotify_id="other")])
        storage.flush()
        storage.add(RaidPlaylistLink(id=10, user_id=1, target_playlist_id="target", raid_playlist_id="collection"))
        for row_id, owner, target, uri, status in [
            (31, 1, "target", A, "pending"),
            (32, 1, "target", B, "pending"),
            (33, 1, "target", C, "promoted"),
            (34, 1, "target", D, "dismissed"),
            (35, 2, "target", A, "pending"),
            (36, 1, "other", A, "pending"),
        ]:
            storage.add(
                PendingRaidTrack(
                    id=row_id, user_id=owner, target_playlist_id=target, track_uri=uri, track_name=uri, status=status
                )
            )
        storage.commit()
        yield SimpleNamespace(engine=engine, storage=storage)
    engine.dispose()


def capture(saved, ids=(32, 35, 36, 33, 34, 31, 31, 999)):
    return capture_module().capture_dismiss(saved.storage, 1, "target", track_ids=list(ids))


def rows(saved):
    with Session(saved.engine) as observer:
        return {row.id: (row.status, row.resolved_at) for row in observer.scalars(select(PendingRaidTrack))}


def test_selected_capture_preserves_existing_owned_pending_intersection_and_canonical_order(saved):
    command = capture(saved)
    assert command.kind == "pending_dismiss"
    assert command.selection == "selected"
    assert command.track_ids == [31, 32]
    assert [row.model_dump() for row in command.pending_rows] == [
        {"id": 31, "track_uri": A, "status": "pending"},
        {"id": 32, "track_uri": B, "status": "pending"},
    ]
    assert command.source_binding.model_dump() == {"id": 10, "playlist_id": "collection"}
    assert command.reserved_targets == ["collection", "target"]
    assert not {"user_id", "api", "method", "path"}.intersection(command.model_fields)


def test_dismiss_all_freezes_only_current_owned_pending_and_does_not_expand_at_apply(saved):
    command = capture_module().capture_dismiss_all(saved.storage, 1, "target")
    assert command.selection == "all"
    assert command.track_ids == [31, 32]
    saved.storage.add(
        PendingRaidTrack(
            id=40,
            user_id=1,
            target_playlist_id="target",
            track_uri="spotify:track:" + "e" * 22,
            track_name="new",
            status="pending",
        )
    )
    saved.storage.commit()
    receipt = capture_module().dismiss_captured(saved.storage, 1, command, now=NOW)
    assert receipt == {"confirmed_ids": [31, 32], "changed_ids": [31, 32], "affected_count": 2, "track_uris": [A, B]}
    saved.storage.commit()
    assert rows(saved)[40][0] == "pending"


@pytest.mark.parametrize("all_rows", [False, True])
def test_empty_selection_and_missing_link_are_valid_db_only_noops(saved, all_rows):
    saved.storage.delete(saved.storage.get(RaidPlaylistLink, 10))
    for row_id in [31, 32]:
        saved.storage.get(PendingRaidTrack, row_id).status = "dismissed"
    saved.storage.commit()
    command = capture_module().capture_dismiss_all(saved.storage, 1, "target") if all_rows else capture(saved, (999,))
    assert command.pending_rows == ()
    assert command.source_binding is None
    assert command.reserved_targets == ["target"]
    assert capture_module().dismiss_captured(saved.storage, 1, command, now=NOW) == {
        "confirmed_ids": [],
        "changed_ids": [],
        "affected_count": 0,
        "track_uris": [],
    }


def test_capture_and_recapture_have_only_reads_no_autoflush_commit_or_provider_access(saved, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Capture must not flush, commit, construct credentials, or dispatch HTTP")

    monkeypatch.setattr(SpotifyAPI, "__init__", forbidden)
    monkeypatch.setattr(AuthService, "_auth_manager", forbidden)
    existing = saved.storage.get(PendingRaidTrack, 31)
    monkeypatch.setattr(saved.storage, "flush", forbidden)
    monkeypatch.setattr(saved.storage, "commit", forbidden)
    existing.status = "promoted"
    saved.storage.add(User(spotify_id="unflushed"))
    statements = []
    event.listen(saved.engine, "before_cursor_execute", lambda conn, cur, sql, *args: statements.append(sql))
    command = capture(saved)
    current = capture_module().recapture(saved.storage, 1, command)
    assert current.track_ids == [31, 32]
    assert current.pending_rows[0].status == "pending"
    assert existing.status == "promoted"
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


def test_storage_receipt_flushes_without_commit_or_activity_and_rolls_back_with_caller(saved):
    command = capture(saved)
    before = rows(saved)
    result = capture_module().dismiss_captured(saved.storage, 1, command, now=NOW)
    assert result["affected_count"] == 2
    assert rows(saved) == before
    assert saved.storage.scalar(select(PendingRaidTrack.status).where(PendingRaidTrack.id == 31)) == "dismissed"
    assert saved.storage.scalars(select(ActivityLog)).all() == []
    saved.storage.rollback()
    assert rows(saved) == before


def test_repeated_receipt_confirms_same_exact_ids_without_new_changes_or_timestamp(saved):
    command = capture(saved)
    first = capture_module().dismiss_captured(saved.storage, 1, command, now=NOW)
    saved.storage.commit()
    second = capture_module().dismiss_captured(saved.storage, 1, command, now=NOW.replace(hour=13))
    saved.storage.commit()
    assert first["affected_count"] == 2
    assert second == {"confirmed_ids": [31, 32], "changed_ids": [], "affected_count": 0, "track_uris": [A, B]}
    assert rows(saved)[31] == ("dismissed", NOW)
    assert rows(saved)[32] == ("dismissed", NOW)


def test_partial_already_dismissed_selection_reports_only_actual_changes(saved):
    command = capture(saved)
    saved.storage.get(PendingRaidTrack, 31).status = "dismissed"
    saved.storage.commit()
    result = capture_module().dismiss_captured(saved.storage, 1, command, now=NOW)
    assert result["confirmed_ids"] == [31, 32]
    assert result["changed_ids"] == [32]
    assert result["affected_count"] == 1


@pytest.mark.parametrize("drift", ["promoted", "uri", "owner", "target", "deleted"])
def test_row_identity_or_promoted_drift_rejects_all_changes_before_dismissing_anything(saved, drift):
    command = capture(saved)
    row = saved.storage.get(PendingRaidTrack, 32)
    if drift == "promoted":
        row.status = "promoted"
    elif drift == "uri":
        row.track_uri = "spotify:track:" + "e" * 22
    elif drift == "owner":
        row.user_id = 2
    elif drift == "target":
        row.target_playlist_id = "other"
    else:
        saved.storage.delete(row)
    saved.storage.commit()
    before = rows(saved)
    with pytest.raises(capture_module().DismissRejected, match="selected"):
        capture_module().dismiss_captured(saved.storage, 1, command, now=NOW)
    saved.storage.rollback()
    assert rows(saved) == before
    assert rows(saved)[31][0] == "pending"


@pytest.mark.parametrize("drift", ["playlist", "deleted", "replaced", "new"])
def test_owned_association_identity_drift_never_follows_replacement_or_dismisses(saved, drift):
    if drift == "new":
        saved.storage.delete(saved.storage.get(RaidPlaylistLink, 10))
        saved.storage.commit()
    command = capture(saved)
    if drift in ("deleted", "replaced"):
        saved.storage.delete(saved.storage.get(RaidPlaylistLink, 10))
        saved.storage.commit()
    if drift == "playlist":
        saved.storage.get(RaidPlaylistLink, 10).raid_playlist_id = "replacement"
    elif drift in ("new", "replaced"):
        saved.storage.add(
            RaidPlaylistLink(id=20, user_id=1, target_playlist_id="target", raid_playlist_id="collection")
        )
    saved.storage.commit()
    with pytest.raises(capture_module().DismissRejected) as rejected:
        capture_module().dismiss_captured(saved.storage, 1, command, now=NOW)
    assert rejected.value.code == "source_changed"
    assert rows(saved)[31][0] == "pending"


def test_read_only_recapture_allows_own_receipt_transitions_only_when_explicit(saved):
    command = capture(saved)
    frozen = command.model_dump()
    capture_module().dismiss_captured(saved.storage, 1, command, now=NOW)
    saved.storage.commit()
    with pytest.raises(capture_module().DismissRejected):
        capture_module().recapture(saved.storage, 1, command)
    current = capture_module().recapture(saved.storage, 1, command, allow_dismissed=True)
    assert current.pending_rows[0].status == "dismissed"
    assert capture_module().assert_revalidated(command, current, allow_dismissed=True) == current
    assert command.model_dump() == frozen


@pytest.mark.parametrize("user_id", [True, 1.0, "1", 0, 999])
def test_invalid_or_missing_owner_never_captures_or_changes_rows(saved, user_id):
    with pytest.raises(capture_module().DismissRejected):
        capture_module().capture_dismiss(saved.storage, user_id, "target", track_ids=[31])
    assert rows(saved)[31][0] == "pending"


@pytest.mark.parametrize("track_ids", [[True], [1.0], ["31"], [0]])
def test_invalid_private_capture_ids_refuse_instead_of_coercing_foreign_authority(saved, track_ids):
    with pytest.raises(capture_module().DismissRejected):
        capture_module().capture_dismiss(saved.storage, 1, "target", track_ids=track_ids)


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", 1),
        ("unknown", True),
        ("selection", "everything"),
        ("playlist_id", "https://evil"),
        ("source_binding", {"id": True, "playlist_id": "c"}),
    ],
)
def test_closed_private_schema_rejects_extra_or_coercible_authority(saved, field, value):
    document = capture(saved).model_dump(mode="json")
    document[field] = value
    with pytest.raises(ValidationError):
        schemas().PendingDismissCommand.model_validate(document)


@pytest.mark.parametrize("change", ["duplicate", "reverse", "promoted", "dismissed", "bool"])
def test_private_pending_selection_requires_canonical_strict_pending_identities(saved, change):
    document = capture(saved).model_dump(mode="json")
    if change == "duplicate":
        document["pending_rows"].append(document["pending_rows"][0])
    elif change == "reverse":
        document["pending_rows"].reverse()
    elif change == "bool":
        document["pending_rows"][0]["id"] = True
    else:
        document["pending_rows"][0]["status"] = change
    with pytest.raises(ValidationError):
        schemas().PendingDismissCommand.model_validate(document)


def test_required_storage_failure_propagates_without_any_internal_commit(saved, monkeypatch):
    command = capture(saved)
    before = rows(saved)

    def unavailable():
        raise RuntimeError("required receipt failed")

    monkeypatch.setattr(saved.storage, "flush", unavailable)
    with pytest.raises(RuntimeError, match="required receipt"):
        capture_module().dismiss_captured(saved.storage, 1, command, now=NOW)
    saved.storage.rollback()
    assert rows(saved) == before


def test_capture_database_error_is_authored_and_control_errors_propagate(saved, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from shuffify.services.operations.repository import LeaseLost

    def unavailable(*args, **kwargs):
        raise OperationalError("private SQL", {}, Exception("private DB details"))

    monkeypatch.setattr(saved.storage, "execute", unavailable)
    with pytest.raises(capture_module().DismissRejected) as failed:
        capture(saved)
    assert failed.value.code == "database_read_failed"
    assert failed.value.retryable is True
    assert "private" not in str(failed.value)

    def lost(*args, **kwargs):
        raise LeaseLost("fence")

    monkeypatch.setattr(saved.storage, "execute", lost)
    with pytest.raises(LeaseLost):
        capture(saved)


def test_read_only_recapture_never_follows_relinked_collection(saved):
    command = capture(saved)
    capture_module().dismiss_captured(saved.storage, 1, command, now=NOW)
    saved.storage.commit()
    saved.storage.get(RaidPlaylistLink, 10).raid_playlist_id = "replacement"
    saved.storage.commit()
    with pytest.raises(capture_module().DismissRejected) as changed:
        capture_module().recapture(saved.storage, 1, command, allow_dismissed=True)
    assert changed.value.code == "source_changed"
    assert command.source_binding.playlist_id == "collection"


def test_command_is_immutable_and_detached_from_caller_selection(saved):
    ids = [31, 32]
    command = capture_module().capture_dismiss(saved.storage, 1, "target", track_ids=ids)
    ids.append(35)
    assert command.track_ids == [31, 32]
    with pytest.raises((AttributeError, TypeError)):
        command.pending_rows.append(command.pending_rows[0])
    with pytest.raises(ValidationError):
        command.pending_rows[0].track_uri = C
