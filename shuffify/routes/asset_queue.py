"""Local-only admission of one immutable playlist asset command."""

from flask import jsonify

from shuffify.routes.queue import AdmissionRejected, enqueue_command
from shuffify.services.api_response import error_payload
from shuffify.services.operations.commands.asset_capture import AssetCaptureRejected, capture_asset


def admit_asset(user, playlist_id, kind, values):
    def build(storage):
        try:
            return capture_asset(storage, user.id, playlist_id, kind=kind, **values), None
        except AssetCaptureRejected as error:
            raise AdmissionRejected(
                (
                    jsonify(
                        error_payload(
                            error.message,
                            error.http_status,
                            error_code=error.code,
                            retryable=False,
                            **getattr(error, "details", {}),
                        )
                    ),
                    error.http_status,
                )
            ) from error

    return enqueue_command(user, kind, {"playlist_id": playlist_id, **values}, build)
