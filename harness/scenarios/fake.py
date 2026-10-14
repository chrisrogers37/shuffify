"""Replay scenarios against the fake Spotify, in process, as the seeded user alice."""

from typing import Dict

from harness.fake_spotify import create_app, spotify_id, tokens

from . import Answer, ScenarioFailure

USER = "alice"
FOREIGN = "bob-finds"  # Bob's public playlist: alice follows it and cannot edit it
# What shuffify asks for (shuffify/spotify/auth.py, DEFAULT_SCOPES).
SCOPES = ("playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public "
          "user-read-private")


class FakeTarget:
    """A fresh fake for each target. Each ``own`` role is a new, empty,
    private playlist of alice's, so a scenario sees only what it set up."""

    def __init__(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.store = self.app.config["STORE"]
        self.headers = {"Authorization": f"Bearer {tokens.mint('access', 3600, user=USER, scope=SCOPES)}"}

    def violations(self) -> list:
        return list(self.app.config["VIOLATIONS"])

    def bind(self, playlists: Dict[str, str], tracks: int) -> Dict[str, str]:
        values = {}
        for role, kind in playlists.items():
            if kind == "own":
                playlist = self.store.create_playlist(owner=USER, name=f"Scenario {role}", public=False, description="")
                values[role] = playlist.id
            else:
                values[role] = spotify_id("playlist", FOREIGN)
        for number, track in enumerate(self.store.catalog[:tracks], 1):
            values[f"track{number}"], values[f"track{number}.id"] = track.uri, track.id
        return values

    def call(self, method: str, path: str, query: dict, body, unserved: bool) -> Answer:
        violations = self.app.config["VIOLATIONS"]
        before = len(violations)
        resp = self.client.open(f"/v1{path}", method=method, query_string=query, json=body, headers=self.headers)
        if unserved:
            # The fake records a call to an endpoint it does not serve; this
            # one the scenario makes on purpose.
            new = violations[before:]
            if len(new) != 1 or "not an operation the fake serves" not in new[0]:
                raise ScenarioFailure(f"{method} {path}: expected the fake to record one unserved call, got {new}")
            del violations[before:]
        return Answer(resp.status_code, resp.get_json(silent=True), bool(resp.data))
