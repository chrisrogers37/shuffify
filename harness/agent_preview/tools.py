"""Two local read tools. The caller's fixture binding is outside the tool arguments."""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shuffify.models.db import JobExecution
from shuffify.services.operations.repository import public_operation
from shuffify.services.shuffle_service import ShuffleService

HERE = Path(__file__).resolve().parent
PREVIEW_LIMIT = 20
PlaylistID = Annotated[str, Field(min_length=1, max_length=255)]


class ToolError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlanRequest(ToolModel):
    playlist_id: PlaylistID
    action: Literal["newest_first"]


class StatusRequest(ToolModel):
    operation_id: Annotated[int, Field(gt=0)]


class Track(ToolModel):
    uri: str
    name: str


class Position(ToolModel):
    position: Annotated[int, Field(ge=1)]
    before: Track
    after: Track
    locked: bool


class PlanPreview(ToolModel):
    state: Literal["preview"]
    playlist_id: PlaylistID
    playlist_name: str
    snapshot_id: str
    action: Literal["newest_first"]
    track_count: Annotated[int, Field(ge=0)]
    changed_positions: Annotated[int, Field(ge=0)]
    positions: Annotated[list[Position], Field(max_length=PREVIEW_LIMIT)]
    truncated: bool


def load_fixture():
    return json.loads((HERE / "workspace.json").read_text())


def plan_playlist_operation(fixture, request):
    playlist = fixture["playlists"].get(request.playlist_id)
    if playlist is None or playlist["user_id"] != fixture["owner_id"]:
        raise ToolError("playlist_not_found", "Playlist not found.")

    tracks = playlist["tracks"]
    before = [track["uri"] for track in tracks]
    locks = playlist["locked_positions"]
    after = ShuffleService.execute("NewestFirstShuffle", tracks, {"jitter": 1}, locked_positions=locks)
    if Counter(before) != Counter(after):
        raise ToolError("invalid_plan", "The proposed order did not preserve every track occurrence.")

    labels = {track["uri"]: Track(uri=track["uri"], name=track["name"]) for track in tracks}
    positions = [
        Position(position=i + 1, before=labels[uri], after=labels[after[i]], locked=locks.get(str(i)) == uri)
        for i, uri in enumerate(before[:PREVIEW_LIMIT])
    ]
    return PlanPreview(
        state="preview", playlist_id=request.playlist_id, playlist_name=playlist["name"],
        snapshot_id=playlist["snapshot_id"], action=request.action, track_count=len(before),
        changed_positions=sum(a != b for a, b in zip(before, after, strict=True)),
        positions=positions, truncated=len(before) > PREVIEW_LIMIT,
    ).model_dump()


def get_operation_status(fixture, request):
    # These historical parent rows have no restoration children. They stay transient:
    # projecting a fixture never opens a DB session or calls admission or the worker.
    for seed in fixture["operations"]:
        if seed["id"] == request.operation_id and seed["user_id"] == fixture["owner_id"]:
            fields = dict(seed)
            for key in ("started_at", "completed_at"):
                if fields[key] is not None:
                    fields[key] = datetime.fromisoformat(fields[key])
            row = JobExecution(**fields)
            return {"success": True, "operation": public_operation(row)}
    raise ToolError("operation_not_found", "Operation not found.")


def contracts():
    status_schema = HERE.parents[1] / "web/schemas/operation-context.schema.json"
    return [
        {
            "name": "plan_playlist_operation",
            "description": "Preview newest-first order with track locks on the bound fixture playlist.",
            "annotations": {"readOnlyHint": True},
            "inputSchema": PlanRequest.model_json_schema(),
            "outputSchema": PlanPreview.model_json_schema(),
        },
        {
            "name": "get_operation_status",
            "description": "Read a recorded outcome. Available recovery means eligibility to open restore review.",
            "annotations": {"readOnlyHint": True},
            "inputSchema": StatusRequest.model_json_schema(),
            "outputSchema": {"type": "object", **json.loads(status_schema.read_text())},
        },
    ]


def call(fixture, name, arguments):
    handlers = {
        "plan_playlist_operation": (PlanRequest, plan_playlist_operation),
        "get_operation_status": (StatusRequest, get_operation_status),
    }
    if name not in handlers:
        raise ToolError("unknown_tool", "Unknown tool.")
    schema, handler = handlers[name]
    try:
        request = schema.model_validate(arguments)
    except ValidationError:
        raise ToolError("invalid_arguments", "Arguments do not match the tool contract.") from None
    return handler(fixture, request)
