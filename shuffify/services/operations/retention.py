"""One bounded maintenance page. Keep durable identities and the evidence still used by readers."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, PlaylistSnapshot
from shuffify.services.operations.history import HistoryStore

RETENTION_DAYS = 90
MAX_PAGE = 100
SETTLED = ("succeeded", "failed", "rolled_back")
LEGACY_TERMINAL = ("success", "failed", "failed_rolled_back", "skipped")


def _settled_document(row):
    document = row.journal
    if not isinstance(document, dict) or document.get("version", 1) != 1 or document.get("status") == "needs_attention":
        return False
    runtime = document.get("runtime", {})
    if not isinstance(runtime, dict) or "restoration" in runtime:
        return False
    recovery = document.get("recovery", {})
    if not isinstance(recovery, dict) or recovery.get("available"):
        return False
    marker = runtime.get("terminal_finalization")
    # Without a final worker decision, a journal with private execution evidence stays intact.
    if marker is None and document.keys() - {"result", "progress", "recovery"}:
        return False
    if marker is not None and (
        not isinstance(marker, dict) or marker.get("done") is not True or marker.get("state") != row.state
    ):
        return False
    # Old writers may lack finalization markers. Never discard ambiguous dispatch evidence.
    for key in ("plans", "compensation"):
        plans = document.get(key, [])
        if not isinstance(plans, list):
            return False
        for plan in plans:
            if not isinstance(plan, dict) or not isinstance(plan.get("batches"), list):
                return False
            if any(not isinstance(batch, dict) or batch.get("state") not in {"prepared", "acknowledged", "rejected"}
                   for batch in plan["batches"]):
                return False
    return True


def _referenced(storage, row):
    if storage.scalar(select(PlaylistSnapshot.id).where(PlaylistSnapshot.job_execution_id == row.id).limit(1)):
        return True
    # The child-to-parent link is in the immutable capture, not a foreign key.
    parent_id = JobExecution.payload["command"]["capture"]["operation_id"]
    if storage.scalar(select(JobExecution.id).where(
        JobExecution.kind == "operation_restore", JobExecution.user_id == row.user_id,
        cast(parent_id.as_string(), String) == str(row.id),
    ).limit(1)):
        return True
    if not isinstance(row.journal, dict) or "history" not in row.journal:
        return False
    return not HistoryStore.can_expire_receipt(storage, row)


def cleanup_page(engine, *, after_id=0, limit=MAX_PAGE, now=None):
    """Inspect up to limit consecutive IDs. Resume at next_after_id; restart at zero on a later sweep."""
    if not 1 <= limit <= MAX_PAGE or after_id < 0:
        raise ValueError("Invalid cleanup page.")
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    deleted = compacted = 0
    with Session(engine) as storage, storage.begin():
        if engine.dialect.name == "sqlite":
            storage.connection().exec_driver_sql("BEGIN IMMEDIATE")
        ids = storage.scalars(select(JobExecution.id).where(JobExecution.id > after_id)
                              .order_by(JobExecution.id).limit(limit)).all()
        eligible = select(JobExecution).where(
            JobExecution.id.in_(ids), JobExecution.completed_at < cutoff,
            JobExecution.lease_owner.is_(None), JobExecution.lease_expires_at.is_(None),
            or_(
                JobExecution.kind.is_(None) & JobExecution.state.is_(None) & JobExecution.status.in_(LEGACY_TERMINAL),
                (JobExecution.kind != "operation_restore") & JobExecution.state.in_(SETTLED),
            ),
        ).order_by(JobExecution.id).with_for_update(skip_locked=True)
        for row in storage.scalars(eligible.execution_options(yield_per=1)):
            if _referenced(storage, row):
                continue
            if row.kind is None:
                if not row.journal and row.request_key is None and row.scheduled_for is None:
                    storage.delete(row)
                    deleted += 1
                continue
            if not _settled_document(row):
                continue
            # Preserve the polling/replay DTO, including its original result. Keep the original
            # payload for both exact-command and factory-intent comparisons on keyed admissions.
            summary = {key: row.journal[key] for key in ("result", "progress", "recovery") if key in row.journal}
            keyed = row.request_key is not None or row.scheduled_for is not None
            if row.journal != summary or (not keyed and row.payload):
                row.journal = summary
                if not keyed:
                    row.payload = {}
                compacted += 1
    return {"examined": len(ids), "deleted": deleted, "compacted": compacted,
            "next_after_id": ids[-1] if len(ids) == limit else None}
