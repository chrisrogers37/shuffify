"""Reject mismatched private routine identity before any authenticated resource is used."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from shuffify.services.operations.commands.envelope import validate_command
from shuffify.services.operations.commands.routine_schemas import ScheduleRunCommand


def runtime_for(command=None):
    command = command or {
        "playlist_id": "target", "triggered_by": "manual", "reviewed": None,
        "definition": {"schedule_id": 1, "job_type": "rotate", "target_playlist_id": "target",
                       "target_playlist_name": "Example", "algorithm_name": None, "algorithm_params": {},
                       "source_playlist_ids": []},
        "config": {"link": None, "sources": [], "locks": [],
                   "pair": {"id": 2, "archive_playlist_id": "archive", "archive_playlist_name": "Archive"}},
    }
    return SimpleNamespace(
        operation=SimpleNamespace(payload={"version": 1, "command": deepcopy(command)},
                                  targets=["archive", "target"], kind="schedule_run", schedule_id=1,
                                  scheduled_for=None),
        lease=SimpleNamespace(targets=("archive", "target")),
    )


@pytest.mark.parametrize("tamper", [
    "version", "row_targets", "lease_targets", "kind", "schedule_id", "origin", "payload_targets", "payload_owner",
])
def test_invalid_routine_identity_cannot_reach_authenticated_resources(tamper):
    runtime = runtime_for()
    if tamper == "version":
        runtime.operation.payload["version"] = True
    elif tamper == "row_targets":
        runtime.operation.targets = ["target"]
    elif tamper == "lease_targets":
        runtime.lease.targets = ("target",)
    elif tamper == "kind":
        runtime.operation.kind = "raid_now"
    elif tamper == "schedule_id":
        runtime.operation.schedule_id = 9
    elif tamper == "origin":
        runtime.operation.scheduled_for = datetime.now(timezone.utc)
    elif tamper == "payload_targets":
        runtime.operation.payload["command"]["reserved_targets"] = ["other"]
    else:
        runtime.operation.payload["command"]["user_id"] = 999
    with pytest.raises((ValueError, ValidationError)):
        validate_command(runtime, ScheduleRunCommand)


def test_scheduler_identity_uses_the_server_owned_fire_key():
    runtime = runtime_for()
    runtime.operation.payload["command"]["triggered_by"] = "scheduler"
    with pytest.raises(ValueError):
        validate_command(runtime, ScheduleRunCommand)
    runtime.operation.scheduled_for = datetime.now(timezone.utc)
    assert validate_command(runtime, ScheduleRunCommand).reserved_targets == ["archive", "target"]
