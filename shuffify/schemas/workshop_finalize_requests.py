"""Request schemas for finalizing Workshop source tasks (raid, restore, archive).

Each request names the exact tracks it acts on; no endpoint expands the
current inbox.
"""

from typing import Annotated

from pydantic import BaseModel, Field, StrictBool, StringConstraints

from shuffify.schemas.requests import TRACK_URI_RE

TrackURI = Annotated[str, StringConstraints(strict=True, pattern=TRACK_URI_RE.pattern)]
PlaylistID = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9]+$"
    ),
]


class SourceFinalizeRequest(BaseModel):
    source_playlist_id: PlaylistID
    target_track_uris: list[TrackURI]
    inspect_only: StrictBool = False


class FinalizeRaidRequest(SourceFinalizeRequest):
    source_playlist_id: PlaylistID | None
    track_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(min_length=1)


class FinalizeRestoreRequest(SourceFinalizeRequest):
    track_uris: list[TrackURI] = Field(min_length=1)


class FinalizeArchiveRequest(FinalizeRestoreRequest):
    baseline_track_uris: list[TrackURI]
