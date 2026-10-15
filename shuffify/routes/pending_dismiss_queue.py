"""Freeze an exact local inbox selection for a DB-first queued dismissal."""

from flask import jsonify

from shuffify.routes.queue import AdmissionRejected, enqueue_command
from shuffify.services.api_response import error_payload
from shuffify.services.operations.commands.dismiss_capture import DismissRejected, capture_dismiss, capture_dismiss_all


def admit_pending_dismiss(user, playlist_id, *, track_ids=None):
    selection = "all" if track_ids is None else "selected"
    intent = {"playlist_id": playlist_id, "selection": selection}
    if track_ids is not None:
        intent["track_ids"] = sorted(set(track_ids))

    def build(storage):
        try:
            command = (
                capture_dismiss_all(storage, user.id, playlist_id)
                if track_ids is None
                else capture_dismiss(storage, user.id, playlist_id, track_ids=track_ids)
            )
            return command, None
        except DismissRejected as error:
            raise AdmissionRejected(
                (
                    jsonify(
                        error_payload(
                            error.message, error.http_status, error_code=error.code, retryable=error.retryable
                        )
                    ),
                    error.http_status,
                )
            ) from error

    return enqueue_command(user, "pending_dismiss", intent, build)
