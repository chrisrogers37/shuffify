"""Freeze complete ordered states and the actual 100-item Spotify batch boundaries."""

import hashlib
import json
from copy import deepcopy

BATCH_SIZE = 100
DETAIL_FIELDS = frozenset(("name", "public", "description"))


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
        "before": deepcopy(before), "after": deepcopy(after),
        "before_hash": state_hash(before), "after_hash": state_hash(after),
        "state": "prepared", "attempts": 0, "snapshot_before": None, "snapshot_id": None,
    }


def plan(kind, resource, before, *, uris=(), position=None, details=None):
    current = deepcopy(before)
    batches = []
    path = f"/playlists/{resource}/items"
    values = list(uris)
    if kind == "replace":
        if current["track_uris"] == values:
            return current, batches
        for start in range(0, max(1, len(values)), BATCH_SIZE):
            after = deepcopy(current)
            after["track_uris"] = values[:start + BATCH_SIZE]
            batches.append(batch("PUT" if start == 0 else "POST", path, {"uris": values[start:start + BATCH_SIZE]},
                                 current, after))
            current = after
    elif kind == "add":
        if position is not None and (type(position) is not int or not 0 <= position <= len(current["track_uris"])):
            raise ValueError("Insertion position is outside the baseline playlist")
        for start in range(0, len(values), BATCH_SIZE):
            body = {"uris": values[start:start + BATCH_SIZE]}
            if position is not None:
                body["position"] = position + start
            after = deepcopy(current)
            index = body.get("position", len(after["track_uris"]))
            after["track_uris"][index:index] = body["uris"]
            batches.append(batch("POST", path, body, current, after))
            current = after
    elif kind == "remove":
        for start in range(0, len(values), BATCH_SIZE):
            selected = values[start:start + BATCH_SIZE]
            removed = set(selected)
            after = deepcopy(current)
            after["track_uris"] = [uri for uri in current["track_uris"] if uri not in removed]
            batches.append(batch("DELETE", path, {"items": [{"uri": uri} for uri in selected]}, current, after))
            current = after
    elif kind == "details":
        body = {key: deepcopy(value) for key, value in (details or {}).items()
                if key in DETAIL_FIELDS and (key not in current["metadata"] or current["metadata"][key] != value)}
        after = deepcopy(current)
        after["metadata"].update(body)
        if after != current:
            batches.append(batch("PUT", f"/playlists/{resource}", body, current, after))
        current = after
    else:
        raise ValueError("Unsupported Spotify mutation kind")
    return current, batches
