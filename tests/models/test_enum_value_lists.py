"""Each value list written out beside an enum lists exactly that enum's values.

A CHECK constraint stays literal SQL: a database keeps the constraint it was
created with, so a changed list also takes a migration that replaces the
constraint. ``JobKind`` stays a ``Literal`` for type checkers, and a web union
is read through the schemas generated from ``web/src/api/contracts.ts``.

The copies of the snapshot cap, in the settings request, its CHECK constraint
and the web schemas, are checked against ``MAX_SNAPSHOTS_LIMIT`` the same way.
"""

import json
import re
from pathlib import Path
from typing import get_args

import pytest
import sqlalchemy as sa

from shuffify.enums import JobType, PendingRaidStatus, ScheduleType, SnapshotType
from shuffify.models.db import db
from shuffify.services.operations.commands.routine_schemas import JobKind
from shuffify.services.user_settings_service import MAX_SNAPSHOTS_LIMIT

SCHEMAS = Path(__file__).resolve().parents[2] / "web" / "schemas"
# Each CHECK constraint that lists an enum's values, by constraint name.
CHECKS = {
    "ck_schedules_job_type": JobType,
    "ck_schedules_schedule_type": ScheduleType,
    "ck_snapshot_type": SnapshotType,
    "ck_pending_raid_status": PendingRaidStatus,
}
# Each web union that lists an enum's values, by its definition name in web/schemas.
UNIONS = {"JobType": JobType}


@pytest.mark.parametrize(("name", "enum"), CHECKS.items())
def test_check_constraint_lists_its_enum(name, enum):
    texts = {
        constraint.name: str(constraint.sqltext)
        for table in db.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    values = re.findall(r"'([^']*)'", texts[name])
    assert set(values) == set(enum), f"Change {name}'s text and add a migration that replaces the constraint"


def test_job_kind_lists_job_type():
    assert set(get_args(JobKind)) == set(JobType)


@pytest.mark.parametrize(("name", "enum"), UNIONS.items())
def test_web_union_lists_its_enum(name, enum):
    copies = {}
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        definition = json.loads(path.read_text()).get("definitions", {}).get(name)
        if definition is not None:
            copies[path.name] = set(definition["enum"])
    assert copies, f"No schema under web/schemas defines {name}"
    for path, values in copies.items():
        assert values == set(enum), path


def test_each_copy_of_the_snapshot_cap_is_the_cap():
    # Imported here: importing shuffify.schemas ahead of shuffify.services meets an import cycle.
    from shuffify.schemas.settings_requests import UserSettingsUpdateRequest

    field = UserSettingsUpdateRequest.model_fields["max_snapshots_per_playlist"]
    assert [rule.le for rule in field.metadata if hasattr(rule, "le")] == [MAX_SNAPSHOTS_LIMIT]
    check = next(
        str(constraint.sqltext)
        for constraint in db.metadata.tables["user_settings"].constraints
        if constraint.name == "ck_user_settings_max_snapshots"
    )
    assert re.findall(r"<=\s*(\d+)", check) == [str(MAX_SNAPSHOTS_LIMIT)], (
        "Change ck_user_settings_max_snapshots's text and add a migration that replaces the constraint"
    )
    maxima = {}
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        definition = json.loads(path.read_text()).get("definitions", {}).get("SnapshotLimit")
        if definition is not None:
            maxima[path.name] = definition["maximum"]
    assert maxima, "No schema under web/schemas defines SnapshotLimit"
    for path, maximum in maxima.items():
        assert maximum == MAX_SNAPSHOTS_LIMIT, path
