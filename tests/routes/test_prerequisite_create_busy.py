"""Creating a dependent Spotify playlist must respect the target lock first."""

from contextlib import contextmanager
from threading import Event, Thread
from unittest.mock import MagicMock, patch

import pytest

from shuffify.models.db import PlaylistPair, RaidPlaylistLink, User, db
from shuffify.services.playlist_lock import playlist_configuration_lock, playlist_lock
from tests.paths import api_path


@contextmanager
def held_target(app, target, lock=playlist_configuration_lock):
    entered = Event()
    release = Event()

    def hold():
        with app.app_context():
            with lock(target) as acquired:
                assert acquired
                entered.set()
                release.wait(timeout=8)

    holder = Thread(target=hold)
    holder.start()
    assert entered.wait(timeout=2)
    try:
        yield
    finally:
        release.set()
        holder.join(timeout=2)
        assert not holder.is_alive()


@pytest.mark.parametrize(("path", "creator", "model"), [
    ("raid-link", "shuffify.routes.raid_panel.RaidLinkService.create_raid_playlist", RaidPlaylistLink),
    ("pair", "shuffify.routes.playlist_pairs.PlaylistPairService.create_archive_playlist", PlaylistPair),
])
def test_busy_create_new_does_not_dispatch_spotify(
    path, creator, model, auth_client, db_app,
):
    with patch("shuffify.routes.require_auth", return_value=MagicMock()), patch(creator) as create:
        with held_target(db_app, "p1"):
            response = auth_client.post(
                api_path(f"/playlist/p1/{path}"), json={"create_new": True},
            )

    assert response.status_code == 409
    assert response.get_json()["error_code"] == "playlist_busy"
    create.assert_not_called()
    assert model.query.count() == 0


@pytest.mark.parametrize(("path", "creator", "model"), [
    ("raid-link", "shuffify.routes.raid_panel.RaidLinkService.create_raid_playlist", RaidPlaylistLink),
    ("pair", "shuffify.routes.playlist_pairs.PlaylistPairService.create_archive_playlist", PlaylistPair),
])
def test_create_new_can_associate_during_execution_lock(
    path, creator, model, auth_client, db_app,
):
    with patch("shuffify.routes.require_auth", return_value=MagicMock()), patch(
        creator, return_value=("new_asset", "New Asset")
    ) as create:
        with held_target(db_app, "p1", lock=playlist_lock):
            response = auth_client.post(
                api_path(f"/playlist/p1/{path}"), json={"create_new": True},
            )

    assert response.status_code == 200, response.get_json()
    create.assert_called_once()
    assert model.query.count() == 1


def test_existing_raid_link_keeps_idempotent_response_without_spotify(auth_client):
    user = User.query.filter_by(spotify_id="user123").one()
    link = RaidPlaylistLink(
        user_id=user.id, target_playlist_id="p1", raid_playlist_id="existing",
    )
    db.session.add(link)
    db.session.commit()

    with patch("shuffify.routes.require_auth", return_value=MagicMock()), patch(
        "shuffify.routes.raid_panel.RaidLinkService.create_raid_playlist"
    ) as create:
        response = auth_client.post(api_path("/playlist/p1/raid-link"), json={"create_new": True})

    assert response.status_code == 200
    assert response.get_json()["raid_link"]["raid_playlist_id"] == "existing"
    create.assert_not_called()
    assert RaidPlaylistLink.query.count() == 1


def test_existing_pair_rejects_before_spotify_creation(auth_client):
    user = User.query.filter_by(spotify_id="user123").one()
    pair = PlaylistPair(
        user_id=user.id, production_playlist_id="p1", archive_playlist_id="existing",
    )
    db.session.add(pair)
    db.session.commit()

    with patch("shuffify.routes.require_auth", return_value=MagicMock()), patch(
        "shuffify.routes.playlist_pairs.PlaylistPairService.create_archive_playlist"
    ) as create:
        response = auth_client.post(api_path("/playlist/p1/pair"), json={"create_new": True})

    assert response.status_code == 409
    create.assert_not_called()
    assert PlaylistPair.query.count() == 1
