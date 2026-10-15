"""Frozen private intent for a new playlist or an existing asset association."""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from shuffify.schemas.workshop_finalize_requests import PlaylistID

Name = Annotated[str, Field(min_length=1, max_length=255)]
AuthoredName = Annotated[str, Field(min_length=1, max_length=512)]


class AssetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class AssetBinding(AssetModel):
    id: Annotated[StrictInt, Field(gt=0)]
    playlist_id: PlaylistID


class AssetCommand(AssetModel):
    version: ClassVar[int] = 1
    kind: Literal["pair_create", "raid_link_create", "setup_playlist_create"]
    playlist_id: PlaylistID
    purpose: Literal["archive", "collection"]
    create_new: bool
    name: AuthoredName
    description: Name
    target_playlist_name: Name | None = None
    asset_playlist_id: PlaylistID | None = None
    asset_playlist_name: Name | None = None
    existing_binding: AssetBinding | None = None
    drip_count: Annotated[StrictInt, Field(ge=1, le=50)] = 3
    drip_enabled: bool = False

    @property
    def already_linked(self):
        return self.existing_binding is not None

    @property
    def reserved_targets(self):
        return sorted({self.playlist_id, *([self.asset_playlist_id] if self.asset_playlist_id else [])})

    @model_validator(mode="after")
    def validate_mode(self):
        if self.kind == "pair_create" and self.purpose != "archive":
            raise ValueError("A pair requires an archive")
        if self.kind == "raid_link_create" and self.purpose != "collection":
            raise ValueError("A raid link requires a collection")
        if self.create_new:
            if self.asset_playlist_id is not None or self.existing_binding is not None:
                raise ValueError("A new playlist cannot already have an asset identity")
        elif self.asset_playlist_id is None:
            raise ValueError("An existing asset requires an identity")
        if self.asset_playlist_id == self.playlist_id:
            raise ValueError("The asset must differ from its target")
        if self.kind == "setup_playlist_create" and not self.create_new:
            raise ValueError("Setup creation must create a new playlist")
        if self.existing_binding is not None and (
            self.kind != "raid_link_create" or self.existing_binding.playlist_id != self.asset_playlist_id
        ):
            raise ValueError("The existing link differs from its captured asset")
        return self
