"""Closed private commands for cleanup after a separately saved Workshop target.

The surrounding operation payload owns the exact integer version-1 envelope.
User identity stays on its JobExecution row; captured associations and selected
inbox identities grant no authority beyond that authenticated owner.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from shuffify.schemas.workshop_finalize_requests import PlaylistID, TrackURI

RowID = Annotated[StrictInt, Field(gt=0)]


class CapturedFinalizerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class FinalizerSourceBinding(CapturedFinalizerModel):
    id: RowID
    playlist_id: PlaylistID


class FinalizerPendingRow(CapturedFinalizerModel):
    id: RowID
    track_uri: TrackURI
    status: Literal["pending", "promoted"]


class FinalizerCommand(CapturedFinalizerModel):
    version: ClassVar[int] = 1
    kind: ClassVar[str]
    playlist_id: PlaylistID
    source_playlist_id: PlaylistID | None
    source_binding: FinalizerSourceBinding | None
    target_track_uris: list[TrackURI]

    @property
    def linked_source_playlist_id(self):
        return self.source_binding.playlist_id if self.source_binding is not None else None

    @property
    def reserved_targets(self):
        return sorted({self.playlist_id, *([self.linked_source_playlist_id] if self.source_binding else [])})

    @model_validator(mode="after")
    def validate_binding(self):
        if self.source_playlist_id != self.linked_source_playlist_id:
            raise ValueError("The captured source differs from its owned association")
        if self.source_playlist_id == self.playlist_id:
            raise ValueError("The source must be distinct from the saved target")
        return self


class FinalizeRaidsCommand(FinalizerCommand):
    kind = "workshop_finalize_raids"
    pending_rows: Annotated[list[FinalizerPendingRow], Field(min_length=1)]

    @property
    def track_ids(self):
        return [row.id for row in self.pending_rows]

    @model_validator(mode="after")
    def validate_selection(self):
        if self.track_ids != sorted(set(self.track_ids)):
            raise ValueError("Captured inbox IDs must be unique and in canonical order")
        return self


class ArchiveCommand(FinalizerCommand):
    source_playlist_id: PlaylistID
    source_binding: FinalizerSourceBinding
    track_uris: Annotated[list[TrackURI], Field(min_length=1)]


class ArchiveAppendCommand(ArchiveCommand):
    kind = "workshop_archive_append"
    baseline_track_uris: list[TrackURI]


class FinalizeRestoreCommand(ArchiveCommand):
    kind = "workshop_finalize_restore"
