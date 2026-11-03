"""Freeze complete ordered states and the actual 100-item Spotify batch boundaries.

A playlist batch keeps its request body and the hashes of the states before and after it, not the
states themselves. `advance` applies a batch's body to the state before it, so the state after any
batch follows from its plan's baseline and the batches before it, and a journal grows with its track
lists. Batches frozen with their complete states still carry them, and `advance` returns those.
"""

import hashlib
import json
from copy import deepcopy

from shuffify.spotify.api import MAX_PLAYLIST_TRACKS

BATCH_SIZE = 100
DETAIL_FIELDS = frozenset(("name", "public", "description"))


class PlanTooLarge(ValueError):
    """A plan would list, or leave in its playlist, more tracks than one Spotify playlist can hold."""


def content(observed):
    tracks = observed.get("track_uris")
    metadata = observed.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Playlist metadata must be an object")
    if observed.get("scope") == "metadata":
        return {"scope": "metadata", "metadata": {key: deepcopy(value) for key, value in metadata.items()
                                                   if key in DETAIL_FIELDS}}
    if not isinstance(tracks, (list, tuple)) or any(not isinstance(uri, str) for uri in tracks):
        raise ValueError("A complete ordered track list is required")
    return {"track_uris": list(tracks), "metadata": {key: deepcopy(value) for key, value in metadata.items()
                                                   if key in DETAIL_FIELDS}}


def state_hash(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def batch(method, path, body, before, after):
    return {
        "method": method, "path": path, "body": deepcopy(body),
        "before_hash": state_hash(before), "after_hash": state_hash(after),
        "state": "prepared", "attempts": 0, "snapshot_before": None, "snapshot_id": None,
    }


def advance(state, item):
    """Return the state after the playlist batch `item`, given the state before it."""
    if "after" in item:
        return item["after"]
    method, path, body = item["method"], item["path"], item["body"]
    after = {**state, "metadata": dict(state["metadata"])}
    if "track_uris" in state:
        after["track_uris"] = list(state["track_uris"])
    if not path.endswith("/items"):
        after["metadata"].update(deepcopy(body))
    elif method == "PUT":
        after["track_uris"] = list(body["uris"])
    elif method == "POST":
        index = body.get("position", len(after["track_uris"]))
        after["track_uris"][index:index] = body["uris"]
    elif method == "DELETE":
        removed = {entry["uri"] for entry in body["items"]}
        after["track_uris"] = [uri for uri in after["track_uris"] if uri not in removed]
    else:
        raise ValueError("Unsupported Spotify mutation method")
    return after


def replay(state, batches):
    """Return the state after each batch, applying the batches in order from `state`."""
    states = []
    for item in batches:
        state = advance(state, item)
        states.append(state)
    return states


def plan(kind, resource, before, *, uris=(), position=None, details=None):
    values = list(uris)
    # An add is measured by the playlist it leaves; every other plan by the list it sends.
    size = len(values) + (len(before["track_uris"]) if kind == "add" else 0)
    if size > MAX_PLAYLIST_TRACKS:
        raise PlanTooLarge(f"A Spotify playlist holds at most {MAX_PLAYLIST_TRACKS:,} tracks")
    current = deepcopy(before)
    batches = []
    path = f"/playlists/{resource}/items"

    def append(method, target, body):
        nonlocal current
        after = advance(current, {"method": method, "path": target, "body": body})
        batches.append(batch(method, target, body, current, after))
        current = after

    if kind == "replace":
        if current["track_uris"] == values:
            return current, batches
        for start in range(0, max(1, len(values)), BATCH_SIZE):
            append("PUT" if start == 0 else "POST", path, {"uris": values[start:start + BATCH_SIZE]})
    elif kind == "add":
        if position is not None and (type(position) is not int or not 0 <= position <= len(current["track_uris"])):
            raise ValueError("Insertion position is outside the baseline playlist")
        for start in range(0, len(values), BATCH_SIZE):
            body = {"uris": values[start:start + BATCH_SIZE]}
            if position is not None:
                body["position"] = position + start
            append("POST", path, body)
    elif kind == "remove":
        for start in range(0, len(values), BATCH_SIZE):
            append("DELETE", path, {"items": [{"uri": uri} for uri in values[start:start + BATCH_SIZE]]})
    elif kind == "details":
        body = {key: deepcopy(value) for key, value in (details or {}).items()
                if key in DETAIL_FIELDS and (key not in current["metadata"] or current["metadata"][key] != value)}
        if body:
            append("PUT", f"/playlists/{resource}", body)
    else:
        raise ValueError("Unsupported Spotify mutation kind")
    return current, batches
