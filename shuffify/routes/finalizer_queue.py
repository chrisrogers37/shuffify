"""Capture exact source tasks locally before accepting their worker operation."""

from flask import jsonify

from shuffify.routes.queue import AdmissionRejected, enqueue_command
from shuffify.services.operations.commands.finalizer_capture import (
    FinalizerCaptureRejected,
    capture_append,
    capture_raids,
    capture_restore,
)
from shuffify.services.workshop_finalize_service import WorkshopFinalizeResult

CAPTURES = {
    "workshop_finalize_raids": (capture_raids, "promoted_count"),
    "workshop_archive_append": (capture_append, "archived_count"),
    "workshop_finalize_restore": (capture_restore, "restored_count"),
}


def admit_finalizer(user, playlist_id, kind, parsed):
    capture, count_key = CAPTURES[kind]
    values = parsed.model_dump(exclude={"inspect_only"})

    def build(storage):
        try:
            return capture(storage, user.id, playlist_id, **values), None
        except FinalizerCaptureRejected as error:
            result = WorkshopFinalizeResult.request_rejected(
                values, error.message, error.http_status, count_key, error_code=error.code
            )
            body = dict(result.to_dict(), retryable=error.retryable)
            raise AdmissionRejected((jsonify(body), error.http_status)) from error

    return enqueue_command(user, kind, {"playlist_id": playlist_id, **values}, build)
