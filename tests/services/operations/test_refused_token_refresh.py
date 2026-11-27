"""A queued run whose token refresh the database refuses is deferred, never recorded failed.

The asset, restore and dismissal commands settle their own read failures. A
refused refresh still reaches the worker, which defers the run, and the next
run completes the command.
"""

import pytest
from sqlalchemy.exc import OperationalError

from shuffify.models.db import PendingRaidTrack
from shuffify.services.operations.commands.assets import HANDLERS as ASSET_HANDLERS
from shuffify.services.operations.commands.dismissal import pending_dismiss
from shuffify.spotify.exceptions import SpotifyRefreshUnavailable
from shuffify.worker import OperationWorker
from tests.services.operations.test_asset_commands import asset_operation as asset_operation
from tests.services.operations.test_dismiss_commands import inbox as inbox
from tests.services.operations.test_read_deferral_budget import make_due
from tests.services.operations.test_restore_command import restoration as restoration
from tests.services.operations.test_routine_commands import routine as routine


def refused_refresh(_user):
    """The worker's client, whose first refresh the database refuses as the refresher does at a lock timeout."""
    try:
        raise OperationalError("SELECT ... FOR NO KEY UPDATE", {}, Exception("lock timeout"))
    except OperationalError as error:
        raise SpotifyRefreshUnavailable("The stored refresh token could not be read.", retry_after=10) from error


def assert_deferred(repository, operation_id):
    pending = repository.get(operation_id)
    assert pending.state == "running" and pending.completed_at is None
    assert pending.journal["read_retry"]["next_attempt_at"]


@pytest.mark.parametrize("kind", ["pair_create", "raid_link_create", "setup_playlist_create"])
def test_an_asset_run_whose_refresh_is_refused_is_deferred_then_completes(db_app, asset_operation, kind):
    case = asset_operation
    row = case.enqueue(kind)

    def worker(api_factory):
        return OperationWorker(
            db_app, repository=case.repository, handlers=ASSET_HANDLERS, api_factory=api_factory,
            heartbeat_interval=0,
        )

    assert worker(refused_refresh).run_once(row.id) is False
    assert_deferred(case.repository, row.id)
    assert case.world.writes == []

    make_due(case, row.id)
    assert worker(case.api_factory).run_once(row.id) is True
    assert case.repository.get(row.id).state == "succeeded"


def test_a_dismissal_whose_refresh_is_refused_is_deferred_then_cleans_up(inbox):
    row = inbox.enqueue_dismiss()

    def worker(api_factory):
        return OperationWorker(
            inbox.app, repository=inbox.repository, handlers={"pending_dismiss": pending_dismiss},
            api_factory=api_factory, heartbeat_interval=0,
        )

    assert worker(refused_refresh).run_once(row.id) is False
    assert_deferred(inbox.repository, row.id)
    assert PendingRaidTrack.query.filter_by(status="dismissed").count() == 2
    assert inbox.world.writes == []

    make_due(inbox, row.id)
    assert worker(inbox.factory).run_once(row.id) is True
    completed = inbox.repository.get(row.id)
    assert completed.state == "succeeded"
    assert completed.journal["result"]["source_status"] == "complete"


def test_a_restore_whose_refresh_is_refused_is_deferred_then_restores(restoration):
    row = restoration.enqueue_restore()

    finished, _ = restoration.run_restore(row, refused_refresh)
    assert finished is False
    assert_deferred(restoration.repository, row.id)
    assert restoration.world.writes == []

    make_due(restoration, row.id)
    finished, completed = restoration.run_restore(row)
    assert finished and completed.state == "succeeded"
