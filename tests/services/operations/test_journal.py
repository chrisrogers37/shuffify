"""Write-ahead observation records every actual Spotify mutation attempt."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from shuffify.services.operations.journal import NeedsAttention
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import SpotifyWriteRejectedError
from shuffify.spotify.http_client import SpotifyHTTPClient
from shuffify.spotify.observation import observe_mutations
from tests.services.operations.test_crash_recovery import World, uri


def test_http_observer_checkpoints_before_dispatch_and_after_response():
    events = []
    response = SimpleNamespace(status_code=200)

    class Observer:
        def before_http(self, method, path, body):
            events.append(("intent", method, path, body))
            return "ticket"

        def after_http(self, ticket, value):
            assert value is response
            events.append(("ack", ticket))

        def http_failed(self, ticket, error):
            raise AssertionError("The request succeeded")

    client = SpotifyHTTPClient("fixture-token")
    client._session.request = lambda *args, **kwargs: events.append(("dispatch",)) or response
    with observe_mutations(Observer()):
        observed = client._perform_request(
            "PUT", "https://api.spotify.com/v1/playlists/target/items", json={"uris": []},
        )
        assert observed is response
    assert events == [
        ("intent", "PUT", "/playlists/target/items", {"uris": []}), ("dispatch",), ("ack", "ticket"),
    ]


def test_checkpoints_preserve_runtime_prepared_choices_and_service_results():
    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(2)])
    world.saved["runtime"] = {"prepared": {"random_order": [uri(2)]}, "steps": {"review": {"ok": True}}}
    world.saved["result"] = {"operation": {"kind": "shuffle"}}
    assert journal.resume()["state"] == "succeeded"
    assert world.saved["runtime"] == {
        "prepared": {"random_order": [uri(2)]}, "steps": {"review": {"ok": True}},
    }
    assert world.saved["result"] == {"operation": {"kind": "shuffle"}}


def test_full_plan_is_frozen_before_the_first_batch_and_each_ack_retains_the_snapshot():
    world = World([uri(1)])
    journal = world.journal()
    desired = [uri(index % 19) for index in range(250)]
    journal.freeze_replace("target", desired)
    frozen = world.saved["plans"][0]
    assert frozen["baseline"]["track_uris"] == [uri(1)]
    assert frozen["desired"]["track_uris"] == desired
    assert [len(item["body"]["uris"]) for item in frozen["batches"]] == [100, 100, 50]
    assert [item["method"] for item in frozen["batches"]] == ["PUT", "POST", "POST"]
    assert world.writes == []
    journal.resume()
    saved = world.saved["plans"][0]
    assert saved["batches_done"] == 3
    assert [item["snapshot_id"] for item in saved["batches"]] == ["snapshot-1", "snapshot-2", "snapshot-3"]


@pytest.mark.parametrize("kind", ["replace", "add", "remove", "details", "create"])
def test_high_level_replay_after_lost_step_output_reuses_the_frozen_plan(kind):
    world = World([uri(1), uri(2)])
    api = object.__new__(SpotifyAPI)
    api._cache, api._user_id = None, None
    calls = {
        "replace": lambda: api.update_playlist_tracks("target", [uri(3)]),
        "add": lambda: api.playlist_add_items("target", [uri(3)], position=0),
        "remove": lambda: api.playlist_remove_items("target", [uri(1)]),
        "details": lambda: api.update_playlist_details("target", public=True),
        "create": lambda: api.create_user_playlist("New playlist", public=False),
    }
    with world.journal().observe():
        first = calls[kind]()
    writes = deepcopy(world.writes)
    recovered = world.journal()
    assert recovered.resume()["state"] == "succeeded"
    with recovered.observe():
        replayed = calls[kind]()
    assert replayed == first
    assert world.writes == writes
    assert len(world.saved["plans"]) == 1


def test_mismatching_replayed_intent_stops_without_a_second_write():
    world = World([uri(1)])
    api = object.__new__(SpotifyAPI)
    api._cache, api._user_id = None, None
    with world.journal().observe():
        api.playlist_add_items("target", [uri(2)])
    writes = deepcopy(world.writes)
    with world.journal().observe(), pytest.raises(NeedsAttention):
        api.playlist_add_items("target", [uri(3)])
    assert world.writes == writes


def test_cached_command_step_advances_past_its_completed_mutation_range():
    world = World([uri(1)])
    api = object.__new__(SpotifyAPI)
    api._cache, api._user_id = None, None
    with world.journal().observe():
        api.playlist_add_items("target", [uri(2)])
        api.playlist_add_items("target", [uri(3)])
    writes = deepcopy(world.writes)
    recovered = world.journal()
    with recovered.observe():
        recovered.skip_to(1)
        api.playlist_add_items("target", [uri(3)])
    assert recovered.cursor == 2
    assert world.writes == writes


def test_lost_fence_before_actual_http_dispatch_makes_zero_external_attempts():
    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(2)])
    client = SpotifyHTTPClient("fixture-token")
    dispatched = []
    client._session.request = lambda *args, **kwargs: dispatched.append(args)
    journal.dispatch = lambda method, path, body: client._request(method, path, json=body)
    checkpoint = world.checkpoint

    def fence(document):
        if any(item["attempts"] for step in document["plans"] for item in step["batches"]):
            raise RuntimeError("lease lost")
        return checkpoint(document)

    journal.checkpoint = fence
    with pytest.raises(RuntimeError, match="lease lost"):
        journal.resume()
    assert dispatched == []


def test_compensation_sends_only_metadata_that_changed():
    world = World([uri(1)])
    world.playlists["target"]["metadata"]["description"] = None
    journal = world.journal()
    journal.freeze_details("target", public=True)
    journal.resume()
    assert journal.compensate()["state"] == "rolled_back"
    assert world.writes[-1] == ("PUT", "/playlists/target", {"public": False})


def test_legacy_restore_cannot_resume_a_failed_forward_plan_before_restoring():
    world = World([uri(1)])
    api = object.__new__(SpotifyAPI)
    api._cache, api._user_id = None, None
    journal = world.journal()
    original = world.dispatch

    def reject_second_batch(method, path, body):
        if world.writes:
            raise SpotifyWriteRejectedError("batch rejected")
        return original(method, path, body)

    journal.dispatch = reject_second_batch
    with journal.observe():
        with pytest.raises(SpotifyWriteRejectedError):
            api.update_playlist_tracks("target", [uri(index % 17) for index in range(250)])
        writes = deepcopy(world.writes)
        with pytest.raises(NeedsAttention):
            api.update_playlist_tracks("target", [uri(1)])
    assert world.writes == writes


def test_saved_rate_limit_deadline_survives_a_crash_and_blocks_all_spotify_io():
    from shuffify.services.operations.journal import OperationDeferred
    from tests.services.operations.test_crash_recovery import Crash

    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(2)])
    step = journal.doc["plans"][0]
    item = step["batches"][0]
    journal.active_batch = step, item
    ticket = journal.before_http("PUT", "/playlists/target/items", {"uris": [uri(2)]})
    response = SimpleNamespace(status_code=429, headers={"Retry-After": "120"})
    world.cut = len(world.trace) + 2  # Rejection persisted, but the caller never sees its exception.
    with pytest.raises(Crash):
        journal.after_http(ticket, response)
    world.cut = None
    world.trace = []
    recovered = world.journal()
    with pytest.raises(OperationDeferred) as deferred:
        recovered.resume()
    assert deferred.value.retry_after > 0
    assert deferred.value.next_attempt_at.isoformat() == world.saved["plans"][0]["batches"][0]["next_attempt_at"]
    assert world.trace == []
    assert world.writes == []
    api = object.__new__(SpotifyAPI)
    api._cache, api._user_id = None, None
    with recovered.observe(), pytest.raises(OperationDeferred):
        api.update_playlist_tracks("target", [uri(2)])
    assert world.trace == []


def test_rate_limit_defers_the_http_loop_immediately_and_can_resume_when_due():
    from datetime import datetime, timedelta, timezone

    from shuffify.services.operations.journal import OperationDeferred

    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(2)])
    client = SpotifyHTTPClient("fixture-token")
    attempts = []
    client._session.request = lambda *args, **kwargs: attempts.append(args) or SimpleNamespace(
        status_code=429, headers={"Retry-After": "120"},
    )
    journal.dispatch = lambda method, path, body: client._request(method, path, json=body)
    with pytest.raises(OperationDeferred):
        journal.resume()
    assert len(attempts) == 1
    assert world.saved["plans"][0]["batches"][0]["state"] == "rejected"
    world.saved["plans"][0]["batches"][0]["next_attempt_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    assert world.journal().resume()["state"] == "succeeded"
    assert len(world.writes) == 1


def test_changed_item_content_with_the_old_snapshot_cannot_acknowledge_a_lost_response():
    from tests.services.operations.test_crash_recovery import Crash

    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(2)])
    original = world.dispatch

    def lost_response(*args):
        original(*args)
        raise Crash()

    journal.dispatch = lost_response
    with pytest.raises(Crash):
        journal.resume()
    world.playlists["target"]["snapshot_id"] = "snapshot-0"
    writes = deepcopy(world.writes)
    outcome = world.journal().resume()
    assert outcome["state"] == "needs_attention"
    assert outcome["recovery"]["reason"] == "snapshot_ambiguous"
    assert world.writes == writes


def test_unknown_original_metadata_is_never_blindly_compensated():
    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_details("target", description="changed")
    assert journal.resume()["state"] == "succeeded"
    writes = deepcopy(world.writes)
    outcome = journal.compensate()
    assert outcome["state"] == "needs_attention"
    assert outcome["recovery"]["reason"] == "baseline_metadata_unavailable"
    assert world.writes == writes


def test_a_new_create_cannot_be_frozen_while_an_earlier_dispatch_is_unresolved():
    from tests.services.operations.test_crash_recovery import Crash

    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(2)])
    journal.dispatch = lambda *args: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        journal.resume()
    with pytest.raises(NeedsAttention):
        journal.freeze_create("Do not create")
    assert len(world.saved["plans"]) == 1
