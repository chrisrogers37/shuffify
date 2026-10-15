"""Closed exact-row intent and observations for local-first inbox dismissal."""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from shuffify.schemas.workshop_finalize_requests import PlaylistID, TrackURI

RowID = Annotated[StrictInt, Field(gt=0)]


class DismissModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class DismissSourceBinding(DismissModel):
    id: RowID
    playlist_id: PlaylistID


class DismissObservedRow(DismissModel):
    id: RowID
    track_uri: TrackURI
    status: Literal["pending", "dismissed"]


class DismissPendingRow(DismissObservedRow):
    status: Literal["pending"]


class DismissObservation(DismissModel):
    playlist_id: PlaylistID
    selection: Literal["selected", "all"]
    pending_rows: tuple[DismissObservedRow, ...]
    source_binding: DismissSourceBinding | None

    @field_validator("pending_rows", mode="before")
    @classmethod
    def frozen_rows(cls, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("Captured pending rows must be a JSON array")
        return tuple(value)

    @property
    def track_ids(self):
        return [row.id for row in self.pending_rows]

    @property
    def reserved_targets(self):
        return sorted({self.playlist_id, *([self.source_binding.playlist_id] if self.source_binding else [])})

    @model_validator(mode="after")
    def canonical_selection(self):
        if self.track_ids != sorted(set(self.track_ids)):
            raise ValueError("Captured pending IDs must be unique and in canonical order")
        if self.source_binding is not None and self.source_binding.playlist_id == self.playlist_id:
            raise ValueError("The collection must differ from the target")
        return self


class PendingDismissCommand(DismissObservation):
    kind: ClassVar[str] = "pending_dismiss"
    version: ClassVar[int] = 1
    pending_rows: tuple[DismissPendingRow, ...]
