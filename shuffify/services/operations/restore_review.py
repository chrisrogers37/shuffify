"""Fresh authorized comparison bindings and bounded public restoration facts."""

from copy import deepcopy
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from shuffify.schemas.workshop_finalize_requests import PlaylistID
from shuffify.services.live_review_bindings import fingerprint
from shuffify.services.operations.planner import content
from shuffify.services.operations.restoration import MAX_RESTORE_RESOURCES, RestoreUnavailable
from shuffify.services.playlist_service import PlaylistService
from shuffify.services.workshop_commit_service import IncompletePlaylistRead, WorkshopCommitService

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PREVIEW_LIMIT = 20
METADATA_PREVIEW_LIMIT = 1024


class ResourceDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    playlist_id: PlaylistID
    digest: Digest


class RestoreReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: StrictInt
    reviewed_scope: Digest
    reviewed_resources: Annotated[tuple[ResourceDigest, ...], Field(min_length=1, max_length=MAX_RESTORE_RESOURCES)]

    @field_validator("reviewed_resources", mode="before")
    @classmethod
    def array(cls, value):
        if type(value) not in (list, tuple):
            raise ValueError("Review resources must be a JSON array")
        return tuple(value)

    @model_validator(mode="after")
    def canonical(self):
        ids = [item.playlist_id for item in self.reviewed_resources]
        if self.version != 1 or ids != sorted(set(ids)):
            raise ValueError("The restoration review must be canonical version 1")
        return self


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reviewed: RestoreReview


def read_metadata(api, user, resource):
    reader = PlaylistService(api)
    first = reader.validate_user_can_edit(resource, user.spotify_id, skip_cache=True)
    second = reader.validate_user_can_edit(resource, user.spotify_id, skip_cache=True)
    fields = ("name", "public", "description")
    if any(first.get(key) != second.get(key) for key in fields):
        raise IncompletePlaylistRead("Playlist metadata changed during review")
    return {
        "scope": "metadata",
        "metadata": {key: second.get(key) for key in fields},
        "snapshot_id": second.get("snapshot_id"),
    }


def read_tracks(api, user, resource):
    reader = WorkshopCommitService(api)
    first = reader.read_complete(resource, user.spotify_id)
    second = reader.read_complete(resource, user.spotify_id)
    metadata = api.get_playlist(resource, skip_cache=True)
    if (
        first.track_uris != second.track_uris
        or first.snapshot_id != second.snapshot_id
        or metadata.get("snapshot_id") != second.snapshot_id
    ):
        raise IncompletePlaylistRead("Playlist changed during review")
    return {
        "track_uris": list(second.track_uris),
        "snapshot_id": second.snapshot_id,
        "metadata": {key: metadata.get(key) for key in ("name", "public", "description")},
    }


def read_all(captured, tracks, metadata):
    return {
        item.playlist_id: {
            **(metadata if item.scope == "metadata" else tracks)(item.playlist_id),
            **({"scope": "metadata"} if item.scope == "metadata" else {}),
        }
        for item in captured.observations
    }


def resource_digest(resource, observed):
    value = {"purpose": "shuffify.operation-restore.current.v1", "playlist_id": resource, "content": content(observed)}
    if observed.get("scope") != "metadata":
        value["snapshot_id"] = observed.get("snapshot_id")
    return fingerprint(value)


def bindings(captured, before):
    return RestoreReview(
        version=1,
        reviewed_scope=captured.scope_fingerprint,
        reviewed_resources=[
            ResourceDigest(playlist_id=resource, digest=resource_digest(resource, before[resource]))
            for resource in captured.targets
        ],
    )


def assert_reviewed(captured, reviewed, before=None):
    reviewed = reviewed if isinstance(reviewed, RestoreReview) else RestoreReview.model_validate(reviewed)
    if (
        reviewed.reviewed_scope != captured.scope_fingerprint
        or [item.playlist_id for item in reviewed.reviewed_resources] != list(captured.targets)
        or before is not None
        and bindings(captured, before) != reviewed
    ):
        raise RestoreUnavailable(
            "restore_review_changed", "The reviewed playlists changed. Review the restoration again."
        )
    return reviewed


def _preview(value):
    return value[:METADATA_PREVIEW_LIMIT] if isinstance(value, str) else value


def build_context(captured, tracks, metadata):
    before = read_all(captured, tracks, metadata)
    desired = {item.playlist_id: item for item in captured.resources}
    resources = []
    for target in captured.observations:
        resource, current = target.playlist_id, before[target.playlist_id]
        restore = desired.get(resource)
        current_order = current.get("track_uris")
        desired_order = (
            list(restore.track_uris) if restore is not None and restore.track_uris is not None else current_order
        )
        fields = ["tracks"] if restore is not None and restore.track_uris is not None else []
        fields += sorted(restore.metadata) if restore is not None else []
        resources.append(
            {
                "playlist_id": resource,
                "name": (current.get("metadata", {}).get("name") or "")[:255] or None,
                "role": "restore" if restore is not None else "guard",
                "scope": target.scope,
                "write_fields": fields,
                "current_count": len(current_order) if current_order is not None else None,
                "desired_count": len(desired_order) if desired_order is not None else None,
                "current_preview": deepcopy(current_order[:PREVIEW_LIMIT]) if current_order is not None else [],
                "desired_preview": deepcopy(desired_order[:PREVIEW_LIMIT]) if desired_order is not None else [],
                "current_metadata": {
                    key: _preview(current["metadata"].get(key)) for key in (restore.metadata if restore else {})
                },
                "desired_metadata": {key: _preview(value) for key, value in restore.metadata.items()}
                if restore
                else {},
                "metadata_truncated": bool(
                    restore
                    and any(
                        isinstance(value, str) and len(value) > METADATA_PREVIEW_LIMIT
                        for value in [
                            *restore.metadata.values(),
                            *(current["metadata"].get(key) for key in restore.metadata),
                        ]
                    )
                ),
            }
        )
    return {
        "kind": "operation_restore",
        "operation_id": captured.operation_id,
        "available": True,
        "reviewed": bindings(captured, before).model_dump(mode="json"),
        "resources": resources,
    }
