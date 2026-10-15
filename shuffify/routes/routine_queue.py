"""Local-only capture of routine commands after each route's public parsing."""

from pydantic import ValidationError

from shuffify.routes.queue import AdmissionRejected, enqueue_command
from shuffify.services.operations.commands.routine_capture import (
    CaptureNotFound,
    CaptureRejected,
    capture_direct,
    capture_schedule,
)


def _capture(build, on_rejection):
    try:
        return build(), None
    except CaptureNotFound as error:
        raise AdmissionRejected(on_rejection(str(error), 404)) from error
    except CaptureRejected as error:
        raise AdmissionRejected(on_rejection(error.message, 400)) from error
    except ValidationError as error:
        message = "Review the routine's playlist settings before running it."
        raise AdmissionRejected(on_rejection(message, 400)) from error


def admit_schedule(user, schedule_id, *, reviewed, on_rejection):
    """Replay an accepted key before reading this routine's current definition."""
    def build(storage):
        return _capture(lambda: capture_schedule(storage, user.id, schedule_id, reviewed=reviewed), on_rejection)

    return enqueue_command(user, "schedule_run", {"schedule_id": schedule_id, "reviewed": reviewed}, build,
                           schedule_id=schedule_id)


def admit_direct(user, playlist_id, kind, *, source_playlist_ids=None, reviewed=None, on_rejection):
    """Reserve the captured collection/archive without authenticating Spotify."""
    operation_kind = {"raid": "raid_now", "drip": "drip_now", "raid_and_drip": "raid_and_drip"}[kind]

    def build(storage):
        return _capture(
            lambda: capture_direct(storage, user.id, playlist_id, kind,
                                   source_playlist_ids=source_playlist_ids, reviewed=reviewed), on_rejection,
        )

    intent = {"playlist_id": playlist_id, "source_playlist_ids": source_playlist_ids, "reviewed": reviewed}
    return enqueue_command(user, operation_kind, intent, build)
