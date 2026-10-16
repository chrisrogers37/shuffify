"""Existing asset labels come from the authorized frozen provider observation."""

from copy import deepcopy

import pytest

from shuffify.models.db import ActivityLog, PlaylistPair, RaidPlaylistLink
from tests.services.operations.test_asset_commands import asset_operation as asset_operation


@pytest.mark.parametrize("kind", ["pair_create", "raid_link_create"])
def test_existing_asset_ignores_browser_label_and_persists_verified_name(asset_operation, kind):
    case = asset_operation
    case.world.playlists["existing"] = deepcopy(case.world.playlists["target"])
    case.world.playlists["existing"]["metadata"]["name"] = "Canonical Spotify asset"
    command = case.enqueue(
        kind,
        create_new=False,
        asset_playlist_id="existing",
        asset_playlist_name="Browser supplied counterfeit label",
    )
    assert command.payload["command"]["name"] == "Browser supplied counterfeit label"

    completed = case.run(command)

    result = completed.journal["result"]
    assert completed.state == "succeeded"
    assert result["outcome"] == "existing"
    assert result["playlist"] == {"id": "existing", "name": "Canonical Spotify asset"}
    if kind == "pair_create":
        row = PlaylistPair.query.one()
        assert row.archive_playlist_id == "existing"
        assert row.archive_playlist_name == result["pair"]["archive_playlist_name"] == "Canonical Spotify asset"
    else:
        row = RaidPlaylistLink.query.one()
        assert row.raid_playlist_id == "existing"
        assert row.raid_playlist_name == result["raid_link"]["raid_playlist_name"] == "Canonical Spotify asset"
    frozen = completed.journal["runtime"]["prepared"]["asset.plan.v1"]
    assert frozen["existing"]["metadata"]["name"] == "Canonical Spotify asset"
    assert result["association_status"] == "complete"
    assert case.world.writes == []
    assert len(completed.journal["boundaries"]["accepted"]) == 1
    assert ActivityLog.query.count() == 1


@pytest.mark.parametrize("kind", ["pair_create", "raid_link_create"])
def test_missing_provider_name_uses_exact_id_instead_of_browser_label(asset_operation, kind):
    case = asset_operation
    case.world.playlists["existing"] = deepcopy(case.world.playlists["target"])
    case.world.playlists["existing"]["metadata"]["name"] = None
    command = case.enqueue(
        kind,
        create_new=False,
        asset_playlist_id="existing",
        asset_playlist_name="Browser supplied counterfeit label",
    )

    completed = case.run(command)

    assert completed.state == "succeeded"
    result = completed.journal["result"]
    assert result["playlist"] == {"id": "existing", "name": "existing"}
    association = result["pair"] if kind == "pair_create" else result["raid_link"]
    name = "archive_playlist_name" if kind == "pair_create" else "raid_playlist_name"
    assert association[name] == "existing"
    assert case.world.writes == []
