"""Local bookkeeping and its receipt commit once under the operation's lease fence."""

import pytest

from shuffify.enums import SnapshotType
from shuffify.models.db import ActivityLog, PlaylistSnapshot, TrackLock, User, UserSettings, db
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from shuffify.services.operations.repository import LeaseLost, OperationRepository


@pytest.fixture
def local_operation(db_app):
    user = User(spotify_id="bookkeeping-user")
    db.session.add(user)
    db.session.commit()
    repository = OperationRepository()
    operation = repository.enqueue(kind="example", user_id=user.id, targets=["playlist"], payload={})
    return repository, repository.claim(operation_id=operation.id), user.id


def test_local_step_receipt_and_effect_commit_together_and_replay_without_callback(local_operation):
    repository, lease, user_id = local_operation
    calls = []

    def apply(session):
        calls.append(True)
        return OperationBookkeeping(session, user_id, lease.operation_id).activity("shuffle", "Shuffled playlist")

    first = repository.local_step(lease, "activity", apply)
    assert repository.local_step(lease, "activity", apply) == first
    assert calls == [True]
    assert ActivityLog.query.count() == 1
    assert repository.get(lease.operation_id).journal["runtime"]["local_steps"]["activity"] == first


def test_failed_or_non_json_callback_rolls_back_effect_and_receipt(local_operation):
    repository, lease, user_id = local_operation

    def invalid(session):
        OperationBookkeeping(session, user_id, lease.operation_id).activity("shuffle", "Never committed")
        return object()

    with pytest.raises(ValueError):
        repository.local_step(lease, "invalid", invalid)
    assert ActivityLog.query.count() == 0
    assert repository.get(lease.operation_id).journal == {}


def test_lost_lease_never_runs_local_callback(local_operation):
    repository, lease, _user_id = local_operation
    repository.finish(lease, "failed")
    with pytest.raises(LeaseLost):
        repository.local_step(lease, "unsafe", lambda session: pytest.fail("Lost leases cannot stage effects"))


def test_snapshot_creation_retention_and_job_tag_are_one_replayable_transaction(local_operation):
    repository, lease, user_id = local_operation
    db.session.add(UserSettings(user_id=user_id, max_snapshots_per_playlist=1))
    db.session.commit()

    def snapshot(session):
        return OperationBookkeeping(session, user_id, lease.operation_id).snapshot(
            "playlist", "Playlist", ["before"], SnapshotType.AUTO_PRE_SHUFFLE, auto=True,
        )

    first = repository.local_step(lease, "snapshot", snapshot)
    assert repository.local_step(lease, "snapshot", snapshot)["id"] == first["id"]
    assert PlaylistSnapshot.query.count() == 1
    assert PlaylistSnapshot.query.one().job_execution_id == lease.operation_id
    second = repository.local_step(lease, "next-snapshot", snapshot)
    assert PlaylistSnapshot.query.one().id == second["id"]
    assert second["id"] != first["id"]


def test_legacy_snapshot_with_zero_retention_deletions_finishes_its_transaction(local_operation):
    from shuffify.services.playlist_snapshot_service import PlaylistSnapshotService

    repository, lease, user_id = local_operation
    snapshot = PlaylistSnapshotService.create_snapshot(user_id, "playlist", "Playlist", ["before"], SnapshotType.MANUAL)
    assert snapshot.id is not None
    assert repository.local_step(lease, "after-legacy-snapshot", lambda session: {"completed": True}) == {
        "completed": True,
    }


def test_snapshot_lookup_can_use_the_callers_explicit_owned_transaction(local_operation):
    from shuffify.services.playlist_snapshot_service import PlaylistSnapshotNotFoundError, PlaylistSnapshotService

    repository, lease, user_id = local_operation

    def lookup(session):
        result = OperationBookkeeping(session, user_id, lease.operation_id).snapshot(
            "playlist", "Playlist", ["before"], SnapshotType.MANUAL,
        )
        selected = PlaylistSnapshotService.get_snapshot(result["id"], user_id, session=session)
        with pytest.raises(PlaylistSnapshotNotFoundError):
            PlaylistSnapshotService.get_snapshot(result["id"], user_id + 1, session=session)
        return {"id": selected.id, "track_uris": selected.track_uris}

    assert repository.local_step(lease, "lookup", lookup)["track_uris"] == ["before"]


def test_lock_reconciliation_is_receipted_and_uses_the_explicit_session(local_operation):
    repository, lease, user_id = local_operation
    db.session.add_all([
        TrackLock(user_id=user_id, spotify_playlist_id="playlist", track_uri="kept", position=0, lock_tier="super"),
        TrackLock(user_id=user_id, spotify_playlist_id="playlist", track_uri="removed", position=1, lock_tier="super"),
    ])
    db.session.commit()
    result = repository.local_step(lease, "locks", lambda session: OperationBookkeeping(
        session, user_id, lease.operation_id,
    ).reconcile_locks("playlist", ["other", "kept"]))
    assert result == 1
    db.session.expire_all()
    assert [(lock.track_uri, lock.position) for lock in TrackLock.query.all()] == [("kept", 1)]


def test_optional_failure_uses_savepoint_and_durably_records_fallback(local_operation):
    repository, lease, user_id = local_operation

    def execute(session):
        bookkeeping = OperationBookkeeping(session, user_id, lease.operation_id)

        def broken():
            bookkeeping.activity("shuffle", "Must roll back")
            raise RuntimeError("Example local failure")

        return bookkeeping.optional(broken, fallback={"status": "failed"})

    assert repository.local_step(lease, "optional", execute) == {"status": "failed"}
    assert repository.local_step(lease, "optional", lambda session: pytest.fail("Already recorded")) == {
        "status": "failed",
    }
    assert ActivityLog.query.count() == 0


def test_optional_must_propagate_lease_loss(local_operation):
    repository, lease, user_id = local_operation

    def execute(session):
        bookkeeping = OperationBookkeeping(session, user_id, lease.operation_id)

        def lost():
            raise LeaseLost("Example loss")

        return bookkeeping.optional(lost)

    with pytest.raises(LeaseLost):
        repository.local_step(lease, "optional", execute)


@pytest.mark.parametrize("wrapped", [False, True])
def test_optional_must_propagate_direct_or_wrapped_durable_abort(local_operation, wrapped):
    from shuffify.services.operations.journal import OperationAborted

    repository, lease, user_id = local_operation

    def execute(session):
        bookkeeping = OperationBookkeeping(session, user_id, lease.operation_id)

        def aborted():
            try:
                raise OperationAborted("example_abort")
            except OperationAborted as error:
                if wrapped:
                    raise RuntimeError("Legacy service wrapper") from error
                raise

        return bookkeeping.optional(aborted)

    with pytest.raises(OperationAborted):
        repository.local_step(lease, "optional-abort", execute)


def test_callback_cannot_commit_before_its_receipt(local_operation):
    repository, lease, user_id = local_operation

    def premature(session):
        OperationBookkeeping(session, user_id, lease.operation_id).activity("shuffle", "Must roll back")
        session.commit()

    with pytest.raises(RuntimeError, match="commit"):
        repository.local_step(lease, "premature", premature)
    assert ActivityLog.query.count() == 0


def test_lease_is_rechecked_after_callback_before_local_effects_commit(local_operation):
    repository, lease, user_id = local_operation
    original_now = repository._now
    count = 0

    def clock(session):
        from datetime import timedelta

        nonlocal count
        count += 1
        return original_now(session) + (timedelta(hours=1) if count > 1 else timedelta())

    repository._now = clock
    with pytest.raises(LeaseLost):
        repository.local_step(lease, "late", lambda session: OperationBookkeeping(
            session, user_id, lease.operation_id,
        ).activity("shuffle", "Must roll back"))
    assert ActivityLog.query.count() == 0
