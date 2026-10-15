"""Local-only selection of known unaccepted effects for an explicitly reviewed restore."""

from copy import deepcopy
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from shuffify.schemas.workshop_finalize_requests import PlaylistID, TrackURI
from shuffify.services.live_review_bindings import fingerprint

MAX_RESTORE_RESOURCES = 100


class RestoreUnavailable(ValueError):
    def __init__(self, code, message, http_status=409, *, operation_id=None):
        self.code, self.message, self.http_status = code, message, http_status
        self.operation_id = operation_id
        super().__init__(message)


class RestoreTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    playlist_id: PlaylistID
    scope: Literal["tracks", "metadata"]


class RestoreResource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    playlist_id: PlaylistID
    track_uris: tuple[TrackURI, ...] | None
    metadata: dict[str, str | bool]

    @field_validator("track_uris", mode="before")
    @classmethod
    def ordered_tracks(cls, value):
        if value is None:
            return None
        if type(value) not in (list, tuple):
            raise ValueError("Track order must be a JSON array")
        return tuple(value)

    @field_validator("metadata")
    @classmethod
    def written_fields(cls, value):
        if set(value) - {"name", "description", "public"}:
            raise ValueError("Only written playlist metadata can be restored")
        if any(type(item) is not (bool if key == "public" else str) for key, item in value.items()):
            raise ValueError("Restoration metadata has invalid field types")
        return deepcopy(value)

    @model_validator(mode="after")
    def nonempty_effect(self):
        if self.track_uris is None and not self.metadata:
            raise ValueError("A writable restoration resource must contain a known effect")
        return self


class RestoreCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: ClassVar[str] = "operation_restore"
    operation_id: Annotated[StrictInt, Field(gt=0)]
    playlist_id: PlaylistID
    targets: Annotated[tuple[PlaylistID, ...], Field(min_length=1, max_length=MAX_RESTORE_RESOURCES)]
    resources: tuple[RestoreResource, ...]
    observations: tuple[RestoreTarget, ...]
    scope_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @field_validator("targets", "resources", "observations", mode="before")
    @classmethod
    def ordered_array(cls, value):
        if type(value) not in (list, tuple):
            raise ValueError("The capture requires a JSON array")
        return tuple(value)

    @model_validator(mode="after")
    def canonical_scope(self):
        ids = [resource.playlist_id for resource in self.resources]
        if (
            not self.targets
            or self.targets != tuple(sorted(set(self.targets)))
            or self.playlist_id != self.targets[0]
            or not ids
            or ids != sorted(set(ids))
            or not set(ids).issubset(self.targets)
            or [item.playlist_id for item in self.observations] != list(self.targets)
        ):
            raise ValueError("The restoration scope must be canonical and fully reserved")
        scopes = {item.playlist_id: item.scope for item in self.observations}
        if any(
            resource.track_uris is not None and scopes[resource.playlist_id] != "tracks" for resource in self.resources
        ):
            raise ValueError("Track restoration requires a complete track observation")
        return self

    @property
    def reserved_targets(self):
        return list(self.targets)


def _refuse():
    return RestoreUnavailable("operation_not_restorable", "There are no confirmed operation changes to restore.")


def _validate_plans(plans, targets, *, compensation=False):
    for plan in plans:
        kind, resource = plan["kind"], plan["resource"]
        allowed = {"compensate"} if compensation else {"replace", "add", "remove", "details"}
        if kind not in allowed or resource not in targets:
            raise _refuse()
        resource_path = f"/playlists/{resource}"
        for index, batch in enumerate(plan["batches"]):
            metadata = kind == "details" or compensation and batch["path"] == resource_path
            path = resource_path + ("" if metadata else "/items")
            method = (
                "PUT"
                if metadata
                else "DELETE"
                if kind == "remove"
                else "POST"
                if kind == "add" or kind in {"replace", "compensate"} and index
                else "PUT"
            )
            state, attempts = batch["state"], batch["attempts"]
            if (
                batch["path"] != path
                or batch["method"] != method
                or not isinstance(batch["body"], dict)
                or state not in {"prepared", "dispatched", "acknowledged", "rejected"}
                or type(attempts) is not int
                or attempts < 0
                or metadata
                and not set(batch["body"]).issubset({"name", "description", "public"})
            ):
                raise _refuse()


def capture_restore(operation, user_id):
    """Derive authority and desired fields from private persisted state, never client contents."""
    if operation is None or type(user_id) is not int or operation.user_id != user_id:
        raise RestoreUnavailable("operation_not_found", "Operation not found.", 404)
    if getattr(operation, "kind", None) == "operation_restore":
        raise RestoreUnavailable(
            "nested_restoration_unavailable",
            "Review this restoration's existing result before taking further action.",
        )
    if operation.state != "needs_attention":
        raise _refuse()
    try:
        document = deepcopy(operation.journal)
        if type(document.get("version")) is not int or document["version"] != 1:
            raise _refuse()
        resolved = document.get("runtime", {}).get("restoration", {}).get("operation_id")
        if type(resolved) is int and resolved > 0:
            raise RestoreUnavailable(
                "operation_already_restored", "This operation has already been restored.", operation_id=resolved
            )
        plans = document["plans"]
        if any(plan["kind"] == "create" for plan in plans):
            raise RestoreUnavailable(
                "created_playlist_requires_review",
                "Review created playlists directly; they cannot be recreated or deleted here.",
            )
        if any(
            batch["state"] == "dispatched"
            for plan in plans + document.get("compensation", [])
            for batch in plan["batches"]
        ):
            raise RestoreUnavailable(
                "write_completion_unknown",
                "A Spotify write may still be completing. Its result must be confirmed before restoration.",
            )
        boundaries = document.get("boundaries")
        if boundaries is None:
            selected, baselines = plans, document["resources"]
        else:
            current = boundaries["current"]
            if current is None:
                raise _refuse()
            start, previous = current["start"], 0
            if type(start) is not int or not 0 <= start <= len(plans):
                raise _refuse()
            for accepted in boundaries["accepted"]:
                if (
                    type(accepted["start"]) is not int
                    or type(accepted["end"]) is not int
                    or accepted["start"] != previous
                    or not previous <= accepted["end"] <= start
                ):
                    raise _refuse()
                previous = accepted["end"]
            if start != previous:
                raise _refuse()
            selected, baselines = plans[start:], current["baselines"]
        targets = tuple(sorted(set(operation.targets)))
        if len(targets) > MAX_RESTORE_RESOURCES:
            raise RestoreUnavailable(
                "restore_scope_too_large", "This operation contains too many resources for a restoration review."
            )
        if not set(document["resources"]).issubset(targets):
            raise _refuse()
        _validate_plans(plans, targets)
        _validate_plans(document.get("compensation", []), targets, compensation=True)
        resources = []
        for resource in sorted({plan["resource"] for plan in selected if plan["resource"] is not None}):
            if resource not in targets:
                raise _refuse()
            acknowledged = [
                batch
                for plan in selected
                if plan["resource"] == resource
                for batch in plan["batches"]
                if batch["state"] == "acknowledged"
            ]
            tracks = any(batch["path"].endswith("/items") for batch in acknowledged)
            fields = {
                key
                for batch in acknowledged
                if not batch["path"].endswith("/items")
                for key in batch["body"]
                if key in {"name", "public", "description"}
            }
            if not tracks and not fields:
                continue
            baseline = baselines[resource]["baseline"]
            metadata = {key: baseline["metadata"][key] for key in sorted(fields)}
            if (
                "public" in metadata
                and type(metadata["public"]) is not bool
                or any(not isinstance(value, str) for key, value in metadata.items() if key != "public")
            ):
                raise _refuse()
            resources.append(
                RestoreResource(
                    playlist_id=resource,
                    track_uris=tuple(baseline["track_uris"]) if tracks else None,
                    metadata=metadata,
                )
            )
        if not resources:
            raise _refuse()
        observations = tuple(
            RestoreTarget(playlist_id=target, scope=document["resources"].get(target, {}).get("scope", "tracks"))
            for target in targets
        )
        scope = fingerprint(
            {
                "purpose": "shuffify.operation-restore.v1",
                "operation_id": operation.id,
                "targets": targets,
                "resources": [resource.model_dump(mode="json") for resource in resources],
                "observations": [item.model_dump(mode="json") for item in observations],
            }
        )
        return RestoreCapture(
            operation_id=operation.id,
            playlist_id=targets[0],
            targets=targets,
            resources=tuple(resources),
            observations=observations,
            scope_fingerprint=scope,
        )
    except RestoreUnavailable:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise _refuse() from None
