"""Write-ahead observation records every actual Spotify mutation attempt."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from shuffify.services.operations.journal import JournalError, NeedsAttention
from shuffify.services.operations.planner import PlanTooLarge
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


def test_compensation_waits_for_a_rejected_forward_batch_retry_hint():
    from datetime import datetime, timedelta, timezone

    from shuffify.services.operations.journal import OperationDeferred

    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(index) for index in range(2, 152)])
    client = SpotifyHTTPClient("fixture-token")
    client._session.request = lambda *args, **kwargs: SimpleNamespace(
        status_code=429, headers={"Retry-After": "120"},
    )
    dispatched = []

    def first_batch_lands_then_rate_limited(method, path, body):
        dispatched.append(method)
        if len(dispatched) == 1:
            return world.dispatch(method, path, body)
        return client._request(method, path, json=body)

    journal.dispatch = first_batch_lands_then_rate_limited
    with pytest.raises(OperationDeferred):
        journal.resume()
    batches = world.saved["plans"][0]["batches"]
    assert [item["state"] for item in batches] == ["acknowledged", "rejected"]
    writes = deepcopy(world.writes)
    world.trace = []

    with pytest.raises(OperationDeferred) as deferred:
        world.journal().compensate()
    assert deferred.value.next_attempt_at.isoformat() == batches[1]["next_attempt_at"]
    assert not any(point.startswith(("read.", "spotify.")) for point in world.trace)
    assert world.writes == writes and world.saved["phase"] == "forward"

    world.saved["plans"][0]["batches"][1]["next_attempt_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    assert world.journal().compensate()["state"] == "rolled_back"
    assert world.playlists["target"]["track_uris"] == [uri(1)]


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


def test_a_plan_beyond_a_full_playlist_is_refused_before_it_is_frozen():
    world = World([uri(1)])
    with pytest.raises(PlanTooLarge):
        world.journal().freeze_replace("target", [uri(index) for index in range(10_001)])
    assert world.saved is None
    assert world.writes == []


@pytest.mark.parametrize("kind", ["replace", "add", "remove"])
def test_a_frozen_plan_stays_proportional_to_its_track_list(kind):
    sizes = []
    for count in (1_000, 4_000):
        tracks = [uri(index) for index in range(count)]
        world = World(tracks if kind == "remove" else [uri(1)])
        getattr(world.journal(), f"freeze_{kind}")("target", tracks)
        sizes.append(len(json.dumps(world.saved)))
    # Four times the tracks take about four times the bytes.
    assert sizes[1] < 5 * sizes[0]


def test_a_batch_that_no_longer_matches_its_frozen_plan_is_never_sent():
    world = World([uri(1)])
    world.journal().freeze_replace("target", [uri(100 + index) for index in range(150)])
    world.saved["plans"][0]["batches"][1]["body"]["uris"][0] = uri(999)
    with pytest.raises(JournalError):
        world.journal().resume()
    assert [method for method, _, _ in world.writes] == ["PUT"]


def with_full_batch_states(document):
    """Add the complete before and after states that each batch of a whole-list replacement once stored."""
    for step in document["plans"] + document.get("compensation", []):
        baseline, desired = step["baseline"], step["desired"]["track_uris"]
        ends = range(100, len(desired) + 100, 100)
        states = [baseline, *({**baseline, "track_uris": desired[:end]} for end in ends)]
        assert len(states) == len(step["batches"]) + 1, "only whole-list replacements are rebuilt here"
        for item, before, after in zip(step["batches"], states, states[1:]):
            item["before"], item["after"] = deepcopy(before), deepcopy(after)
    return document


def test_a_journal_with_full_batch_states_resumes_after_a_lost_response():
    from tests.services.operations.test_crash_recovery import Crash

    world = World([uri(1), uri(2)])
    desired = [uri(100 + index) for index in range(250)]
    journal = world.journal()
    journal.freeze_replace("target", desired)
    dispatch = world.dispatch

    def second_response_lost(*args):
        response = dispatch(*args)
        if len(world.writes) == 2:
            raise Crash()
        return response

    journal.dispatch = second_response_lost
    with pytest.raises(Crash):
        journal.resume()
    world.saved = with_full_batch_states(world.saved)
    assert world.journal().resume()["state"] == "succeeded"
    assert world.playlists["target"]["track_uris"] == desired
    assert [method for method, _, _ in world.writes] == ["PUT", "POST", "POST"]


def test_a_journal_with_full_batch_states_compensates_to_its_original_tracks():
    world = World([uri(1), uri(2)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(100 + index) for index in range(250)])
    dispatch = world.dispatch

    def second_batch_rejected(method, path, body):
        if world.writes:
            raise SpotifyWriteRejectedError("batch rejected")
        return dispatch(method, path, body)

    journal.dispatch = second_batch_rejected
    with pytest.raises(SpotifyWriteRejectedError):
        journal.resume()
    world.saved = with_full_batch_states(world.saved)
    assert world.journal().compensate()["state"] == "rolled_back"
    assert world.playlists["target"]["track_uris"] == [uri(1), uri(2)]


def test_a_compensation_with_full_batch_states_resumes_after_a_lost_response():
    from tests.services.operations.test_crash_recovery import Crash

    original = [uri(100 + index) for index in range(250)]
    world = World(original)
    journal = world.journal()
    journal.freeze_replace("target", [uri(1)])
    assert journal.resume()["state"] == "succeeded"
    dispatch = world.dispatch

    def second_rollback_response_lost(*args):
        response = dispatch(*args)
        if len(world.writes) == 3:
            raise Crash()
        return response

    journal = world.journal()
    journal.dispatch = second_rollback_response_lost
    with pytest.raises(Crash):
        journal.compensate()
    world.saved = with_full_batch_states(world.saved)
    assert world.journal().resume()["state"] == "rolled_back"
    assert world.playlists["target"]["track_uris"] == original
    assert [method for method, _, _ in world.writes] == ["PUT", "PUT", "POST", "POST"]


def test_a_new_plan_continues_from_a_journal_with_full_batch_states():
    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace("target", [uri(100 + index) for index in range(150)])
    assert journal.resume()["state"] == "succeeded"
    world.saved = with_full_batch_states(world.saved)
    second = [uri(300 + index) for index in range(150)]
    journal = world.journal()
    journal.freeze_replace("target", second)
    assert journal.resume()["state"] == "succeeded"
    assert world.playlists["target"]["track_uris"] == second
    assert [method for method, _, _ in world.writes] == ["PUT", "POST", "PUT", "POST"]
