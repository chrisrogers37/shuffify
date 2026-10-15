"""Asset association receipts use the caller's fenced transaction."""

import pytest
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, PlaylistPair, RaidPlaylistLink, User, db
from shuffify.services.operations.commands.asset_capture import AssetCaptureRejected, capture_asset
from shuffify.services.operations.commands.asset_storage import commit_asset


@pytest.fixture
def storage_context(db_app):
    user = User(spotify_id="example-owner")
    db.session.add(user)
    db.session.commit()
    with Session(db.engine) as storage:
        yield storage, user.id


@pytest.mark.parametrize(
    "kind,model,key",
    [
        ("pair_create", PlaylistPair, "pair"),
        ("raid_link_create", RaidPlaylistLink, "raid_link"),
    ],
)
def test_association_receipt_flushes_without_committing(storage_context, kind, model, key):
    storage, user_id = storage_context
    command = capture_asset(storage, user_id, "target", kind=kind, target_playlist_name="Example")
    applied = {
        "result": {
            "success": True,
            "outcome": "created",
            "privacy_confirmed": True,
            "playlist": {"id": "created", "name": command.name},
            "creation_unknown": False,
        }
    }
    result = commit_asset(storage, user_id, None, command, applied)
    assert result["result"][key]["id"]
    assert result["result"][key]["user_id"] == user_id
    assert storage.scalar(db.select(model)).id == result["result"][key]["id"]
    storage.rollback()
    assert storage.scalar(db.select(model)) is None
    assert storage.scalar(db.select(ActivityLog)) is None


def test_setup_creation_records_activity_without_an_association(storage_context):
    storage, user_id = storage_context
    command = capture_asset(storage, user_id, "target", kind="setup_playlist_create", purpose="archive", name="New")
    applied = {
        "result": {
            "success": True,
            "outcome": "created",
            "privacy_confirmed": True,
            "playlist": {"id": "created", "name": "New"},
            "creation_unknown": False,
        }
    }
    result = commit_asset(storage, user_id, None, command, applied)
    assert result["result"]["playlist"]["id"] == "created"
    assert storage.scalar(db.select(PlaylistPair)) is None
    assert storage.scalar(db.select(RaidPlaylistLink)) is None
    assert storage.scalar(db.select(ActivityLog)).activity_type == "playlist_setup_create"
    storage.rollback()


def test_privacy_uncertainty_cannot_create_an_association(storage_context):
    storage, user_id = storage_context
    command = capture_asset(storage, user_id, "target", kind="pair_create")
    with pytest.raises(ValueError):
        commit_asset(
            storage,
            user_id,
            None,
            command,
            {
                "result": {
                    "success": True,
                    "outcome": "created",
                    "privacy_confirmed": False,
                    "playlist": {"id": "created", "name": "Example"},
                    "creation_unknown": False,
                }
            },
        )
    assert storage.scalar(db.select(PlaylistPair)) is None


def test_concurrent_association_is_not_replaced(storage_context):
    storage, user_id = storage_context
    command = capture_asset(storage, user_id, "target", kind="pair_create")
    storage.add(PlaylistPair(user_id=user_id, production_playlist_id="target", archive_playlist_id="other"))
    storage.commit()
    with pytest.raises(AssetCaptureRejected):
        commit_asset(
            storage,
            user_id,
            None,
            command,
            {
                "result": {
                    "success": True,
                    "outcome": "created",
                    "privacy_confirmed": True,
                    "playlist": {"id": "created", "name": "Example"},
                    "creation_unknown": False,
                }
            },
        )
    assert storage.scalar(db.select(PlaylistPair)).archive_playlist_id == "other"
