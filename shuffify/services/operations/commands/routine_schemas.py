"""Closed server-captured configuration for routine and explicit executor commands.

These are private queue documents, never HTTP request schemas. Raw saved JSON
stays raw so admission cannot turn an executor's empty/gated/error path into a
different action. Reserved targets are derived, never trusted from a payload.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictInt, model_validator

from shuffify.services.live_review_scope import JOB_STEPS

from .schemas import ReviewedCommand

Identifier = Annotated[str, Field(min_length=1)]
RowID = Annotated[StrictInt, Field(gt=0)]
JobKind = Literal["raid", "shuffle", "raid_and_shuffle", "raid_and_drip", "rotate", "drip"]


class CapturedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class RoutineDefinition(CapturedModel):
    schedule_id: RowID | None
    job_type: JobKind
    target_playlist_id: Identifier
    target_playlist_name: str | None
    algorithm_name: str | None
    algorithm_params: JsonValue
    source_playlist_ids: JsonValue


class CapturedLink(CapturedModel):
    id: RowID
    raid_playlist_id: Identifier
    raid_playlist_name: str | None
    drip_count: StrictInt
    drip_enabled: StrictBool


class CapturedPair(CapturedModel):
    id: RowID
    archive_playlist_id: Identifier
    archive_playlist_name: str | None


class CapturedSource(CapturedModel):
    id: RowID
    source_type: Literal["own", "external", "search_query"]
    source_playlist_id: str | None
    source_name: str | None
    search_query: str | None
    raid_count: StrictInt
    source_url: str | None


class CapturedLock(CapturedModel):
    position: StrictInt
    track_uri: Identifier


class RoutineConfig(CapturedModel):
    link: CapturedLink | None
    pair: CapturedPair | None
    sources: list[CapturedSource]
    locks: list[CapturedLock]


class RoutineCommand(ReviewedCommand):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    kind: ClassVar[str]
    definition: RoutineDefinition
    config: RoutineConfig

    @property
    def steps(self):
        return JOB_STEPS[self.definition.job_type]

    def targets_for(self, steps=None):
        selected = self.steps if steps is None else tuple(steps)
        if any(step not in self.steps for step in selected):
            raise ValueError("A configuration comparison includes an undeclared step")
        targets = {self.playlist_id}
        if any(step in selected for step in ("raid", "drip")) and self.config.link is not None:
            targets.add(self.config.link.raid_playlist_id)
        if any(step in selected for step in ("raid", "rotate")) and self.config.pair is not None:
            targets.add(self.config.pair.archive_playlist_id)
        return sorted(targets)

    @property
    def reserved_targets(self):
        return self.targets_for()

    @model_validator(mode="after")
    def validate_targets(self):
        if self.playlist_id != self.definition.target_playlist_id:
            raise ValueError("Definition target differs from command target")
        if any(step in self.steps for step in ("raid", "drip")) and self.config.link is not None:
            if self.config.link.raid_playlist_id == self.playlist_id:
                raise ValueError("The collection must be distinct from the target")
        if any(step in self.steps for step in ("raid", "rotate")) and self.config.pair is not None:
            if self.config.pair.archive_playlist_id == self.playlist_id:
                raise ValueError("The archive must be distinct from the target")
        return self


class ScheduleRunCommand(RoutineCommand):
    kind = "schedule_run"
    review_kind = "schedule"
    triggered_by: Literal["manual", "scheduler"] = "manual"

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.definition.schedule_id is None:
            raise ValueError("A saved routine requires its schedule identity")
        return self


class DirectRoutineCommand(RoutineCommand):
    triggered_by: Literal["manual"] = "manual"

    @model_validator(mode="after")
    def validate_direct_definition(self):
        if self.definition.schedule_id is not None or self.definition.job_type != self.review_kind:
            raise ValueError("Explicit executor definition differs from its command kind")
        extras = self.definition.source_playlist_ids
        if extras is not None and (not isinstance(extras, list) or any(not isinstance(item, str) for item in extras)):
            raise ValueError("Explicit source IDs must be a string list or null")
        params = {"force": True} if "drip" in self.steps else None
        if self.definition.algorithm_name is not None or self.definition.algorithm_params != params:
            raise ValueError("Explicit executor parameters differ from the requested action")
        return self


class RaidNowCommand(DirectRoutineCommand):
    kind = "raid_now"
    review_kind = "raid"


class DripNowCommand(DirectRoutineCommand):
    kind = "drip_now"
    review_kind = "drip"


class RaidAndDripCommand(DirectRoutineCommand):
    kind = "raid_and_drip"
    review_kind = "raid_and_drip"
