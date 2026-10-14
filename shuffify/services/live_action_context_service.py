"""Read-only review context for live actions.

Reviews a Library Shuffle, Undo or visibility change or a snapshot
restore before it runs, with the comparison bindings from
``live_review_bindings`` that the run checks again; the executor actions'
reviews are in ``live_executor_context_service``. Nothing here writes, runs an
algorithm, snapshots or records.
"""

import logging
from collections import Counter
from datetime import datetime, timezone

from pydantic import ValidationError

from shuffify.schemas import parse_shuffle_request
from shuffify.schemas.raid_requests import RaidNowRequest
from shuffify.schemas.requests import ShuffleRequest
from shuffify.services.live_executor_context_service import LiveExecutorContextService
from shuffify.services.live_metadata_service import LiveMetadataService
from shuffify.services.live_operation_service import DirectFailure, direct_rejected_result, direct_response_payload
from shuffify.services.live_playlist_service import LivePlaylistService, track_mutation_type
from shuffify.services.live_review_bindings import (
    privacy_scope,
    shuffle_scope,
    snapshot_bindings,
    undo_bindings,
)
from shuffify.services.live_review_context import (
    context_rejected,
    failure_rejected,
    playlist_name,
    read_failure,
    resource_entry,
    review_context,
)
from shuffify.services.live_review_scope import DIRECT_STEPS
from shuffify.services.playlist_service import PlaylistService
from shuffify.services.playlist_snapshot_service import PlaylistSnapshotNotFoundError, PlaylistSnapshotService
from shuffify.services.state_service import StateService
from shuffify.services.workshop_commit_service import _TRACK_URI, WorkshopCommitService
from shuffify.shuffle_algorithms.registry import ShuffleRegistry

logger = logging.getLogger(__name__)

# The schema's shuffle fields; track locks are executor policy, not a Library control.
SHUFFLE_FORM_FIELDS = frozenset(ShuffleRequest.model_fields) - {"locked_positions"}
_CONTEXT_KEYS = {
    "shuffle": {"kind", "form"}, "undo": {"kind"}, "privacy": {"kind", "public"},
    "raid": {"kind"}, "drip": {"kind"}, "raid_and_drip": {"kind"},
}
# A Collect may name explicit extra sources, exactly as its mutation body may.
_OPTIONAL_KEYS = {"raid": {"source_playlist_ids"}, "raid_and_drip": {"source_playlist_ids"}}
_INSPECT_DESIRED = {"tracks": "desired_track_uris", "privacy": "desired_public"}
# The operation kind each observer request reports.
INSPECTION_KINDS = {"tracks": "track_inspection", "privacy": "privacy"}


def summarize(baseline, desired):
    """Occurrence counts and shared-occurrence order, as WorkshopSave.summarize."""
    before, after = Counter(baseline), Counter(desired)

    def shared(uris, other):
        seen, kept = Counter(), []
        for uri in uris:
            seen[uri] += 1
            if seen[uri] <= other[uri]:
                kept.append(uri)
        return kept

    return {
        "added_count": sum((after - before).values()),
        "removed_count": sum((before - after).values()),
        "order_changed": shared(baseline, after) != shared(desired, before),
    }


class ContextRequestError(ValueError):
    def __init__(self, kind, code, message):
        super().__init__(message)
        self.kind, self.code, self.message = kind, code, message


def parse_context_request(body):
    """The (kind, data) of one exact review request variant."""
    kind = body.get("kind") if isinstance(body, dict) else None
    if not isinstance(kind, str) or kind not in _CONTEXT_KEYS:
        raise ContextRequestError("live_context", "invalid_request", "Choose a supported action to review.")
    if not _CONTEXT_KEYS[kind] <= set(body) <= _CONTEXT_KEYS[kind] | _OPTIONAL_KEYS.get(kind, set()):
        raise ContextRequestError(kind, "invalid_request", "This review request has missing or unexpected fields.")
    if kind in DIRECT_STEPS:
        return kind, _extra_sources(kind, body)
    if kind == "undo":
        return kind, None
    if kind == "privacy":
        if type(body["public"]) is not bool:
            raise ContextRequestError(kind, "invalid_request", "The 'public' field must be true or false.")
        return kind, body["public"]
    form = body["form"]
    invalid = ContextRequestError(kind, "invalid_request", "Check the shuffle method and its options.")
    if (
        not isinstance(form, dict)
        or "algorithm" not in form
        or not set(form) <= SHUFFLE_FORM_FIELDS
        or any(not isinstance(value, str) for value in form.values())
    ):
        raise invalid
    if form["algorithm"].strip() not in ShuffleRegistry.get_available_algorithms():
        raise ContextRequestError(kind, "invalid_algorithm", "Choose a valid shuffle method.")
    try:
        parsed = parse_shuffle_request(dict(form))
    except (ValidationError, ValueError):
        raise invalid from None
    return kind, (parsed.algorithm, parsed.get_algorithm_params())


def _extra_sources(kind, body):
    """A Collect's explicit extra source IDs, validated as its mutation validates them, or None."""
    if "source_playlist_ids" not in body:
        return None
    extras = body["source_playlist_ids"]
    invalid = ContextRequestError(kind, "invalid_request", "Choose valid raid sources, or use all configured sources.")
    # Unlike the mutation, a review never reads an explicit null as "all sources".
    if not isinstance(extras, list) or any(not isinstance(value, str) for value in extras):
        raise invalid
    try:
        RaidNowRequest(source_playlist_ids=extras)
    except ValidationError:
        raise invalid from None
    return list(extras)


def context_kind(body):
    """The request's review kind once it names a valid one, else live_context."""
    kind = body.get("kind") if isinstance(body, dict) else None
    return kind if isinstance(kind, str) and kind in _CONTEXT_KEYS else "live_context"


def _context(kind, target_id, name, *, label, mutation_type, field, bookkeeping, scope, current, action,
             before_order=None, history=None):
    return {
        "version": 1,
        "kind": kind,
        "target": {"id": target_id, "name": name},
        "label": label,
        "resources": [resource_entry(target_id, name, "target", "write", mutation_type, field)],
        "required_bookkeeping": list(bookkeeping),
        "review_binding": {"version": 1, "scope": scope, "before_order": before_order, "history": history},
        "current": current,
        "action": action,
    }


class LiveActionContextService:
    """Resolve one finite Library action review without writing, running or recording."""

    def __init__(self, api):
        self.api = api

    def resolve(self, playlist_id, user, session, body):
        """(payload, status) for one review request."""
        try:
            kind, data = parse_context_request(body)
        except ContextRequestError as error:
            return context_rejected(error.code, 400, error.message, kind=error.kind)
        if kind in DIRECT_STEPS:
            return LiveExecutorContextService(self.api).direct(kind, playlist_id, user, data)
        if kind == "privacy":
            return self._privacy(playlist_id, user, data)
        try:
            observed = WorkshopCommitService(self.api).read_complete(playlist_id, user.spotify_id)
            if kind == "shuffle":
                context = self._shuffle(playlist_id, observed, *data)
            else:
                context = self._undo(playlist_id, observed, session)
        except Exception as error:
            return failure_rejected(kind, read_failure(error))
        return {"success": True, "context": context}, 200

    @staticmethod
    def _shuffle(playlist_id, observed, algorithm, params):
        if not observed.track_uris:
            raise DirectFailure(400, "empty_playlist", "The playlist contains no tracks.")
        name = playlist_name(observed.name)
        return _context(
            "shuffle", playlist_id, name, label="Shuffle now", mutation_type="track_order", field=None,
            bookkeeping=["session_history"], scope=shuffle_scope(playlist_id, algorithm, params),
            current={"track_count": len(observed.track_uris), "loaded_at": observed.loaded_at},
            action={
                "algorithm": {"id": algorithm, "name": ShuffleRegistry.get_algorithm(algorithm)().name},
                "params": dict(params),
            },
        )

    @staticmethod
    def _undo(playlist_id, observed, session):
        # Copies the selection; history is never initialized or advanced here.
        selection = StateService.peek_undo(session, playlist_id)
        desired, current = list(selection.desired_uris), list(observed.track_uris)
        LivePlaylistService.validate_order(desired)
        bindings = undo_bindings(playlist_id, selection, current)
        name = playlist_name(observed.name)
        return _context(
            "undo", playlist_id, name, label="Restore previous saved version now",
            mutation_type=track_mutation_type(desired, current),
            field=None, bookkeeping=["session_history"], scope=bindings["reviewed_scope"],
            before_order=bindings["reviewed_before_order"],
            history=bindings["reviewed_history"],
            current={"track_count": len(current), "track_uris": current, "loaded_at": observed.loaded_at},
            action={
                "current_index": selection.current_index,
                "selected_index": selection.current_index - 1,
                "desired_track_uris": desired,
                "summary": summarize(current, desired),
            },
        )

    def snapshot_restore(self, snapshot_id, user):
        """(payload, status) reviewing a restore of one owned snapshot against a fresh complete read."""
        kind = "snapshot_restore"
        try:
            snapshot = PlaylistSnapshotService.get_snapshot(snapshot_id, user.id)
        except PlaylistSnapshotNotFoundError:
            return context_rejected("snapshot_not_found", 404, "Snapshot not found.", kind=kind)
        except Exception:
            logger.warning("Snapshot review lookup unavailable for %s", snapshot_id, exc_info=True)
            return context_rejected(
                "snapshot_unavailable", 503,
                "Snapshot could not be read. Nothing was changed; load the review again.", retryable=True, kind=kind,
            )
        playlist_id, desired = snapshot.playlist_id, list(snapshot.track_uris)
        if not desired:
            return context_rejected("empty_snapshot", 400, "Snapshot contains no tracks.", kind=kind)
        try:
            LivePlaylistService.validate_order(desired)
            # The review compares against fresh Spotify contents, never a local working draft.
            observed = WorkshopCommitService(self.api).read_complete(playlist_id, user.spotify_id)
        except Exception as error:
            return failure_rejected(kind, read_failure(error))
        current, name = list(observed.track_uris), playlist_name(observed.name)
        bindings = snapshot_bindings(playlist_id, snapshot.id, desired, current)
        context = review_context(
            kind, "Restore saved snapshot now", playlist_id, name,
            snapshot={"id": snapshot.id, "created_at": snapshot.created_at.isoformat()},
            resources=[resource_entry(playlist_id, name, "target", "write", track_mutation_type(desired, current))],
            steps=[], bookkeeping=["session_history"],
            scope=bindings["reviewed_scope"], before_order=bindings["reviewed_before_order"],
            current={"track_count": len(current), "track_uris": current, "loaded_at": observed.loaded_at},
            action={"snapshot_id": snapshot.id, "desired_track_uris": desired, "summary": summarize(current, desired)},
        )
        return {"success": True, "context": context}, 200

    def _privacy(self, playlist_id, user, public):
        try:
            metadata = PlaylistService(self.api).validate_user_can_edit(
                playlist_id, user.spotify_id, skip_cache=True
            )
        except Exception as error:
            return direct_response_payload(LiveMetadataService.read_failure(error), "privacy")
        current = metadata.get("public") if isinstance(metadata, dict) else None
        if type(current) is not bool:
            return context_rejected(
                "metadata_unavailable", 502,
                "Playlist visibility could not be read. Nothing was changed; load the review again.",
                retryable=True, kind="privacy",
            )
        state = "public" if public else "private"
        context = _context(
            "privacy", playlist_id, playlist_name(metadata.get("name")), label=f"Make playlist {state} now",
            mutation_type="metadata", field="public", bookkeeping=[],
            scope=privacy_scope(playlist_id, public),
            current={"public": current, "loaded_at": datetime.now(timezone.utc).isoformat()},
            action={"public": public},
        )
        return {"success": True, "context": context}, 200


class InspectRequestError(ValueError):
    """An observer request that is not one exact finite variant."""


def inspection_kind(body):
    """The observer operation kind a possibly unparseable request names."""
    return INSPECTION_KINDS["privacy" if isinstance(body, dict) and body.get("kind") == "privacy" else "tracks"]


def parse_inspect_request(body):
    """(kind, retained desired intent or None) for one exact observer variant."""
    kind = body.get("kind") if isinstance(body, dict) else None
    if not isinstance(kind, str) or kind not in _INSPECT_DESIRED:
        raise InspectRequestError()
    field = _INSPECT_DESIRED[kind]
    if not set(body) <= {"kind", field}:
        raise InspectRequestError()
    if field not in body:
        return kind, None  # Deliberately inconclusive: there is no retained intent.
    desired = body[field]
    if kind == "privacy":
        if type(desired) is not bool:
            raise InspectRequestError()
        return kind, desired
    if not isinstance(desired, list) or any(
        not isinstance(uri, str) or not _TRACK_URI.fullmatch(uri) for uri in desired
    ):
        raise InspectRequestError()
    return kind, list(desired)


def inspection_rejected(body, error_code, http_status, message):
    """A check that did not observe anything; earlier results stay unverified."""
    kind = inspection_kind(body)
    result = direct_rejected_result(kind, error_code=error_code, http_status=http_status, unknown=True, message=message)
    return inspection_payload(result, kind)


def inspection_payload(result, kind):
    payload, status = direct_response_payload(result, kind)
    payload["inspection_only"] = True
    return payload, status
