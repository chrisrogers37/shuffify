"""Committing shared prerequisites hold the target lock through mutation."""

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from shuffify.models.db import RaidPlaylistLink, UpstreamSource, User, db
from shuffify.services.playlist_lock import PlaylistBusyError
from shuffify.services.playlist_pair_service import PlaylistPairService
from shuffify.services.raid_link_service import RaidLinkService
from shuffify.services.upstream_source_service import UpstreamSourceNotFoundError, UpstreamSourceService


@pytest.fixture
def owner(db_app):
    with db_app.app_context():
        user = User(spotify_id="lock-owner")
        db.session.add(user)
        db.session.commit()
        yield user


@pytest.mark.parametrize("mutation", [
    "source_add", "search_add", "source_update", "source_delete", "source_delete_implicit",
    "link_create", "link_update", "link_delete",
    "pair_create", "pair_update", "pair_delete",
])
def test_commit_mutators_refuse_busy_target_before_writing(owner, monkeypatch, mutation):
    source = UpstreamSource(
        user_id=owner.id, target_playlist_id="target", source_playlist_id="existing",
        source_type="external",
    )
    link = RaidPlaylistLink(
        user_id=owner.id, target_playlist_id="target", raid_playlist_id="collection",
    )
    db.session.add_all([source, link])
    pair = PlaylistPairService.create_pair(owner.id, "target", "archive", commit=False)
    db.session.commit()

    @contextmanager
    def busy(target, *, timeout_s):
        assert target == "target"
        assert timeout_s > 0
        yield False

    monkeypatch.setattr("shuffify.services.playlist_lock.playlist_configuration_lock", busy)
    actions = {
        "source_add": lambda: UpstreamSourceService.add_source("lock-owner", "target", "new"),
        "search_add": lambda: UpstreamSourceService.add_search_source("lock-owner", "target", "new query"),
        "source_update": lambda: UpstreamSourceService.update_raid_count(owner.id, "target", source.id, 9),
        "source_delete": lambda: UpstreamSourceService.delete_source(source.id, "lock-owner", "target"),
        "source_delete_implicit": lambda: UpstreamSourceService.delete_source(source.id, "lock-owner"),
        "link_create": lambda: RaidLinkService.create_link(owner.id, "target", "other"),
        "link_update": lambda: RaidLinkService.update_link(owner.id, "target", drip_count=9),
        "link_delete": lambda: RaidLinkService.delete_link(owner.id, "target"),
        "pair_create": lambda: PlaylistPairService.create_pair(owner.id, "target", "other"),
        "pair_update": lambda: PlaylistPairService.update_pair(owner.id, "target", auto_archive_on_remove=False),
        "pair_delete": lambda: PlaylistPairService.delete_pair(owner.id, "target"),
    }
    with pytest.raises(PlaylistBusyError) as error:
        actions[mutation]()
    assert error.value.error_code == "playlist_busy"
    db.session.expire_all()
    assert UpstreamSource.query.filter_by(user_id=owner.id, target_playlist_id="target").count() == 1
    assert source.raid_count != 9
    assert RaidPlaylistLink.query.filter_by(user_id=owner.id, target_playlist_id="target").one().drip_count != 9
    assert pair.auto_archive_on_remove is True


def test_staged_mutations_do_not_acquire_inner_lock(owner, monkeypatch):
    @contextmanager
    def forbidden(*args, **kwargs):
        raise AssertionError("commit=False must remain under its caller's lock")
        yield  # pragma: no cover

    monkeypatch.setattr("shuffify.services.playlist_lock.playlist_configuration_lock", forbidden)
    source = UpstreamSourceService.add_source("lock-owner", "target", "source", commit=False)
    link = RaidLinkService.create_link(owner.id, "target", "collection", commit=False)
    pair = PlaylistPairService.create_pair(owner.id, "target", "archive", commit=False)
    assert (source.id, link.id, pair.id) == (1, 1, 1)
    db.session.rollback()


def test_implicit_target_delete_refuses_reused_id_on_another_target(owner, monkeypatch):
    source = UpstreamSourceService.add_source("lock-owner", "original", "source")
    source_id = source.id

    @contextmanager
    def replace_while_waiting(target, *, timeout_s):
        assert target == "original"
        assert timeout_s > 0
        # Table-level SQL simulates a concurrent delete/recreate without
        # updating the ORM's pre-lock identity-map copy of the original row.
        db.session.execute(UpstreamSource.__table__.delete().where(UpstreamSource.id == source_id))
        db.session.execute(UpstreamSource.__table__.insert().values(
            id=source_id, user_id=owner.id, target_playlist_id="replacement",
            source_playlist_id="other", source_type="external", raid_count=5,
            created_at=datetime.now(timezone.utc),
        ))
        yield True

    monkeypatch.setattr("shuffify.services.playlist_lock.playlist_configuration_lock", replace_while_waiting)
    with pytest.raises(UpstreamSourceNotFoundError):
        UpstreamSourceService.delete_source(source_id, "lock-owner")
    replacement = db.session.get(UpstreamSource, source_id, populate_existing=True)
    assert replacement.target_playlist_id == "replacement"
    db.session.rollback()
