"""Restoration uses exact Spotify batch boundaries and retains one reviewed intent across recovery."""

import time
from copy import deepcopy

import pytest
import requests

from shuffify.models.db import ActivityLog
from shuffify.services.operations.commands.restore import operation_restore
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import Crash, expire_lease, uri
from tests.services.operations.test_restore_command import replace_parent
from tests.services.operations.test_restore_command import restoration as restoration
from tests.services.operations.test_routine_commands import routine as routine


def parent_order(case, size):
    values = [uri(index % 17) for index in range(size)]

    def setup(journal):
        case.world.playlists["target"]["track_uris"] = values
        journal.begin_step("original")
        journal.freeze_replace("target", [uri(999)])
        journal.resume()

    replace_parent(case, setup)
    return values


def factory(case):
    def create(user):
        api = case.factory(user)
        original = api.get_playlist_items_raw

        def page(resource, limit=100, offset=0):
            value = original(resource, limit=limit, offset=offset)
            end = offset + len(value["items"])
            value["next"] = (
                f"https://api.spotify.com/v1/playlists/{resource}/items?offset={end}" if end < value["total"] else None
            )
            return value

        api.get_playlist_items_raw = page
        return api

    return create


@pytest.mark.parametrize("size", [0, 1, 99, 100, 101, 250, 1000])
def test_every_batch_boundary_restores_the_complete_order_with_duplicates(restoration, size):
    expected = parent_order(restoration, size)
    row = restoration.enqueue_restore()
    finished, completed = restoration.run_restore(row, factory(restoration))
    assert finished and completed.state == "succeeded"
    assert restoration.world.playlists["target"]["track_uris"] == expected
    assert [method for method, _, _ in restoration.world.writes] == ["PUT"] + ["POST"] * ((max(1, size) - 1) // 100)
    assert [len(body["uris"]) for _, _, body in restoration.world.writes] == [
        len(expected[index : index + 100]) for index in range(0, max(1, size), 100)
    ]
    assert all(batch["attempts"] == 1 for batch in completed.journal["plans"][0]["batches"])
    assert ActivityLog.query.count() == 1


@pytest.mark.parametrize("cut", [1, 2, 3])
@pytest.mark.parametrize("phase", ["ack", "applied_response_lost"])
def test_each_http_call_cut_recovers_without_repeating_a_confirmed_batch(restoration, monkeypatch, cut, phase):
    expected = parent_order(restoration, 250)
    row = restoration.enqueue_restore()
    checkpoint, dispatch = restoration.repository.checkpoint, restoration.world.dispatch
    fired, calls = [], []

    def saved(lease, document, **kwargs):
        result = checkpoint(lease, document, **kwargs)
        acknowledged = sum(
            batch["state"] == "acknowledged" for plan in document.get("plans", []) for batch in plan["batches"]
        )
        if phase == "ack" and acknowledged == cut and not fired:
            fired.append(True)
            raise Crash("after ACK")
        return result

    def lost(method, path, body):
        result = dispatch(method, path, body)
        calls.append(True)
        if phase == "applied_response_lost" and len(calls) == cut and not fired:
            fired.append(True)
            raise Crash("applied response lost")
        return result

    with monkeypatch.context() as patches:
        patches.setattr(restoration.repository, "checkpoint", saved)
        patches.setattr(restoration.world, "dispatch", lost)
        with pytest.raises(Crash):
            restoration.run_restore(row, factory(restoration))
    expire_lease(restoration.repository, row.id)
    finished, completed = restoration.run_restore(row, factory(restoration))
    assert fired and finished and completed.state == "succeeded"
    assert restoration.world.playlists["target"]["track_uris"] == expected
    assert [len(body["uris"]) for _, _, body in restoration.world.writes] == [100, 100, 50]
    assert [batch["attempts"] for batch in completed.journal["plans"][0]["batches"]] == [1, 1, 1]
    assert ActivityLog.query.count() == 1


def test_retry_after_retains_original_restore_and_resumes_only_after_the_hint(restoration):
    parent_order(restoration, 101)
    attempted = []
    base_factory = factory(restoration)

    def api_factory(user):
        api = base_factory(user)
        send = api._http._session.request

        def limited(method, url, **kwargs):
            attempted.append(method)
            if len(attempted) == 2:
                response = requests.Response()
                response.status_code = 429
                response.headers["Retry-After"] = "1"
                response._content = b"{}"
                return response
            return send(method, url, **kwargs)

        api._http._session.request = limited
        return api

    row = restoration.enqueue_restore()
    worker = OperationWorker(
        restoration.app,
        repository=restoration.repository,
        handlers={"operation_restore": operation_restore},
        api_factory=api_factory,
        heartbeat_interval=0,
    )
    assert worker.run_once(row.id) is False
    pending = restoration.repository.get(row.id)
    assert pending.state == "running" and pending.lease_owner is None
    assert ActivityLog.query.count() == 0 and len(restoration.world.writes) == 1
    assert worker.run_once(row.id) is False
    time.sleep(1.05)
    assert worker.run_once(row.id) is True
    completed = restoration.repository.get(row.id)
    assert completed.state == "succeeded" and len(restoration.world.writes) == 2
    assert [batch["attempts"] for batch in completed.journal["plans"][0]["batches"]] == [1, 2]
    assert ActivityLog.query.count() == 1


def test_failed_later_restore_batch_rolls_back_only_the_child_and_never_resolves_parent(restoration):
    parent_order(restoration, 101)
    before = deepcopy(restoration.world.playlists["target"])
    base_factory = factory(restoration)

    def api_factory(user):
        api = base_factory(user)
        send = api._http._session.request
        attempts = []

        def reject(method, url, **kwargs):
            attempts.append(method)
            if len(attempts) == 2:
                response = requests.Response()
                response.status_code, response._content = 403, b"{}"
                return response
            return send(method, url, **kwargs)

        api._http._session.request = reject
        return api

    finished, completed = restoration.run_restore(restoration.enqueue_restore(), api_factory)
    assert finished and completed.state == "rolled_back"
    assert restoration.world.playlists["target"]["track_uris"] == before["track_uris"]
    assert completed.journal["result"]["changed_position_count"] == 0
    assert ActivityLog.query.count() == 0
    assert not restoration.repository.get(restoration.parent_id).journal.get("runtime", {}).get("restoration")


def test_external_edit_during_terminal_publication_downgrades_current_counts_without_overwrite(
    restoration, monkeypatch
):
    row = restoration.enqueue_restore()

    def crash(*args, **kwargs):
        raise Crash("terminal publication pending")

    with monkeypatch.context() as patches:
        patches.setattr(restoration.repository, "finish", crash)
        with pytest.raises(Crash):
            restoration.run_restore(row)
    restoration.world.playlists["target"].update(track_uris=[uri(88)], snapshot_id="external")
    expire_lease(restoration.repository, row.id)
    finished, completed = restoration.run_restore(row)
    result = completed.journal["result"]
    assert finished and completed.state == "needs_attention"
    assert result["changed_position_count"] is None and result["restored_resource_count"] is None
    assert result["parent_resolution"] == "complete"
    assert all("current_track_uris" not in resource for resource in result["resources"])
    assert restoration.world.playlists["target"]["track_uris"] == [uri(88)]
    assert len(restoration.world.writes) == 1 and ActivityLog.query.count() == 1
