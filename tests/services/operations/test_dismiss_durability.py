"""DB-first dismissal receipts survive cleanup, publication and process boundaries."""

import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, PendingRaidTrack, RaidPlaylistLink, db
from shuffify.services.operations.commands.dismissal import pending_dismiss
from shuffify.services.operations.journal import OperationDeferred
from shuffify.services.operations.repository import LeaseLost
from shuffify.services.playlist_lock import playlist_configuration_lock
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import Crash, uri
from tests.services.operations.test_dismiss_commands import inbox as inbox
from tests.services.operations.test_routine_commands import routine as routine


def states(inbox):
    with Session(inbox.repository.engine) as storage:
        return list(storage.execute(select(PendingRaidTrack.status).order_by(PendingRaidTrack.id)).scalars())


def test_required_receipt_failure_never_authenticates_or_claims_dismissal(inbox, monkeypatch):
    original = inbox.repository.local_step

    def fail(lease, name, callback, **kwargs):
        if name == "accept.dismiss":
            raise RuntimeError("private storage failure")
        return original(lease, name, callback, **kwargs)

    monkeypatch.setattr(inbox.repository, "local_step", fail)
    completed = inbox.run_dismiss(inbox.enqueue_dismiss())
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert result["dismissed_count"] is None
    assert result["database_status"] == "unknown"
    assert result["source_status"] == "not_started"
    assert states(inbox) == ["pending", "pending"]
    assert inbox.factories == [] and inbox.world.writes == []
    assert ActivityLog.query.count() == 0


def test_lost_atomic_dismissal_reply_uses_matching_receipt_without_repeating_local_or_source_effects(
    inbox, monkeypatch
):
    original = inbox.repository.local_step

    def lost(lease, name, callback, **kwargs):
        result = original(lease, name, callback, **kwargs)
        if name == "accept.dismiss":
            raise RuntimeError("commit acknowledgement lost")
        return result

    monkeypatch.setattr(inbox.repository, "local_step", lost)
    completed = inbox.run_dismiss(inbox.enqueue_dismiss())
    assert completed.state == "succeeded"
    assert completed.journal["result"]["dismissed_count"] == 2
    assert states(inbox) == ["dismissed", "dismissed"]
    assert ActivityLog.query.count() == 1
    assert [method for method, _, _ in inbox.world.writes] == ["DELETE"]


def test_optional_activity_failure_does_not_undo_the_required_dismissal(inbox, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private optional activity failure")

    monkeypatch.setattr("shuffify.services.operations.bookkeeping.OperationBookkeeping.activity", fail)
    completed = inbox.run_dismiss(inbox.enqueue_dismiss())
    assert completed.state == "succeeded"
    assert completed.journal["result"]["dismissed_count"] == 2
    assert states(inbox) == ["dismissed", "dismissed"]
    assert ActivityLog.query.count() == 0
    assert completed.journal["runtime"]["local_steps"]["accept.dismiss"]["activity"]["status"] == "failed"


def test_crash_after_local_acceptance_resumes_only_the_cleanup_after_actual_lease_expiry(inbox, monkeypatch):
    inbox.repository.lease_seconds = 1
    original = inbox.repository.local_step
    row = inbox.enqueue_dismiss()

    def crash(lease, name, callback, **kwargs):
        result = original(lease, name, callback, **kwargs)
        if name == "accept.dismiss":
            raise Crash("after dismissal acceptance")
        return result

    with monkeypatch.context() as patches:
        patches.setattr(inbox.repository, "local_step", crash)
        with pytest.raises(Crash):
            inbox.run_dismiss(row)
    assert states(inbox) == ["dismissed", "dismissed"]
    assert inbox.factories == [] and inbox.world.writes == []
    time.sleep(1.05)
    completed = inbox.run_dismiss(row)
    assert completed.state == "succeeded"
    assert completed.journal["result"]["dismissed_count"] == 2
    assert ActivityLog.query.count() == 1
    assert len(inbox.world.writes) == 1


def test_partial_cleanup_recovery_keeps_dismissed_rows_and_never_replays_the_acknowledged_batch(inbox, monkeypatch):
    inbox.repository.lease_seconds = 1
    values = [uri(value) for value in range(1000, 1101)]
    with Session(db.engine) as storage:
        storage.add_all(
            [
                PendingRaidTrack(
                    user_id=inbox.user.id,
                    target_playlist_id="target",
                    track_uri=value,
                    track_name="Example",
                    status="pending",
                )
                for value in values
            ]
        )
        storage.commit()
    inbox.world.playlists["collection"]["track_uris"] = values
    row = inbox.enqueue_dismiss(all_pending=True)
    original = inbox.repository.checkpoint
    cut = []

    def factory(user):
        api = inbox.factory(user)
        original_items = api.get_playlist_items_raw

        def items(resource, limit=100, offset=0):
            page = original_items(resource, limit=limit, offset=offset)
            page["next"] = (
                f"https://api.spotify.com/v1/playlists/{resource}/items?offset={offset + len(page['items'])}"
                if offset + len(page["items"]) < page["total"]
                else None
            )
            return page

        api.get_playlist_items_raw = items
        return api

    def checkpoint(lease, document, **kwargs):
        result = original(lease, document, **kwargs)
        batches = [batch for plan in document.get("plans", []) for batch in plan["batches"]]
        if not cut and batches and batches[0]["state"] == "acknowledged":
            cut.append(True)
            raise Crash("after first source batch ACK")
        return result

    with monkeypatch.context() as patches:
        patches.setattr(inbox.repository, "checkpoint", checkpoint)
        with pytest.raises(Crash):
            inbox.run_dismiss(row, factory)
    assert len(inbox.world.writes) == 1
    time.sleep(1.05)
    completed = inbox.run_dismiss(row, factory)
    assert completed.state == "succeeded"
    assert completed.journal["result"]["dismissed_count"] == 103
    assert completed.journal["result"]["source_removed_count"] == 101
    assert [batch["attempts"] for batch in completed.journal["plans"][0]["batches"]] == [1, 1]
    assert len(inbox.world.writes) == 2
    assert inbox.world.playlists["collection"]["track_uris"] == []
    assert ActivityLog.query.count() == 1


def test_fresh_source_conflict_after_ack_retains_db_receipt_and_never_overwrites(inbox, monkeypatch):
    inbox.repository.lease_seconds = 1
    original = inbox.repository.checkpoint
    row = inbox.enqueue_dismiss()

    def checkpoint(lease, document, **kwargs):
        result = original(lease, document, **kwargs)
        if any(batch["state"] == "acknowledged" for plan in document.get("plans", []) for batch in plan["batches"]):
            raise Crash("after source ACK")
        return result

    with monkeypatch.context() as patches:
        patches.setattr(inbox.repository, "checkpoint", checkpoint)
        with pytest.raises(Crash):
            inbox.run_dismiss(row)
    inbox.world.playlists["collection"].update(track_uris=[uri(99)], snapshot_id="external")
    before = deepcopy(inbox.world.playlists)
    time.sleep(1.05)
    completed = inbox.run_dismiss(row)
    result = completed.journal["result"]
    assert completed.state == "needs_attention"
    assert result["dismissed_count"] == 2 and result["database_status"] == "complete"
    assert result["source_status"] == "unknown" and result["source_removed_count"] is None
    assert result["source_playlist_id"] == "collection" and result["playlist_id"] == "target"
    assert inbox.world.playlists == before and len(inbox.world.writes) == 1
    assert states(inbox) == ["dismissed", "dismissed"]
    assert ActivityLog.query.count() == 1


@pytest.mark.parametrize(
    "control", [LeaseLost("lost"), OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=30))]
)
def test_controls_after_dismissal_never_compensate_or_redismiss(inbox, monkeypatch, control):
    def factory(_user):
        raise control

    row = inbox.enqueue_dismiss()
    worker = OperationWorker(
        inbox.app,
        repository=inbox.repository,
        handlers={"pending_dismiss": pending_dismiss},
        api_factory=factory,
        heartbeat_interval=0,
    )
    assert worker.run_once(row.id) is False
    current = inbox.repository.get(row.id)
    assert current.state == "running"
    assert states(inbox) == ["dismissed", "dismissed"]
    assert ActivityLog.query.count() == 1
    assert inbox.world.writes == []
    assert [item["name"] for item in current.journal["boundaries"]["accepted"]] == ["dismiss"]


def test_configuration_lock_defers_the_original_id_before_db_or_auth(inbox, monkeypatch):
    monkeypatch.setattr("shuffify.services.operations.commands.dismissal.CONFIG_LOCK_WAIT_S", 0.03, raising=False)
    acquired, release = threading.Event(), threading.Event()

    def hold():
        with playlist_configuration_lock("target", timeout_s=1) as locked:
            assert locked
            acquired.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    assert acquired.wait(1)
    row = inbox.enqueue_dismiss()
    try:
        worker = OperationWorker(
            inbox.app,
            repository=inbox.repository,
            handlers={"pending_dismiss": pending_dismiss},
            api_factory=inbox.factory,
            heartbeat_interval=0,
        )
        worker.run_once(row.id)
        current = inbox.repository.get(row.id)
        assert current.state in {"queued", "running"} and current.lease_owner is None
        assert states(inbox) == ["pending", "pending"]
        assert inbox.factories == [] and inbox.world.writes == []
    finally:
        release.set()
        thread.join(1)
    time.sleep(1.05)
    completed = inbox.run_dismiss(row)
    assert completed.state == "succeeded" and completed.id == row.id
    assert completed.journal["result"]["dismissed_count"] == 2
    assert ActivityLog.query.count() == 1


def test_relinked_source_after_local_acceptance_is_never_followed(inbox, monkeypatch):
    original = inbox.repository.local_step

    def relink(lease, name, callback, **kwargs):
        result = original(lease, name, callback, **kwargs)
        if name == "accept.dismiss":
            with Session(inbox.repository.engine) as storage:
                storage.execute(
                    update(RaidPlaylistLink)
                    .where(RaidPlaylistLink.user_id == inbox.user.id)
                    .values(raid_playlist_id="replacement")
                )
                storage.commit()
        return result

    monkeypatch.setattr(inbox.repository, "local_step", relink)
    completed = inbox.run_dismiss(inbox.enqueue_dismiss())
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "source_changed"
    assert completed.journal["result"]["dismissed_count"] == 2
    assert states(inbox) == ["dismissed", "dismissed"]
    assert inbox.factories == [] and inbox.world.writes == []


@pytest.mark.parametrize("lost_reply", [False, True])
def test_cleanup_acceptance_outage_preserves_verified_removal_and_dismissed_rows(inbox, monkeypatch, lost_reply):
    original = inbox.repository.local_step

    def fail(lease, name, callback, **kwargs):
        if name != "accept.dismiss_cleanup":
            return original(lease, name, callback, **kwargs)
        if lost_reply:
            original(lease, name, callback, **kwargs)
        raise RuntimeError("private publication failure")

    monkeypatch.setattr(inbox.repository, "local_step", fail)
    completed = inbox.run_dismiss(inbox.enqueue_dismiss())
    result = completed.journal["result"]
    assert completed.state == ("succeeded" if lost_reply else "needs_attention")
    assert result["dismissed_count"] == 2 and result["database_status"] == "complete"
    assert result["source_status"] == "complete" and result["source_removed_count"] == 2
    assert states(inbox) == ["dismissed", "dismissed"]
    assert inbox.world.playlists["collection"]["track_uris"] == []
    assert [method for method, _, _ in inbox.world.writes] == ["DELETE"]
    assert ActivityLog.query.count() == 1
    assert not completed.journal.get("compensation")


def test_applied_cleanup_response_lost_is_reconciled_without_a_second_delete(inbox, monkeypatch):
    inbox.repository.lease_seconds = 1
    row = inbox.enqueue_dismiss()
    original = inbox.world.dispatch

    def lost(method, path, body):
        result = original(method, path, body)
        if method == "DELETE":
            raise Crash("source applied response lost")
        return result

    with monkeypatch.context() as patches:
        patches.setattr(inbox.world, "dispatch", lost)
        with pytest.raises(Crash):
            inbox.run_dismiss(row)
    time.sleep(1.05)
    completed = inbox.run_dismiss(row)
    assert completed.state == "succeeded"
    assert completed.journal["result"]["source_removed_count"] == 2
    assert len(inbox.world.writes) == 1 and ActivityLog.query.count() == 1
    assert states(inbox) == ["dismissed", "dismissed"]


def test_empty_dismissal_preserves_zero_count_activity_without_provider_access(inbox):
    completed = inbox.run_dismiss(inbox.enqueue_dismiss(ids=[9999]))
    assert completed.state == "succeeded"
    assert completed.journal["result"]["dismissed_count"] == 0
    assert ActivityLog.query.count() == 1
    assert states(inbox) == ["pending", "pending"]
    assert inbox.factories == [] and inbox.world.writes == []


def test_lost_db_acceptance_reply_and_receipt_read_outage_never_authenticate_until_recovery(inbox, monkeypatch):
    inbox.repository.lease_seconds = 1
    original_step, original_get = inbox.repository.local_step, inbox.repository.get
    unavailable = []
    row = inbox.enqueue_dismiss()

    def lost(lease, name, callback, **kwargs):
        result = original_step(lease, name, callback, **kwargs)
        if name == "accept.dismiss":
            unavailable.append(True)
            raise RuntimeError("commit reply lost")
        return result

    def get(operation_id):
        if unavailable:
            raise RuntimeError("private receipt read outage")
        return original_get(operation_id)

    with monkeypatch.context() as patches:
        patches.setattr(inbox.repository, "local_step", lost)
        patches.setattr(inbox.repository, "get", get)
        worker = OperationWorker(
            inbox.app,
            repository=inbox.repository,
            handlers={"pending_dismiss": pending_dismiss},
            api_factory=inbox.factory,
            heartbeat_interval=0,
        )
        assert worker.run_once(row.id) is False
    assert states(inbox) == ["dismissed", "dismissed"]
    assert inbox.factories == [] and inbox.world.writes == []
    assert ActivityLog.query.count() == 1
    time.sleep(1.05)
    completed = inbox.run_dismiss(row)
    assert completed.state == "succeeded"
    assert completed.journal["result"]["dismissed_count"] == 2
    assert ActivityLog.query.count() == 1 and len(inbox.world.writes) == 1


@pytest.mark.parametrize("external_edit", [False, True])
def test_terminal_reply_loss_revalidates_source_without_repeating_dismissal(inbox, monkeypatch, external_edit):
    inbox.repository.lease_seconds = 1
    row = inbox.enqueue_dismiss()

    def crash(*args, **kwargs):
        raise Crash("terminal database publication reply lost")

    with monkeypatch.context() as patches:
        patches.setattr(inbox.repository, "finish", crash)
        with pytest.raises(Crash):
            inbox.run_dismiss(row)
    if external_edit:
        inbox.world.playlists["collection"].update(track_uris=[uri(99)], snapshot_id="external")
    before = deepcopy(inbox.world.playlists)
    time.sleep(1.05)
    completed = inbox.run_dismiss(row)
    result = completed.journal["result"]
    assert completed.state == ("needs_attention" if external_edit else "succeeded")
    assert result["dismissed_count"] == 2 and result["database_status"] == "complete"
    assert result["source_status"] == ("unknown" if external_edit else "complete")
    assert result["source_removed_count"] == (None if external_edit else 2)
    assert inbox.world.playlists == before and len(inbox.world.writes) == 1
    assert ActivityLog.query.count() == 1 and states(inbox) == ["dismissed", "dismissed"]


def test_real_http_retry_after_defers_same_accepted_dismissal_without_duplicate_activity(inbox):
    import requests

    attempts = []

    def factory(user):
        api = inbox.factory(user)
        send = api._http._session.request

        def limited(method, url, **kwargs):
            if method == "DELETE":
                attempts.append(True)
                if len(attempts) == 1:
                    response = requests.Response()
                    response.status_code = 429
                    response.headers["Retry-After"] = "1"
                    response._content = b'{"error":{"message":"try later"}}'
                    return response
            return send(method, url, **kwargs)

        api._http._session.request = limited
        return api

    row = inbox.enqueue_dismiss()
    worker = OperationWorker(
        inbox.app,
        repository=inbox.repository,
        handlers={"pending_dismiss": pending_dismiss},
        api_factory=factory,
        heartbeat_interval=0,
    )
    assert worker.run_once(row.id) is False
    current = inbox.repository.get(row.id)
    assert current.state == "running" and current.lease_owner is None
    assert states(inbox) == ["dismissed", "dismissed"]
    assert ActivityLog.query.count() == 1 and inbox.world.writes == []
    assert current.journal["plans"][0]["batches"][0]["next_attempt_at"]
    time.sleep(1.05)
    completed = inbox.run_dismiss(row, factory)
    assert completed.id == row.id and completed.state == "succeeded"
    assert completed.journal["result"]["dismissed_count"] == 2
    assert len(inbox.world.writes) == 1 and len(attempts) == 2
    assert ActivityLog.query.count() == 1


def test_changed_pending_identity_refuses_before_db_receipt_or_source_auth(inbox):
    row = inbox.enqueue_dismiss()
    with Session(inbox.repository.engine) as storage, storage.begin():
        storage.execute(
            update(PendingRaidTrack).where(PendingRaidTrack.id == inbox.pending_ids[0]).values(track_uri=uri(88))
        )
    completed = inbox.run_dismiss(row)
    result = completed.journal["result"]
    assert completed.state == "failed" and result["error_code"] == "selection_changed"
    assert result["dismissed_count"] is None and result["database_status"] != "complete"
    assert result["source_status"] == "not_started"
    assert result["http_status"] == 409
    assert "selected inbox tracks changed" in result["message"]
    assert states(inbox) == ["pending", "pending"]
    assert inbox.factories == [] and inbox.world.writes == []
    assert ActivityLog.query.count() == 0


def test_source_removal_count_reports_physical_occurrences_independently_of_db_rows(inbox):
    inbox.world.playlists["collection"]["track_uris"] = [uri(4), uri(4), uri(4), uri(5)]
    completed = inbox.run_dismiss(inbox.enqueue_dismiss())
    result = completed.journal["result"]
    assert completed.state == "succeeded"
    assert result["dismissed_count"] == 2 and result["source_removed_count"] == 4
    assert inbox.world.playlists["collection"]["track_uris"] == []
    assert len(inbox.world.writes) == 1


def test_rejected_later_cleanup_batch_restores_only_source_and_keeps_accepted_db_receipt(inbox):
    import requests

    values = [uri(value) for value in range(1000, 1101)]
    with Session(inbox.repository.engine) as storage, storage.begin():
        storage.add_all(
            [
                PendingRaidTrack(
                    user_id=inbox.user.id,
                    target_playlist_id="target",
                    track_uri=value,
                    track_name="Example",
                    status="pending",
                )
                for value in values
            ]
        )
    inbox.world.playlists["collection"]["track_uris"] = values
    target = deepcopy(inbox.world.playlists["target"])
    attempts = []

    def factory(user):
        api = inbox.factory(user)
        read_page, send = api.get_playlist_items_raw, api._http._session.request

        def items(resource, limit=100, offset=0):
            page = read_page(resource, limit=limit, offset=offset)
            end = offset + len(page["items"])
            page["next"] = (
                f"https://api.spotify.com/v1/playlists/{resource}/items?offset={end}" if end < page["total"] else None
            )
            return page

        def request(method, url, **kwargs):
            if method == "DELETE":
                attempts.append(True)
                if len(attempts) == 2:
                    response = requests.Response()
                    response.status_code = 403
                    response._content = b'{"error":{"status":403,"message":"private refusal"}}'
                    return response
            return send(method, url, **kwargs)

        api.get_playlist_items_raw, api._http._session.request = items, request
        return api

    completed = inbox.run_dismiss(inbox.enqueue_dismiss(all_pending=True), factory)
    result = completed.journal["result"]
    assert completed.state == "rolled_back"
    assert result["dismissed_count"] == 103 and result["database_status"] == "complete"
    assert result["source_status"] == "restored" and result["source_removed_count"] == 0
    assert result["source_playlist_id"] == "collection" and result["playlist_id"] == "target"
    assert states(inbox) == ["dismissed"] * 103
    assert inbox.world.playlists["collection"]["track_uris"] == values
    assert inbox.world.playlists["target"] == target
    assert [method for method, _, _ in inbox.world.writes] == ["DELETE", "PUT", "POST"]
    assert ActivityLog.query.count() == 1
    assert "private refusal" not in str(result)
