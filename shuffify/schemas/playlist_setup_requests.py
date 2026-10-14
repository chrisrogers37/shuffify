"""Playlist setup request bodies.

Strict Pydantic v2 models with no extra fields. ``changes`` is a union
discriminated on ``op``; each change names one subject, and a list may change
a subject only once. The server resolves names, orders and validates the
changes against fresh state (``playlist_setup_service``). Routes check that a
body is a JSON object and then call ``model_validate``.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from shuffify.schemas.workshop_finalize_requests import PlaylistID
from shuffify.services.live_review_bindings import DIGEST

MAX_CHANGES = 20
# Inclusive bounds; the setup context's limits report the same values.
RAID_COUNT_RANGE = (1, 100)
DRIP_COUNT_RANGE = (1, 50)

Digest = Annotated[str, StringConstraints(strict=True, pattern=f"^{DIGEST.pattern}$")]
SourceID = Annotated[int, Field(strict=True, gt=0)]
# Matched exactly against a routine's stored source IDs; never sent to Spotify.
StoredPlaylistID = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
SearchQuery = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)]
SourceName = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=255)]
RaidCount = Annotated[int, Field(strict=True, ge=RAID_COUNT_RANGE[0], le=RAID_COUNT_RANGE[1])]
DripCount = Annotated[int, Field(strict=True, ge=DRIP_COUNT_RANGE[0], le=DRIP_COUNT_RANGE[1])]
PlaylistName = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]
_PLAYLIST_ID = TypeAdapter(PlaylistID)


def is_playlist_id(value) -> bool:
    """Whether value is a playlist ID as ``PlaylistID`` accepts it (the setup URL's target is checked the same way)."""
    try:
        _PLAYLIST_ID.validate_python(value)
    except ValidationError:
        return False
    return True


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AddPlaylistSource(_Strict):
    op: Literal["add_playlist_source"]
    playlist_id: PlaylistID

    @property
    def subject(self) -> str:
        return f"source:{self.playlist_id}"


class AddSearchSource(_Strict):
    op: Literal["add_search_source"]
    search_query: SearchQuery
    source_name: SourceName | None = None

    @field_validator("source_name")
    @classmethod
    def _blank_name_is_no_name(cls, value):
        return value or None

    @property
    def subject(self) -> str:
        return f"search:{self.search_query}"


class RemoveSource(_Strict):
    op: Literal["remove_source"]
    source_id: SourceID

    @property
    def subject(self) -> str:
        return f"source-id:{self.source_id}"


class SetSourceCount(_Strict):
    op: Literal["set_source_count"]
    source_id: SourceID
    raid_count: RaidCount

    @property
    def subject(self) -> str:
        return f"source-id:{self.source_id}"


class RemoveRoutineSource(_Strict):
    op: Literal["remove_routine_source"]
    playlist_id: StoredPlaylistID

    @property
    def subject(self) -> str:
        return f"routine-source:{self.playlist_id}"


class _CollectionChange(_Strict):
    @property
    def subject(self) -> str:
        return "collection"


class LinkCollection(_CollectionChange):
    op: Literal["link_collection"]
    playlist_id: PlaylistID


class ReplaceCollection(_CollectionChange):
    op: Literal["replace_collection"]
    playlist_id: PlaylistID


class UnlinkCollection(_CollectionChange):
    op: Literal["unlink_collection"]


class SetDrip(_Strict):
    op: Literal["set_drip"]
    drip_enabled: StrictBool | None = None
    drip_count: DripCount | None = None

    @model_validator(mode="after")
    def _sets_something(self):
        if self.drip_enabled is None and self.drip_count is None:
            raise ValueError("set_drip needs drip_enabled or drip_count")
        return self

    @property
    def subject(self) -> str:
        return "drip"


class _ArchiveChange(_Strict):
    @property
    def subject(self) -> str:
        return "archive"


class PairArchive(_ArchiveChange):
    op: Literal["pair_archive"]
    playlist_id: PlaylistID


class ReplaceArchive(_ArchiveChange):
    op: Literal["replace_archive"]
    playlist_id: PlaylistID


class UnpairArchive(_ArchiveChange):
    op: Literal["unpair_archive"]


class SetAutoArchive(_Strict):
    op: Literal["set_auto_archive"]
    value: StrictBool

    @property
    def subject(self) -> str:
        return "auto-archive"


SetupChange = Annotated[
    Union[
        AddPlaylistSource, AddSearchSource, RemoveSource, SetSourceCount, RemoveRoutineSource,
        LinkCollection, ReplaceCollection, UnlinkCollection, SetDrip,
        PairArchive, ReplaceArchive, UnpairArchive, SetAutoArchive,
    ],
    Field(discriminator="op"),
]


class SectionDigests(_Strict):
    """The ``section_digests`` of the context a review was based on (optional; names a Conflict's sections)."""

    sources: Digest
    collection: Digest
    archive: Digest
    routines: Digest


class SetupImpactRequest(_Strict):
    """``POST /playlist/<id>/setup/impact``: the changes to review against one configuration revision."""

    configuration_revision: Digest
    changes: Annotated[list[SetupChange], Field(min_length=1, max_length=MAX_CHANGES)]
    section_digests: SectionDigests | None = None

    @model_validator(mode="after")
    def _one_change_per_subject(self):
        seen = {}
        for index, change in enumerate(self.changes):
            first = seen.setdefault(change.subject, index)
            if first != index:
                raise ValueError(f"changes {first} and {index} both change {change.subject}")
        return self


class SetupSaveRequest(SetupImpactRequest):
    """``PUT /playlist/<id>/setup/settings``: the reviewed changes and their signed acknowledgment.

    The acknowledgment's shape is checked by ``signed_digest.verify``, so a
    malformed token is refused as an invalid acknowledgment, not a bad body.
    """

    acknowledgment: Any


class CreateSetupPlaylistRequest(_Strict):
    """``POST /playlist/<id>/setup/spotify-playlists``: one explicitly named private playlist."""

    purpose: Literal["collection", "archive"]
    name: PlaylistName
