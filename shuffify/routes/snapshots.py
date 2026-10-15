"""
Snapshot routes: list, create, view, restore, and delete
playlist snapshots.
"""

import logging

from flask import jsonify, request
from pydantic import ValidationError

from shuffify.enums import SnapshotType
from shuffify.routes import (
    api_bp,
    context_preflight,
    json_error,
    json_reply,
    json_success,
    no_store,
    require_auth_and_db,
    require_session_and_db,
    validate_json,
)
from shuffify.routes.queue import AdmissionRejected, capture_direct_history, enqueue_command
from shuffify.schemas import ManualSnapshotRequest
from shuffify.services import (
    PlaylistSnapshotError,
    PlaylistSnapshotNotFoundError,
    PlaylistSnapshotService,
)
from shuffify.services.live_action_context_service import LiveActionContextService
from shuffify.services.live_operation_service import (
    direct_rejected_result,
    direct_response_payload,
    preflight_error_code,
)
from shuffify.services.live_review_bindings import (
    ReviewBindingError,
    invalid_binding_result,
    request_binding,
)
from shuffify.services.live_review_context import context_unavailable
from shuffify.services.operations.commands.schemas import SnapshotRestoreCommand

logger = logging.getLogger(__name__)


@api_bp.route(
    "/playlist/<playlist_id>/snapshots", methods=["GET"]
)
@require_auth_and_db
def list_snapshots(playlist_id, api=None, user=None):
    """List all snapshots for a playlist."""
    limit = request.args.get("limit", 20, type=int)
    limit = max(1, min(limit, 100))

    snapshots = PlaylistSnapshotService.get_snapshots(
        user.id, playlist_id, limit=limit
    )
    return jsonify({
        "success": True,
        "snapshots": [s.to_dict() for s in snapshots],
    })


@api_bp.route(
    "/playlist/<playlist_id>/snapshots", methods=["POST"]
)
@require_auth_and_db
def create_manual_snapshot(
    playlist_id, api=None, user=None
):
    """Create a manual snapshot of a playlist."""
    snap_request, err = validate_json(
        ManualSnapshotRequest
    )
    if err:
        return err

    try:
        snapshot = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id=playlist_id,
            playlist_name=snap_request.playlist_name,
            track_uris=snap_request.track_uris,
            snapshot_type=SnapshotType.MANUAL,
            trigger_description=(
                snap_request.trigger_description
            ),
        )
        logger.info(
            f"User {user.spotify_id} created manual "
            f"snapshot for playlist {playlist_id}"
        )
        return json_success(
            "Snapshot created.",
            snapshot=snapshot.to_dict(),
        )
    except PlaylistSnapshotError as e:
        return json_error(str(e), 500)


@api_bp.route(
    "/snapshots/<int:snapshot_id>", methods=["GET"]
)
@require_auth_and_db
def view_snapshot(snapshot_id, api=None, user=None):
    """View a snapshot's details."""
    try:
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user.id
        )
        return jsonify({
            "success": True,
            "snapshot": snapshot.to_dict(),
        })
    except PlaylistSnapshotNotFoundError:
        return json_error("Snapshot not found.", 404)


def _snapshot_restore_response(result):
    payload, status_code = direct_response_payload(result, "snapshot_restore")
    return jsonify(payload), status_code


def _snapshot_restore_preflight(message, status_code):
    return _snapshot_restore_response(direct_rejected_result(
        "snapshot_restore", error_code=preflight_error_code(status_code),
        http_status=status_code, retryable=status_code in (401, 503), message=message,
    ))


@api_bp.route("/snapshots/<int:snapshot_id>/restore", methods=["POST"])
@require_session_and_db(on_preflight_error=_snapshot_restore_preflight)
def restore_snapshot(snapshot_id, user=None):
    """Freeze an owned snapshot's order and queue its verified restoration."""
    # An optional reviewed bundle; other body keys stay ignored, and a
    # malformed bundle is refused before any database or Spotify read.
    try:
        reviewed = request_binding(request, "snapshot_restore")
    except ReviewBindingError:
        return _snapshot_restore_response(invalid_binding_result("snapshot_restore"))

    def build(storage):
        try:
            snapshot = PlaylistSnapshotService.get_snapshot(snapshot_id, user.id, session=storage)
        except PlaylistSnapshotNotFoundError as error:
            raise AdmissionRejected(_snapshot_restore_response(direct_rejected_result(
                "snapshot_restore", error_code="snapshot_not_found", http_status=404, message="Snapshot not found.",
            ))) from error
        except Exception as error:
            logger.exception("Snapshot lookup unavailable for %s", snapshot_id)
            raise AdmissionRejected(_snapshot_restore_response(direct_rejected_result(
                "snapshot_restore", error_code="snapshot_unavailable", http_status=503,
                retryable=True, message="Snapshot could not be read. No changes were sent.",
            ))) from error
        if not snapshot.track_uris:
            raise AdmissionRejected(_snapshot_restore_response(direct_rejected_result(
                "snapshot_restore", error_code="empty_snapshot", http_status=400,
                message="This snapshot has no saved tracks to restore.",
            )))
        try:
            command = SnapshotRestoreCommand(
                playlist_id=snapshot.playlist_id, snapshot=snapshot.to_dict(), reviewed=reviewed,
            )
        except ValidationError as error:
            raise AdmissionRejected(_snapshot_restore_response(direct_rejected_result(
                "snapshot_restore", error_code="invalid_track_order", http_status=400,
                message="The saved track order cannot be safely restored.",
            ))) from error
        binding, _mapping = capture_direct_history(user, snapshot.playlist_id, "snapshot_restore", storage=storage)
        return command, binding

    return enqueue_command(user, "snapshot_restore", {"snapshot_id": snapshot_id, "reviewed": reviewed}, build)


@api_bp.route("/snapshots/<int:snapshot_id>/restore-context", methods=["GET"])
@no_store
@require_auth_and_db(on_preflight_error=context_preflight("snapshot_restore"))
def snapshot_restore_context(snapshot_id, api=None, user=None):
    """Read-only review of restoring one owned snapshot now; changes nothing."""
    try:
        reply = LiveActionContextService(api).snapshot_restore(snapshot_id, user)
    except Exception:
        logger.exception("Snapshot restore review unavailable for %s", snapshot_id)
        reply = context_unavailable("snapshot_restore")
    return json_reply(reply)


@api_bp.route(
    "/snapshots/<int:snapshot_id>", methods=["DELETE"]
)
@require_auth_and_db
def delete_snapshot(
    snapshot_id, api=None, user=None
):
    """Delete a snapshot."""
    try:
        PlaylistSnapshotService.delete_snapshot(
            snapshot_id, user.id
        )
        return json_success("Snapshot deleted.")
    except PlaylistSnapshotNotFoundError:
        return json_error("Snapshot not found.", 404)
    except PlaylistSnapshotError as e:
        return json_error(str(e), 500)
