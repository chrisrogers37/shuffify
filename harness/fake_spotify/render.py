"""Spotify's JSON objects, built from the fake's state.

Field sets follow Spotify's OpenAPI schema (shapes.json) as a Development Mode
app sees it: the fields Spotify withholds from those apps
(user ``country``/``email``/``product``/..., track and album
``available_markets``, track ``popularity``) are left out. Every href, page
link and ``external_urls`` entry points at the fake's own origin, and images
are data: URIs, so nothing a client follows leaves the machine.
"""

import base64
from typing import List, Optional
from urllib.parse import urlencode

from .store import Album, Artist, Entry, Playlist, Store, Track, User, spotify_id

# GET /playlists/{id} embeds the first page of items. Its size is not in the
# schema; it is kept to what /items itself accepts, so the embedded page's
# next link is one the fake (and Spotify) will serve.
EMBEDDED_PAGE = 50


def _image(color: str) -> dict:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300">'
           f'<rect width="300" height="300" fill="{color}"/></svg>')
    return {"url": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode(), "height": 300, "width": 300}


class Renderer:
    def __init__(self, origin: str, store: Store):
        self.origin = origin.rstrip("/")
        self.api = f"{self.origin}/v1"
        self.store = store

    def _ref(self, kind: str, id_: str, collection: str) -> dict:
        """The fields every Spotify object reference carries."""
        return {
            "external_urls": {"spotify": f"{self.origin}/{kind}/{id_}"},
            "href": f"{self.api}/{collection}/{id_}",
            "id": id_,
            "type": kind,
            "uri": f"spotify:{kind}:{id_}",
        }

    # -- users ----------------------------------------------------------------

    def user_ref(self, user_id: str) -> dict:
        return self._ref("user", user_id, "users")

    def owner(self, user_id: str) -> dict:
        return {**self.user_ref(user_id), "display_name": self.store.users[user_id].display_name}

    def me(self, user: User) -> dict:
        return {
            **self.user_ref(user.id),
            # account_id is "a public, immutable, pseudoanonymous identifier".
            "account_id": spotify_id("account", user.id),
            "display_name": user.display_name,
            "images": [_image("#1db954")],
        }

    # -- catalogue ------------------------------------------------------------

    def artist(self, artist: Artist) -> dict:
        return {**self._ref("artist", artist.id, "artists"), "name": artist.name}

    def album(self, album: Album) -> dict:
        return {
            **self._ref("album", album.id, "albums"),
            "album_type": "album",
            "total_tracks": album.total_tracks,
            "images": [_image(album.color)],
            "name": album.name,
            "release_date": album.release_date,
            "release_date_precision": "day",
            "artists": [self.artist(album.artist)],
        }

    def track(self, track: Track) -> dict:
        return {
            **self._ref("track", track.id, "tracks"),
            "album": self.album(track.album),
            "artists": [self.artist(a) for a in track.artists],
            "disc_number": 1,
            "duration_ms": track.duration_ms,
            "explicit": False,
            "external_ids": {"isrc": track.isrc},
            "name": track.name,
            "preview_url": None,
            "track_number": track.track_number,
            "is_local": False,
        }

    # -- playlists ------------------------------------------------------------

    def _entry(self, playlist: Playlist, entry: Entry) -> dict:
        return {
            "added_at": entry.added_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "added_by": self.user_ref(entry.added_by),
            "is_local": False,
            # "track" on the legacy shape, "item" otherwise.
            ("track" if playlist.legacy_shape else "item"): self.track(self.store.track(entry.uri)),
        }

    def items_page(self, playlist: Playlist, offset: int, limit: int) -> dict:
        entries = playlist.entries[offset:offset + limit]
        return self.page(
            f"{self.api}/playlists/{playlist.id}/items",
            [self._entry(playlist, e) for e in entries],
            offset, limit, len(playlist.entries),
        )

    def _playlist_base(self, playlist: Playlist) -> dict:
        first = self.store.track(playlist.entries[0].uri) if playlist.entries else None
        return {
            **self._ref("playlist", playlist.id, "playlists"),
            "collaborative": playlist.collaborative,
            "description": playlist.description,
            "images": [_image(first.album.color)] if first else [],
            "name": playlist.name,
            "owner": self.owner(playlist.owner),
            "public": playlist.public,
            "snapshot_id": playlist.snapshot_id,
        }

    def simplified_playlist(self, playlist: Playlist) -> dict:
        ref = {"href": f"{self.api}/playlists/{playlist.id}/items", "total": len(playlist.entries)}
        return {**self._playlist_base(playlist), playlist.items_key: ref}

    def playlist(self, playlist: Playlist, with_items: bool) -> dict:
        """The full object. Its items page only for the owner or a collaborator:
        Spotify returns metadata alone for anyone else's playlist."""
        out = self._playlist_base(playlist)
        if with_items:
            out[playlist.items_key] = self.items_page(playlist, 0, EMBEDDED_PAGE)
        return out

    # -- paging ---------------------------------------------------------------

    def page(self, base: str, items: List[dict], offset: int, limit: int, total: int,
             query: Optional[dict] = None) -> dict:
        def link(at: int) -> str:
            return f"{base}?{urlencode({**(query or {}), 'offset': at, 'limit': limit})}"

        return {
            "href": link(offset),
            "items": items,
            "limit": limit,
            "next": link(offset + limit) if offset + limit < total else None,
            "offset": offset,
            "previous": link(max(0, offset - limit)) if offset > 0 else None,
            "total": total,
        }
