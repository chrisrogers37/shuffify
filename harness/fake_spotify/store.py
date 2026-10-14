"""The fake Spotify's state: a generated catalogue, seeded users and playlists.

Everything lives in memory and is rebuilt from seed.json on start and on
``reset()``. IDs are stable across restarts -- derived from names, in the
22-character base62 form real Spotify IDs take -- so a database that outlives
the fake still points at the same users and playlists.
"""

import base64
import hashlib
import json
import string
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

SEED_PATH = Path(__file__).with_name("seed.json")

_BASE62 = string.digits + string.ascii_uppercase + string.ascii_lowercase

# Invented names for the generated catalogue.
_FIRST = ["Velvet", "Paper", "Neon", "Quiet", "Copper", "Silver", "Hollow", "Golden",
          "Midnight", "Static", "Northern", "Lucky", "Wild", "Glass", "Faded", "Electric"]
_SECOND = ["Harbor", "Lanterns", "Ferns", "Engines", "Tide", "Atlas", "Orchard", "Signal",
           "Comets", "Rivers", "Parade", "Window", "Echoes", "Satellite", "Garden", "Machine"]
_ADDED_AT_EPOCH = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)


def spotify_id(kind: str, key: str) -> str:
    """A stable 22-character base62 ID, the shape of a real Spotify ID."""
    n = int.from_bytes(hashlib.sha256(f"{kind}:{key}".encode()).digest(), "big")
    chars = []
    for _ in range(22):
        n, r = divmod(n, 62)
        chars.append(_BASE62[r])
    return "".join(chars)


def _name(i: int, offset: int = 0) -> str:
    return f"{_FIRST[(i + offset) % len(_FIRST)]} {_SECOND[(i * 7 + offset) % len(_SECOND)]}"


@dataclass
class Artist:
    id: str
    name: str


@dataclass
class Album:
    id: str
    name: str
    artist: Artist
    release_date: str
    total_tracks: int
    color: str


@dataclass
class Track:
    id: str
    name: str
    album: Album
    artists: List[Artist]
    duration_ms: int
    track_number: int
    isrc: str

    @property
    def uri(self) -> str:
        return f"spotify:track:{self.id}"


@dataclass
class User:
    id: str
    display_name: str


@dataclass
class Entry:
    """One row of a playlist: a track, who added it and when."""

    uri: str
    added_at: datetime
    added_by: str


@dataclass
class Playlist:
    id: str
    name: str
    owner: str
    description: str
    public: bool
    collaborative: bool = False
    collaborators: set = field(default_factory=set)
    followers: set = field(default_factory=set)
    entries: List[Entry] = field(default_factory=list)
    legacy_shape: bool = False  # serve the legacy "tracks"/"track" keys
    version: int = 0

    @property
    def snapshot_id(self) -> str:
        digest = hashlib.sha256(f"{self.id}:{self.version}".encode()).digest()
        return base64.urlsafe_b64encode(digest).decode()[:32]

    @property
    def uris(self) -> List[str]:
        return [e.uri for e in self.entries]

    @property
    def items_key(self) -> str:
        """Where the items go: "items", or "tracks" in the legacy shape."""
        return "tracks" if self.legacy_shape else "items"


class Store:
    """All state, behind one lock: the fake serves requests on threads."""

    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        seed = json.loads(SEED_PATH.read_text())
        with self.lock:
            self.tracks: Dict[str, Track] = {}
            self.catalog: List[Track] = []
            self._build_catalog(**seed["catalog"])
            self.users = {u["id"]: User(u["id"], u["display_name"]) for u in seed["users"]}
            self.playlists: Dict[str, Playlist] = {}
            for spec in seed["playlists"]:
                self._seed_playlist(spec)
            self.used_codes: set = set()

    # -- catalogue ----------------------------------------------------------

    def _build_catalog(self, artists: int, albums_per_artist: int, tracks_per_album: int) -> None:
        number = 0
        for a in range(artists):
            artist = Artist(spotify_id("artist", str(a)), _name(a, 3))
            for b in range(albums_per_artist):
                album_index = a * albums_per_artist + b
                album = Album(
                    id=spotify_id("album", str(album_index)),
                    name=_name(album_index, 11),
                    artist=artist,
                    release_date=f"{2005 + album_index % 20}-{1 + album_index % 12:02d}-{1 + album_index % 28:02d}",
                    total_tracks=tracks_per_album,
                    color=f"#{hashlib.sha256(str(album_index).encode()).hexdigest()[:6]}",
                )
                for t in range(tracks_per_album):
                    track = Track(
                        id=spotify_id("track", str(number)),
                        name=_name(number, 5),
                        album=album,
                        artists=[artist],
                        duration_ms=150_000 + (number * 7919) % 150_000,
                        track_number=t + 1,
                        isrc=f"QZFAK{number:07d}",
                    )
                    self.catalog.append(track)
                    self.tracks[track.uri] = track
                    number += 1

    def track(self, uri: str) -> Optional[Track]:
        return self.tracks.get(uri)

    # -- playlists ----------------------------------------------------------

    def _seed_playlist(self, spec: dict) -> None:
        start, end = spec["tracks"]
        uris = [t.uri for t in self.catalog[start:end]] * spec.get("repeat", 1)
        playlist = Playlist(
            id=spotify_id("playlist", spec["key"]),
            name=spec["name"],
            owner=spec["owner"],
            description=spec.get("description", ""),
            public=spec.get("public", False),
            collaborative=spec.get("collaborative", False),
            collaborators=set(spec.get("collaborators", [])),
            followers=set(spec.get("followers", [])),
            legacy_shape=spec.get("shape") == "legacy",
        )
        adders = [spec["owner"], *sorted(playlist.collaborators)]
        playlist.entries = [
            Entry(uri, _ADDED_AT_EPOCH + timedelta(days=i, minutes=i), adders[i % len(adders)])
            for i, uri in enumerate(uris)
        ]
        self.playlists[playlist.id] = playlist

    def playlists_for(self, user_id: str) -> List[Playlist]:
        """A user's library: playlists they own, collaborate on or follow."""
        return [
            p for p in self.playlists.values()
            if p.owner == user_id or user_id in p.collaborators or user_id in p.followers
        ]

    @staticmethod
    def can_read_items(playlist: Playlist, user_id: str) -> bool:
        # Spotify: items are only returned to the owner or a collaborator.
        return playlist.owner == user_id or user_id in playlist.collaborators

    @staticmethod
    def can_edit(playlist: Playlist, user_id: str) -> bool:
        return playlist.owner == user_id or (playlist.collaborative and user_id in playlist.collaborators)

    @staticmethod
    def can_see(playlist: Playlist, user_id: str) -> bool:
        return playlist.public or Store.can_read_items(playlist, user_id) or user_id in playlist.followers

    def create_playlist(self, owner: str, name: str, public: bool, description: str) -> Playlist:
        with self.lock:
            key = f"created:{owner}:{len(self.playlists)}:{name}"
            playlist = Playlist(
                id=spotify_id("playlist", key),
                name=name,
                owner=owner,
                description=description,
                public=public,
            )
            self.playlists[playlist.id] = playlist
            return playlist

    def add(self, playlist: Playlist, uris: List[str], position: Optional[int], by: str) -> None:
        now = datetime.now(timezone.utc)
        entries = [Entry(uri, now, by) for uri in uris]
        with self.lock:
            at = len(playlist.entries) if position is None else position
            playlist.entries[at:at] = entries
            playlist.version += 1

    def replace(self, playlist: Playlist, uris: List[str], by: str) -> None:
        now = datetime.now(timezone.utc)
        with self.lock:
            playlist.entries = [Entry(uri, now, by) for uri in uris]
            playlist.version += 1

    def remove(self, playlist: Playlist, uris: List[str]) -> None:
        """Every occurrence of each URI, as Spotify does."""
        drop = set(uris)
        with self.lock:
            playlist.entries = [e for e in playlist.entries if e.uri not in drop]
            playlist.version += 1
