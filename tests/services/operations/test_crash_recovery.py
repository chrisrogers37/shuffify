"""Crash every durable and Spotify boundary, including applied writes with lost responses."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from shuffify.models.db import JobExecution
from shuffify.services.operations.journal import DurableJournal
from shuffify.spotify.api import SpotifyAPI


class Crash(BaseException):
    pass


def uri(index):
    return f"spotify:track:{index:022d}"


def expire_lease(repository, operation_id):
    """Lapse one operation's lease now, so a crashed run is recoverable without waiting on the clock."""
    with repository.engine.begin() as connection:
        lapsed = connection.execute(
            update(JobExecution)
            .where(JobExecution.id == operation_id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
    assert lapsed.rowcount == 1


class World:
    def __init__(self, tracks=()):
        self.playlists = {"target": {"track_uris": list(tracks), "metadata": {"name": "Target", "public": False},
                                      "snapshot_id": "snapshot-0", "settled": True}}
        self.saved = None
        self.trace = []
        self.cut = None
        self.writes = []
        self.revision = 0
        # Opt-in read lag: after each effective write, the next `lag_reads`
        # reads of the written playlist return the pre-write snapshot_id
        # ("snapshot") or the whole pre-write playlist ("full") while the
        # write itself is applied. `lag_resources` limits it to some playlists.
        self.lag_reads = 0
        self.lag_mode = "snapshot"
        self.lag_resources = None
        self.lag_left = {}
        self.lag_view = {}

    def point(self, label):
        self.trace.append(label)
        if len(self.trace) == self.cut:
            raise Crash(label)

    def checkpoint(self, document):
        self.point("checkpoint.before")
        self.saved = deepcopy(document)
        self.point("checkpoint.after")
        return deepcopy(self.saved)

    def read(self, resource):
        self.point("read.before")
        state = deepcopy(self.playlists[resource])
        if self.lag_left.get(resource):
            self.lag_left[resource] -= 1
            view = self.lag_view[resource]
            if self.lag_mode == "full":
                state = deepcopy(view)
            elif self.lag_mode == "items":
                # The track list lags while the snapshot is already the fresh one.
                state = {**deepcopy(view), "snapshot_id": state["snapshot_id"]}
            else:
                state = {**state, "snapshot_id": view["snapshot_id"]}
        self.point("read.after")
        return state

    def dispatch(self, method, path, body):
        self.point("spotify.before")
        self.writes.append((method, path, deepcopy(body)))
        if path == "/me/playlists":
            resource = "created-1"
            self.playlists[resource] = {"track_uris": [], "metadata": deepcopy(body),
                                        "snapshot_id": "created-snapshot", "settled": True}
            self.point("spotify.applied")
            return {"id": resource, "snapshot_id": "created-snapshot", **body}
        resource = path.split("/")[2]
        state = self.playlists[resource]
        before = deepcopy(state)
        if path.endswith("/items"):
            if method == "PUT":
                state["track_uris"] = list(body["uris"])
            elif method == "POST":
                position = body.get("position", len(state["track_uris"]))
                state["track_uris"][position:position] = body["uris"]
            else:
                removed = {item["uri"] for item in body["items"]}
                state["track_uris"] = [track for track in state["track_uris"] if track not in removed]
        else:
            state["metadata"].update(body)
        if state != before:
            self.revision += 1
            state["snapshot_id"] = f"snapshot-{self.revision}"
            if self.lag_reads and (self.lag_resources is None or resource in self.lag_resources):
                self.lag_left[resource], self.lag_view[resource] = self.lag_reads, before
        self.point("spotify.applied")
        result = {"snapshot_id": state["snapshot_id"]} if path.endswith("/items") else None
        self.point("spotify.response")
        return result

    def journal(self):
        return DurableJournal(lambda: deepcopy(self.saved), self.checkpoint, self.read, self.dispatch)


def prepare(world, kind, size):
    journal = world.journal()
    values = [uri(100 + index % 17) for index in range(size)]
    if kind == "replace":
        journal.freeze_replace("target", values)
        expected = values
    elif kind == "add":
        journal.freeze_add("target", values, position=1)
        expected = world.playlists["target"]["track_uris"][:1] + values + world.playlists["target"]["track_uris"][1:]
    else:
        values = [uri(index % 13) for index in range(size)]
        journal.freeze_remove("target", values)
        expected = [track for track in world.playlists["target"]["track_uris"] if track not in set(values)]
    return journal, expected


@pytest.mark.parametrize("size", [0, 1, 99, 100, 101, 250, 1000])
@pytest.mark.parametrize("kind", ["replace", "add", "remove"])
def test_every_crash_boundary_finishes_the_frozen_plan_without_duplicate_writes(kind, size):
    baseline = [uri(index % 13) for index in range(max(3, size))]
    probe = World(baseline)
    journal, expected = prepare(probe, kind, size)
    assert journal.resume()["state"] == "succeeded"
    assert probe.playlists["target"]["track_uris"] == expected
    cuts = len(probe.trace)
    assert "spotify.applied" in probe.trace or kind in ("add", "remove") and size == 0

    for cut in range(1, cuts + 1):
        world = World(baseline)
        world.cut = cut
        try:
            operation, frozen = prepare(world, kind, size)
            operation.resume()
        except Crash:
            pass
        world.cut = None
        before_recovery = deepcopy(world.playlists)
        if world.saved is None:
            recovered, frozen = prepare(world, kind, size)
        else:
            recovered, frozen = world.journal(), expected
        outcome = recovered.resume()
        if outcome["state"] == "needs_attention":
            assert world.playlists == before_recovery
        else:
            assert outcome["state"] == "succeeded", (kind, size, cut)
            assert world.playlists["target"]["track_uris"] == frozen, (kind, size, cut)


@pytest.mark.parametrize("other", ["aaa-other", "zzz-other"])
def test_all_resources_are_reconciled_before_any_resume_and_external_edits_are_untouched(other):
    world = World([uri(1), uri(1), uri(2)])
    world.playlists[other] = deepcopy(world.playlists["target"])
    journal = world.journal()
    journal.freeze_replace("target", [uri(3)])
    journal.freeze_replace(other, [uri(4)])
    world.playlists[other]["track_uris"] = [uri(99)]
    world.playlists[other]["snapshot_id"] = "external-edit"
    before = deepcopy(world.playlists)

    assert world.journal().resume()["state"] == "needs_attention"
    assert world.playlists == before
    assert world.writes == []
    restore = world.journal().restore_plan()
    assert restore["requires_review"] is True
    assert {row["resource"] for row in restore["resources"]} == {"target", other}


def test_unsettled_ambiguous_dispatch_at_baseline_is_never_replayed():
    world = World([uri(1)])
    operation = world.journal()
    operation.freeze_replace("target", [uri(2)])
    world.cut = len(world.trace) + 1
    # Crash immediately after the next persisted intent but before the external effect.

    def crash_dispatch(*args):
        raise Crash()
    operation.dispatch = crash_dispatch
    world.cut = None
    with pytest.raises(Crash):
        operation.resume()
    world.playlists["target"]["settled"] = False
    assert world.journal().resume()["state"] == "needs_attention"
    assert world.writes == []


def test_unknown_creation_is_never_posted_again():
    world = World()
    journal = world.journal()
    journal.freeze_create("New playlist", public=False)
    world.cut = None
    original_dispatch = world.dispatch

    def lost_response(*args):
        original_dispatch(*args)
        raise Crash()
    journal.dispatch = lost_response
    with pytest.raises(Crash):
        journal.resume()
    assert world.journal().resume()["state"] == "needs_attention"
    assert len(world.writes) == 1
    assert world.writes[0][:2] == ("POST", "/me/playlists")


@pytest.mark.parametrize("size", [0, 1, 99, 100, 101, 250, 1000])
def test_every_compensation_crash_resumes_the_compensation_plan(size):
    baseline = [uri(index % 11) for index in range(max(3, size))]
    original = World(baseline)
    journal, _ = prepare(original, "replace", size)
    journal.resume()
    original.trace = []
    probe = deepcopy(original)
    assert probe.journal().compensate()["state"] == "rolled_back"

    for cut in range(1, len(probe.trace) + 1):
        world = deepcopy(original)
        world.cut = cut
        try:
            world.journal().compensate()
        except Crash:
            pass
        world.cut = None
        recovered = world.journal()
        if world.saved["phase"] != "compensating":
            result = recovered.compensate()
        else:
            result = recovered.resume()
        assert result["state"] == "rolled_back", (size, cut)
        assert world.playlists["target"]["track_uris"] == baseline, (size, cut)


def test_compensation_never_restores_over_an_external_edit():
    world = World([uri(1), uri(2)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(3)])
    journal.resume()
    world.playlists["target"]["track_uris"] = [uri(99)]
    world.playlists["target"]["snapshot_id"] = "external-edit"
    before = deepcopy(world.playlists)
    written = len(world.writes)
    assert world.journal().compensate()["state"] == "needs_attention"
    assert world.playlists == before
    assert len(world.writes) == written


def test_the_property_rejects_the_old_request_path_adapter_without_a_frozen_plan():
    world = World([uri(1)])
    desired = [uri(100 + index % 17) for index in range(250)]
    api = object.__new__(SpotifyAPI)
    api._cache, api._user_id = None, None
    api._ensure_valid_token = lambda: None
    api._http = SimpleNamespace(
        put=lambda path, json: world.dispatch("PUT", path, json),
        post=lambda path, json: world.dispatch("POST", path, json),
    )
    # Exercise the real request-path batching with the observer disabled.
    world.cut = 5  # The second batch applied, but its answer was lost.
    with pytest.raises(Crash):
        api.update_playlist_tracks("target", desired)
    assert world.saved is None
    world.cut = None
    rerandomized = [uri(700 + index % 17) for index in range(250)]
    assert api.update_playlist_tracks("target", rerandomized)
    with pytest.raises(AssertionError):
        assert world.playlists["target"]["track_uris"] == desired


def test_an_external_edit_during_compensation_recovery_is_not_overwritten():
    world = World([uri(index % 13) for index in range(250)])
    journal, _ = prepare(world, "replace", 250)
    journal.resume()
    dispatch = world.dispatch

    def stop_after_rollback_batch(*args):
        dispatch(*args)
        raise Crash()

    journal.dispatch = stop_after_rollback_batch
    with pytest.raises(Crash):
        journal.compensate()
    world.playlists["target"]["track_uris"] = [uri(999)]
    world.playlists["target"]["snapshot_id"] = "external-edit"
    before = deepcopy(world.playlists)
    writes = len(world.writes)
    assert world.journal().resume()["state"] == "needs_attention"
    assert world.playlists == before
    assert len(world.writes) == writes


@pytest.mark.parametrize("kind", ["details", "create"])
def test_metadata_and_create_crashes_never_duplicate_effects(kind):
    def freeze(world):
        operation = world.journal()
        if kind == "details":
            operation.freeze_details("target", public=True, name="Changed")
        else:
            operation.freeze_create("New playlist", public=False)
        return operation

    probe = World([uri(1)])
    assert freeze(probe).resume()["state"] == "succeeded"
    for cut in range(1, len(probe.trace) + 1):
        world = World([uri(1)])
        world.cut = cut
        try:
            freeze(world).resume()
        except Crash:
            pass
        world.cut = None
        prior = deepcopy(world.playlists)
        prior_calls = len(world.writes)
        recovered = freeze(world) if world.saved is None else world.journal()
        result = recovered.resume()
        assert result["state"] in {"succeeded", "needs_attention"}
        if result["state"] == "needs_attention":
            assert world.playlists == prior
            assert len(world.writes) == prior_calls
        else:
            if kind == "details":
                assert world.playlists["target"]["metadata"] == {"name": "Changed", "public": True}
            else:
                assert world.saved["plans"][0]["result"]["id"] == "created-1"
        assert len(world.writes) <= 1
