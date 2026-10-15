"""Routine shuffle freezes occurrence metadata and randomness across process interruption."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from shuffify.services.operations.executors.shuffle import ShuffleConfig, execute_shuffle
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.workshop_commit_service import ObservedPlaylist, WorkshopCommitService
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from tests.services.operations.test_worker_journal_integration import (
    PLAYLIST,
    ProcessStopped,
    SpotifyWorld,
)

pytest_plugins = ["tests.services.operations.test_worker_journal_integration"]

BEFORE = ["spotify:track:" + str(index).zfill(22) for index in [1, 1, 2]]


def observe_tracks(world):
    return ObservedPlaylist(
        "Example playlist", tuple(world.uris),
        tuple({"uri": value, "name": "Example track", "artists": [{"name": "Example artist"}],
               "album": {}, "added_at": f"2025-01-0{index + 1}T00:00:00Z"}
              for index, value in enumerate(world.uris)),
        "2025-01-01T00:00:00Z", str(world.snapshot), dict(world.metadata),
    )


def test_shuffle_reuses_one_frozen_choice_and_each_occurrences_added_at_after_lost_reply(setup_worker, monkeypatch):
    repository, build, accept, expire = setup_worker
    world = SpotifyWorld(BEFORE)
    world.cut = 1
    choices = []

    class Algorithm:
        def shuffle(self, tracks, **params):
            choices.append(deepcopy(tracks))
            return [track["uri"] for track in reversed(tracks)]

    monkeypatch.setattr(ShuffleRegistry, "get_algorithm", lambda name: Algorithm)
    monkeypatch.setattr(WorkshopCommitService, "read_complete", lambda self, target, owner: observe_tracks(world))

    def handler(runtime):
        config = ShuffleConfig(runtime.user.id, PLAYLIST, "BasicShuffle", {}, {})
        value = execute_shuffle(runtime, config, prepare_key="shuffle.plan")
        return {"success": True, **value["result"]}

    row = accept()
    with pytest.raises(ProcessStopped):
        build(world, handler).run_once(row.id)
    expire(row.id)
    assert build(world, handler).run_once(row.id) is True
    assert repository.get(row.id).state == "succeeded"
    assert world.uris == list(reversed(BEFORE))
    assert world.calls == 1
    assert len(choices) == 1
    assert [track["added_at"] for track in choices[0]] == [
        "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", "2025-01-03T00:00:00Z",
    ]


@pytest.mark.parametrize("empty", [False, True])
def test_shuffle_with_no_unlocked_tracks_never_runs_the_algorithm_or_writes(setup_worker, monkeypatch, empty):
    repository, build, accept, _expire = setup_worker
    world = SpotifyWorld([] if empty else BEFORE)
    monkeypatch.setattr(WorkshopCommitService, "read_complete", lambda self, target, owner: observe_tracks(world))
    monkeypatch.setattr(ShuffleRegistry, "get_algorithm", lambda name: pytest.fail("There is nothing to shuffle"))

    def handler(runtime):
        locked = {} if empty else dict(enumerate(BEFORE))
        value = execute_shuffle(runtime, ShuffleConfig(runtime.user.id, PLAYLIST, "BasicShuffle", {}, locked),
                                prepare_key="shuffle.plan")
        assert value["result"] == ({"tracks_added": 0, "tracks_total": 0} if empty else {
            "tracks_added": 0, "tracks_total": 3, "skipped_reason": "all_tracks_locked",
        })
        assert value["before"][PLAYLIST]["track_uris"] == world.uris
        assert value["after"][PLAYLIST]["track_uris"] == world.uris
        return {"success": True}

    row = accept()
    assert build(world, handler).run_once(row.id) is True
    assert repository.get(row.id).state == "succeeded"
    assert world.calls == 0


@pytest.mark.parametrize("user_id,target", [(999, PLAYLIST), (1, "unleased")])
def test_shuffle_rejects_a_foreign_user_or_unleased_target_before_api_access(user_id, target):
    runtime = SimpleNamespace(user=SimpleNamespace(id=1), lease=SimpleNamespace(targets=(PLAYLIST,)))
    with pytest.raises(ValueError):
        execute_shuffle(runtime, ShuffleConfig(user_id, target, "BasicShuffle", {}, {}), prepare_key="shuffle.plan")


def test_algorithm_cannot_drop_a_duplicate_occurrence_before_dispatch(setup_worker, monkeypatch):
    repository, build, accept, _expire = setup_worker
    world = SpotifyWorld(BEFORE)

    class InvalidAlgorithm:
        def shuffle(self, tracks, **params):
            return list(dict.fromkeys(track["uri"] for track in tracks))

    monkeypatch.setattr(ShuffleRegistry, "get_algorithm", lambda name: InvalidAlgorithm)
    monkeypatch.setattr(WorkshopCommitService, "read_complete", lambda self, target, owner: observe_tracks(world))

    def handler(runtime):
        value = execute_shuffle(runtime, ShuffleConfig(runtime.user.id, PLAYLIST, "BasicShuffle", {}, {}),
                                prepare_key="shuffle.plan")
        return {"success": True, **value["result"]}

    row = accept()
    assert build(world, handler).run_once(row.id) is True
    assert repository.get(row.id).state == "failed"
    assert world.uris == BEFORE
    assert world.calls == 0


def test_snapshot_only_edit_after_preparation_is_preserved_without_dispatch(setup_worker, monkeypatch):
    repository, build, accept, _expire = setup_worker
    world = SpotifyWorld(BEFORE)
    monkeypatch.setattr(WorkshopCommitService, "read_complete", lambda self, target, owner: observe_tracks(world))
    original = OperationRuntime.prepare

    def prepare_then_edit(self, *args):
        result = original(self, *args)
        world.snapshot += 1
        return result

    monkeypatch.setattr(OperationRuntime, "prepare", prepare_then_edit)

    def handler(runtime):
        value = execute_shuffle(runtime, ShuffleConfig(runtime.user.id, PLAYLIST, "BasicShuffle", {}, {}),
                                prepare_key="shuffle.plan")
        return {"success": True, **value["result"]}

    row = accept()
    assert build(world, handler).run_once(row.id) is True
    assert repository.get(row.id).state == "needs_attention"
    assert world.uris == BEFORE
    assert world.calls == 0
