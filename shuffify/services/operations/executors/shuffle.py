"""Prepare a routine shuffle once and apply its frozen ordered intent."""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass

from shuffify.services.executors.base_executor import JobExecutionError, PlaylistVerificationError
from shuffify.services.operations.planner import content
from shuffify.services.workshop_commit_service import IncompletePlaylistRead, WorkshopCommitService
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.shuffle_algorithms.utils import reassemble_with_locks, split_locked_tracks
from shuffify.spotify.observation import current_observer

from .resources import protect_verified_resources


@dataclass(frozen=True)
class ShuffleConfig:
    user_id: int
    target_playlist_id: str
    algorithm_name: str
    algorithm_params: dict
    locked_positions: dict[int, str]


def prepare_shuffle(runtime, config, *, prepare_key):
    """Freeze authorized occurrence metadata and algorithm choices without local or Spotify writes."""
    target = config.target_playlist_id
    if config.user_id != runtime.user.id or target not in runtime.lease.targets:
        raise ValueError("The shuffle configuration differs from its execution lease")
    if config.algorithm_name not in ShuffleRegistry.get_available_algorithms():
        raise JobExecutionError("A known algorithm is required for shuffle")
    runtime.check_pending_control()
    identity = {"user_id": config.user_id, "target_playlist_id": target, "algorithm_name": config.algorithm_name,
                "algorithm_params": deepcopy(config.algorithm_params),
                "locked_positions": sorted([position, uri] for position, uri in config.locked_positions.items())}

    def prepare():
        observed = WorkshopCommitService(runtime.api).read_complete(target, runtime.user.spotify_id)
        baseline = runtime.read_resource(target)
        if list(observed.track_uris) != baseline["track_uris"] or observed.snapshot_id != baseline["snapshot_id"]:
            raise IncompletePlaylistRead("Playlist changed during shuffle preparation")
        tracks = [
            {**deepcopy(track), "artists": [artist.get("name", "") if isinstance(artist, dict) else artist
                                           for artist in track.get("artists", [])]}
            for track in observed.tracks
        ]
        result = {"tracks_added": 0, "tracks_total": len(tracks)}
        desired = list(observed.track_uris)
        if tracks:
            validated, unlocked = split_locked_tracks(tracks, config.locked_positions)
            if validated and not unlocked:
                result["skipped_reason"] = "all_tracks_locked"
            else:
                algorithm = ShuffleRegistry.get_algorithm(config.algorithm_name)()
                desired = list(algorithm.shuffle(unlocked, **deepcopy(config.algorithm_params)))
                if validated:
                    desired = reassemble_with_locks(desired, validated, len(tracks))
        if Counter(desired) != Counter(observed.track_uris):
            raise JobExecutionError("The shuffle result must preserve every track occurrence")
        after = {**deepcopy(baseline), "track_uris": desired}
        return {"version": 1, "config": identity, "before": {target: baseline}, "desired": {target: after},
                "result": result,
                "choices": {"algorithm": config.algorithm_name, "params": deepcopy(config.algorithm_params)}}

    prepared = runtime.prepare(prepare_key, prepare)
    if prepared["version"] != 1 or prepared["config"] != identity:
        raise ValueError("The prepared shuffle configuration differs from its command")
    return prepared


def execute_shuffle(runtime, config, *, prepare_key):
    """Return complete before/after resources and counts; the caller commits local effects."""
    prepared = prepare_shuffle(runtime, config, prepare_key=prepare_key)
    if current_observer() is not runtime.journal:
        raise JobExecutionError("Shuffle application requires its operation's journal observer")
    target = config.target_playlist_id
    baseline, desired = prepared["before"][target], prepared["desired"][target]["track_uris"]

    def apply():
        runtime.journal.ensure_replace(target, desired, expected_baseline=baseline)
        runtime.api.update_playlist_tracks(target, desired)
        observed = runtime.read_resource(target)
        expected = {**baseline, "track_uris": desired}
        if content(observed) != content(expected):
            raise PlaylistVerificationError(target, desired, observed["track_uris"],
                                            runtime.operation.schedule_id, "shuffle")
        protect_verified_resources(runtime)
        return observed

    after = runtime.step(prepare_key + ".apply", apply)
    return {"before": deepcopy(prepared["before"]), "after": {target: after},
            "result": deepcopy(prepared["result"]), "choices": deepcopy(prepared["choices"])}
