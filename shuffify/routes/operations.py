"""Owner-scoped operation status, independent of Spotify availability."""

from shuffify.routes import api_bp, json_error, json_success, require_session_and_db


@api_bp.get("/operations/<int:operation_id>")
@require_session_and_db
def operation_status(operation_id, user=None):
    """Return bounded progress and the stored result without private command or journal data."""
    from shuffify.services.operations.repository import OperationRepository

    operation = OperationRepository().get_owned(user.id, operation_id)
    if operation is None:
        return json_error("Operation not found.", 404)
    return json_success("Operation loaded.", operation=operation)
