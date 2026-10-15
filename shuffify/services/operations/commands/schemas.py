"""Private, versioned queue intent after HTTP-specific parsing and ownership checks."""

from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from shuffify.schemas.requests import ShuffleRequest
from shuffify.services.live_review_bindings import ReviewBindingError, json_binding

TrackURI = Annotated[str, Field(pattern=r'^spotify:track:[A-Za-z0-9]{22}$')]


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    playlist_id: Annotated[str, Field(min_length=1)]


class ReviewedCommand(Command):
    review_kind: ClassVar[str]
    reviewed: dict[str, str] | None = None

    @model_validator(mode='after')
    def validate_review(self):
        if self.reviewed is not None:
            try:
                result = json_binding(self.reviewed, self.review_kind)
            except ReviewBindingError as error:
                raise ValueError('Invalid review binding') from error
            if result is None or set(result) != set(self.reviewed):
                raise ValueError('Invalid review binding')
        return self


class ShuffleCommand(ReviewedCommand):
    review_kind = 'shuffle'
    algorithm: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_algorithm(self):
        parsed = ShuffleRequest(algorithm=self.algorithm, **self.params)
        normalized = parsed.get_algorithm_params()
        if not set(self.params) <= set(normalized):
            raise ValueError('Unexpected algorithm parameter')
        self.algorithm, self.params = parsed.algorithm, normalized
        return self


class UndoSelectionPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    states: list[list[TrackURI]]
    current_index: StrictInt

    @model_validator(mode='after')
    def validate_selection(self):
        if not 0 < self.current_index < len(self.states):
            raise ValueError('No previous saved state is selected')
        return self

    @property
    def desired_uris(self):
        return list(self.states[self.current_index - 1])


class UndoCommand(ReviewedCommand):
    review_kind = 'undo'
    selection: UndoSelectionPayload


class VisibilityCommand(ReviewedCommand):
    review_kind = 'privacy'
    public: StrictBool


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Annotated[int, Field(gt=0)]
    user_id: Annotated[int, Field(gt=0)]
    playlist_id: Annotated[str, Field(min_length=1)]
    playlist_name: str | None = None
    track_uris: Annotated[list[TrackURI], Field(min_length=1)]
    track_count: int | None = None
    snapshot_type: str | None = None
    trigger_description: str | None = None
    created_at: str | None = None

    def to_dict(self):
        return self.model_dump()


class SnapshotRestoreCommand(ReviewedCommand):
    review_kind = 'snapshot_restore'
    snapshot: SnapshotPayload

    @model_validator(mode='after')
    def validate_target(self):
        if self.snapshot.playlist_id != self.playlist_id:
            raise ValueError('Snapshot target differs from command target')
        return self


class WorkshopCommitCommand(Command):
    baseline_track_uris: list[TrackURI]
    track_uris: list[TrackURI]
