"""Local relation checks prevent a second restore racing an unresolved earlier child."""

from sqlalchemy import select

from shuffify.models.db import JobExecution
from shuffify.services.operations.restoration import RestoreUnavailable


def conclusively_no_dispatch(row):
    """A terminal state alone never proves that a provider request cannot be in flight."""
    document = row.journal
    if not isinstance(document, dict):
        return False
    runtime, boundaries = document.get("runtime", {}), document.get("boundaries", {})
    if not isinstance(runtime, dict) or not isinstance(boundaries, dict):
        return False
    terminal = runtime.get("terminal_finalization", {})
    if not isinstance(terminal, dict):
        return False
    if (
        row.state != "failed"
        or row.completed_at is None
        or type(document.get("version")) is not int
        or document["version"] != 1
        or document.get("status") != "failed"
        or terminal.get("state") != "failed"
        or terminal.get("done") is not True
        or boundaries.get("accepted")
    ):
        return False
    try:
        plans = document["plans"] + document.get("compensation", [])
        return all(
            plan["kind"] in {"replace", "details"}
            and all(
                type(batch["attempts"]) is int and batch["attempts"] == 0 and batch["state"] in {"prepared", "rejected"}
                for batch in plan["batches"]
            )
            for plan in plans
        )
    except (KeyError, TypeError, AttributeError):
        return False


def blocking_attempt(storage, user_id, parent_id, *, exclude_operation_id=None):
    statement = select(JobExecution).where(
        JobExecution.user_id == user_id,
        JobExecution.kind == "operation_restore",
        JobExecution.payload["command"]["capture"]["operation_id"].as_integer() == parent_id,
    )
    if exclude_operation_id is not None:
        statement = statement.where(JobExecution.id != exclude_operation_id)
    for child in storage.scalars(statement.order_by(JobExecution.id)):
        if not conclusively_no_dispatch(child):
            return child
    return None


def require_no_attempt(storage, user_id, parent_id, *, exclude_operation_id=None):
    child = blocking_attempt(storage, user_id, parent_id, exclude_operation_id=exclude_operation_id)
    if child is not None:
        raise RestoreUnavailable(
            "restoration_attempt_unresolved",
            f"Restoration operation {child.id} still needs review. Confirm its result before starting another.",
            operation_id=child.id,
        )
