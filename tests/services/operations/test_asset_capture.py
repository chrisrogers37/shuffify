"""Asset creation captures owned association state without provider access."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from shuffify.models.db import PlaylistPair, RaidPlaylistLink, User, db
from shuffify.services.operations.commands.asset_capture import AssetCaptureRejected, capture_asset, recapture_asset
from shuffify.services.operations.commands.asset_schemas import AssetCommand


@pytest.fixture
def assets(db_app):
    user, other = User(spotify_id="example-owner"), User(spotify_id="other-owner")
    db.session.add_all([user, other])
    db.session.commit()
    return SimpleNamespace(user=user, other=other, storage=Session(db.engine))


@pytest.mark.parametrize(
    "kind,purpose,name,description",
    [
        ("pair_create", "archive", "Example [Archive]", "Archive playlist for removed tracks"),
        ("raid_link_create", "collection", "Example [Raids]", "Raid staging playlist for incoming tracks"),
        ("setup_playlist_create", "archive", "New asset", "Archived tracks, created in Shuffify setup"),
        ("setup_playlist_create", "collection", "New asset", "Collected discoveries, created in Shuffify setup"),
    ],
)
def test_new_asset_captures_exact_authored_strings(assets, kind, purpose, name, description):
    command = capture_asset(
        assets.storage,
        assets.user.id,
        "target",
        kind=kind,
        purpose=purpose,
        target_playlist_name="Example",
        name="New asset",
    )
    assert command.name == name
    assert command.description == description
    assert command.reserved_targets == ["target"]
    assert recapture_asset(assets.storage, assets.user.id, command) == command


def test_existing_pair_refuses_only_owned_target(assets):
    assets.storage.add(
        PlaylistPair(user_id=assets.other.id, production_playlist_id="target", archive_playlist_id="other")
    )
    assets.storage.commit()
    assert capture_asset(assets.storage, assets.user.id, "target", kind="pair_create").create_new
    assets.storage.add(
        PlaylistPair(user_id=assets.user.id, production_playlist_id="target", archive_playlist_id="archive")
    )
    assets.storage.commit()
    with pytest.raises(AssetCaptureRejected) as caught:
        capture_asset(assets.storage, assets.user.id, "target", kind="pair_create")
    assert caught.value.code == "already_paired"


def test_existing_raid_link_is_captured_as_an_exact_idempotent_association(assets):
    row = RaidPlaylistLink(user_id=assets.user.id, target_playlist_id="target", raid_playlist_id="collection")
    assets.storage.add(row)
    assets.storage.commit()
    command = capture_asset(assets.storage, assets.user.id, "target", kind="raid_link_create")
    assert command.already_linked
    assert command.existing_binding.id == row.id
    assert command.reserved_targets == ["collection", "target"]
    row.raid_playlist_id = "replacement"
    assets.storage.commit()
    with pytest.raises(AssetCaptureRejected) as caught:
        recapture_asset(assets.storage, assets.user.id, command)
    assert caught.value.code == "association_changed"


@pytest.mark.parametrize(
    "purpose,model,fields",
    [
        ("archive", PlaylistPair, {"production_playlist_id": "target", "archive_playlist_id": "archive"}),
        ("collection", RaidPlaylistLink, {"target_playlist_id": "target", "raid_playlist_id": "collection"}),
    ],
)
def test_setup_refuses_an_existing_asset_before_creation(assets, purpose, model, fields):
    assets.storage.add(model(user_id=assets.user.id, **fields))
    assets.storage.commit()
    with pytest.raises(AssetCaptureRejected) as caught:
        capture_asset(
            assets.storage, assets.user.id, "target", kind="setup_playlist_create", purpose=purpose, name="New"
        )
    assert caught.value.code == ("already_paired" if purpose == "archive" else "already_linked")


def test_existing_asset_reservation_and_distinct_target(assets):
    command = capture_asset(
        assets.storage,
        assets.user.id,
        "target",
        kind="pair_create",
        create_new=False,
        asset_playlist_id="archive",
        asset_playlist_name="Example archive",
    )
    assert command.reserved_targets == ["archive", "target"]
    with pytest.raises(AssetCaptureRejected):
        capture_asset(
            assets.storage,
            assets.user.id,
            "target",
            kind="pair_create",
            create_new=False,
            asset_playlist_id="target",
            asset_playlist_name="Example",
        )


def test_new_association_after_admission_is_not_overwritten(assets):
    command = capture_asset(assets.storage, assets.user.id, "target", kind="pair_create")
    assets.storage.add(
        PlaylistPair(user_id=assets.user.id, production_playlist_id="target", archive_playlist_id="archive")
    )
    assets.storage.commit()
    with pytest.raises(AssetCaptureRejected) as caught:
        recapture_asset(assets.storage, assets.user.id, command)
    assert caught.value.code == "association_changed"


def test_capture_never_autoflushes_unrelated_changes(assets):
    pending = User(spotify_id="uncommitted-owner")
    assets.storage.add(pending)
    capture_asset(assets.storage, assets.user.id, "target", kind="pair_create")
    assert pending.id is None


def test_private_command_rejects_coercion_extra_and_unsafe_modes(assets):
    command = capture_asset(assets.storage, assets.user.id, "target", kind="pair_create")
    for changes in ({"drip_count": True}, {"extra": "ignored"}, {"purpose": "collection"}, {"create_new": False}):
        with pytest.raises(ValidationError):
            AssetCommand.model_validate({**command.model_dump(mode="json"), **changes})


def test_unknown_owner_cannot_capture_creation(assets):
    with pytest.raises(AssetCaptureRejected) as caught:
        capture_asset(assets.storage, assets.other.id + 100, "target", kind="pair_create")
    assert caught.value.code == "user_not_found"
