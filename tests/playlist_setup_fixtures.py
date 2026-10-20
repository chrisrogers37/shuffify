"""Shared fixtures for the playlist setup tests: a recording fake Spotify, row seeders and spies.

``tests/conftest.py`` registers this module (``pytest_plugins``), so its fixtures
need no import. ``no_scraping`` is not autouse, because a plugin's autouse
fixture would reach every test: a setup test module opts in with
``pytestmark = pytest.mark.usefixtures("no_scraping")``.
"""

import importlib
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from shuffify.models.db import PlaylistPair, RaidPlaylistLink, Schedule, UpstreamSource, db
from shuffify.services import JobExecutorService, RaidSyncService, SchedulerService
from shuffify.spotify.exceptions import SpotifyNotFoundError

TARGET = "target"
PLAYLISTS = {
    TARGET: {"name": "Morning Mix"},
    "mine1": {"name": "My Finds"},
    "ext1": {"name": "Indie Finds", "owner": "someone"},
    "ext2": {"name": "Jazz Picks", "owner": "someone"},
    "col1": {"name": "Collection One"},
    "col2": {"name": "Collection Two"},
    "arc1": {"name": "Archive One"},
    "arc2": {"name": "Archive Two"},
    "collab": {"name": "Shared Collab", "owner": "friend", "collaborative": True},
    "foreign": {"name": "Not Mine", "owner": "stranger"},
}
# Every path that creates, syncs, enables, disables, deletes or runs a job.
SCHEDULER_PATHS = [
    ("shuffify.scheduler", "add_job_for_schedule"),
    ("shuffify.scheduler", "remove_job_for_schedule"),
    (SchedulerService, "create_schedule"),
    (SchedulerService, "update_schedule"),
    (SchedulerService, "delete_schedule"),
    (SchedulerService, "_sync_apscheduler_job"),
    (SchedulerService, "_remove_apscheduler_job"),
    (RaidSyncService, "watch_playlist"),
    (RaidSyncService, "watch_search_query"),
    (RaidSyncService, "unwatch_playlist"),
    (JobExecutorService, "execute"),
    (JobExecutorService, "execute_now"),
    (JobExecutorService, "_run_job"),
    (JobExecutorService, "execute_raid_for_user"),
    (JobExecutorService, "execute_drip_for_user"),
]


class SetupSpotify:
    """A fake Spotify for setup: playlists by ID, every call recorded, failures injectable."""

    def __init__(self, owner="user123", playlists=PLAYLISTS):
        self.owner = owner
        self.playlists = {key: dict(value) for key, value in playlists.items()}
        self.calls = []
        self.read_failures = {}
        self.list_failure = None
        self.create_failure = None
        self.privacy_failure = None

    @property
    def writes(self):
        return [call for call in self.calls if call[0] in ("create_user_playlist", "update_playlist_details")]

    def _shape(self, playlist_id):
        playlist = self.playlists[playlist_id]
        return {
            "id": playlist_id,
            "name": playlist["name"],
            "owner": {"id": playlist.get("owner", self.owner)},
            "collaborative": playlist.get("collaborative", False),
            "items": {"total": 3},
            "images": [{"url": f"https://img.invalid/{playlist_id}.jpg"}],
        }

    def get_current_user(self):
        return {"id": self.owner, "display_name": "Test User", "images": []}

    def get_playlist(self, playlist_id, skip_cache=False):
        self.calls.append(("get_playlist", playlist_id, skip_cache))
        if playlist_id in self.read_failures:
            raise self.read_failures[playlist_id]
        if playlist_id not in self.playlists:
            raise SpotifyNotFoundError(f"No playlist {playlist_id}")
        return self._shape(playlist_id)

    def get_playlist_summary(self, playlist_id):
        self.calls.append(("get_playlist_summary", playlist_id))
        if playlist_id in self.read_failures:
            raise self.read_failures[playlist_id]
        if playlist_id not in self.playlists:
            raise SpotifyNotFoundError(f"No playlist {playlist_id}")
        shape = self._shape(playlist_id)
        return {key: shape[key] for key in ("id", "name", "owner", "collaborative")}

    def get_user_playlists(self, skip_cache=False):
        self.calls.append(("get_user_playlists", skip_cache))
        if self.list_failure:
            raise self.list_failure
        return [
            self._shape(key) for key, value in self.playlists.items()
            if value.get("owner", self.owner) == self.owner or value.get("collaborative")
        ]

    def create_user_playlist(self, name, public=False, description=""):
        self.calls.append(("create_user_playlist", name, public, description))
        if self.create_failure:
            raise self.create_failure
        playlist_id = f"Created{sum(1 for key in self.playlists if key.startswith('Created')) + 1}"
        self.playlists[playlist_id] = {"name": name}
        return {"id": playlist_id, "name": name, "public": public}

    def update_playlist_details(self, playlist_id, **kwargs):
        self.calls.append(("update_playlist_details", playlist_id, kwargs))
        if self.privacy_failure:
            raise self.privacy_failure


@pytest.fixture
def no_scraping():
    """A playlist Spotify does not have is never looked up on the public web in these tests."""
    with patch("shuffify.services.playlist_service.scrape_playlist_metadata", return_value=None) as scraper:
        yield scraper


@pytest.fixture
def scheduler_spies():
    """Mocks over every scheduling, sync, watch and executor path; a test asserts none was called."""
    with ExitStack() as stack:
        spies = {}
        for owner, name in SCHEDULER_PATHS:
            holder = importlib.import_module(owner) if isinstance(owner, str) else owner
            spies[f"{getattr(holder, '__name__', owner)}.{name}"] = stack.enter_context(patch.object(holder, name))
        yield spies


def assert_never_scheduled(spies):
    called = [name for name, spy in spies.items() if spy.called]
    assert called == [], f"setup reached scheduling paths: {called}"


# --- Seeding --------------------------------------------------------------------


def add_source(user, playlist_id=None, *, query=None, name=None, count=5, target=TARGET, kind=None):
    source = UpstreamSource(
        user_id=user.id, target_playlist_id=target, source_playlist_id=playlist_id, search_query=query,
        source_type=kind or ("search_query" if query else "external"), source_name=name, raid_count=count,
    )
    db.session.add(source)
    db.session.commit()
    return source


def add_routine(user, job_type, ids=None, *, enabled=True, kind="interval", value="daily", params=None, target=TARGET):
    schedule = Schedule(
        user_id=user.id, job_type=job_type, target_playlist_id=target, target_playlist_name="Morning Mix",
        source_playlist_ids=ids, algorithm_params=params, schedule_type=kind, schedule_value=value,
        is_enabled=enabled,
    )
    db.session.add(schedule)
    db.session.commit()
    return schedule


def add_link(user, playlist_id="col1", *, name="Collection One", enabled=False, count=3, target=TARGET):
    link = RaidPlaylistLink(
        user_id=user.id, target_playlist_id=target, raid_playlist_id=playlist_id, raid_playlist_name=name,
        drip_enabled=enabled, drip_count=count,
    )
    db.session.add(link)
    db.session.commit()
    return link


def add_pair(user, playlist_id="arc1", *, name="Archive One", auto=True, target=TARGET):
    pair = PlaylistPair(
        user_id=user.id, production_playlist_id=target, archive_playlist_id=playlist_id,
        archive_playlist_name=name, auto_archive_on_remove=auto,
    )
    db.session.add(pair)
    db.session.commit()
    return pair
