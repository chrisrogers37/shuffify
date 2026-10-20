"""A mutable, complete Spotify fake with fault points, shared by the live-action tests."""


def uri(number):
    return f"spotify:track:{number:022d}"


class MutableSpotify:
    """Real reader pagination and mutating API behavior, including fault points."""

    def __init__(self, owner, **playlists):
        self.owner = owner.spotify_id
        self.playlists = {key: list(value) for key, value in playlists.items()}
        self.revisions = {key: 0 for key in playlists}
        self.writes = []
        self.faults = {}
        self.read_failure = None
        self.on_write = None

    def get_playlist(self, playlist_id, **kwargs):
        if self.read_failure:
            raise self.read_failure
        return {
            "id": playlist_id,
            "name": f"Fresh {playlist_id}",
            "owner": {"id": self.owner},
            "snapshot_id": str(self.revisions[playlist_id]),
            "items": {"total": len(self.playlists[playlist_id])},
        }

    def get_playlist_items_raw(self, playlist_id, limit=50, offset=0):
        tracks = self.playlists[playlist_id]
        return {
            "offset": offset,
            "total": len(tracks),
            "items": [
                {
                    "added_at": f"2026-10-{int(value.rsplit(':', 1)[1]) % 20 + 1:02d}T00:00:00Z",
                    "track": {"uri": value, "name": value, "artists": [{"name": "Artist"}], "type": "track"},
                }
                for value in tracks[offset : offset + limit]
            ],
            "next": f"https://fake.invalid/items?offset={offset + limit}" if offset + limit < len(tracks) else None,
        }

    def get_playlist_tracks(self, playlist_id, **kwargs):
        return [{"uri": u} for u in self.playlists[playlist_id]]

    def get_tracks(self, uris):
        return [{"uri": value} for value in uris]

    def _write(self, method, playlist_id, uris, position=None):
        self.writes.append((method, playlist_id, list(uris)))
        if self.on_write:
            self.on_write()
        fault = self.faults.get((method, playlist_id))
        if fault:
            return fault(self, playlist_id, uris)
        if method == "update":
            self.playlists[playlist_id] = list(uris)
        elif method == "remove":
            self.playlists[playlist_id] = [u for u in self.playlists[playlist_id] if u not in set(uris)]
        else:
            old = self.playlists[playlist_id]
            pos = len(old) if position is None else position
            self.playlists[playlist_id] = old[:pos] + list(uris) + old[pos:]
        self.revisions[playlist_id] += 1
        return True

    def update_playlist_tracks(self, playlist_id, uris):
        return self._write("update", playlist_id, uris)

    def playlist_add_items(self, playlist_id, uris, position=None):
        return self._write("add", playlist_id, uris, position)

    def playlist_remove_items(self, playlist_id, uris):
        return self._write("remove", playlist_id, uris)
