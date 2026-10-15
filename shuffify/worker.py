"""Lease-owned command execution with graceful draining and durable interruption state."""

import argparse
import json
import logging
import signal
import threading
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone

from shuffify.services.operations.control import propagate_control
from shuffify.services.operations.journal import NeedsAttention, OperationAborted, OperationDeferred
from shuffify.services.operations.repository import LeaseLost, OperationRepository
from shuffify.services.operations.runtime import OperationResult, OperationRuntime
from shuffify.spotify.deadline import spotify_deadline
from shuffify.spotify.observation import observe_mutations

logger = logging.getLogger(__name__)


class TerminalPending(RuntimeError):
    """A settled external outcome awaits a retry of its fenced local publication."""


class OperationWorker:
    """Run accepted commands while retaining every target lock across commits."""

    def __init__(self, app, *, repository=None, handlers=None, api_factory=None,
                 heartbeat_interval=10, operation_timeout=240):
        self.app = app
        with app.app_context():
            self.repository = repository or OperationRepository()
        self.handlers = dict(handlers or {})
        self.api_factory = api_factory
        self.heartbeat_interval = heartbeat_interval
        self.operation_timeout = operation_timeout
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._scheduler_controller = self._control_server = None
        self._serving = False

    def request_stop(self):
        """Prevent new claims; the active command retains its lease until it settles."""
        self._stopping.set()
        if self._scheduler_controller is not None:
            self._scheduler_controller.request_stop()
        self._wake.set()

    def wake(self):
        self._wake.set()

    @contextmanager
    def _heartbeat(self, lease):
        finished, lost = threading.Event(), threading.Event()

        def renew():
            while not finished.wait(self.heartbeat_interval):
                try:
                    self.repository.renew(lease)
                except Exception:
                    lost.set()
                    logger.exception("Operation %s lost its lease heartbeat", lease.operation_id)
                    return

        thread = None
        if self.heartbeat_interval > 0:
            thread = threading.Thread(target=renew, name="operation-heartbeat", daemon=True)
            thread.start()
        try:
            yield lost
        finally:
            finished.set()
            if thread is not None:
                thread.join(timeout=5)

    def run_once(self, operation_id=None):
        """Claim one command; process interruptions deliberately leave a recoverable lease."""
        completed, _claimed = self._run_one(operation_id)
        return completed

    def _run_one(self, operation_id=None):
        """Distinguish deferred work from an empty queue so other targets can drain."""
        if self._stopping.is_set():
            return False, False
        with self.app.app_context():
            lease = self.repository.claim(operation_id=operation_id)
            if lease is None:
                return False, False
            row = self.repository.get(lease.operation_id)
            # User credential serialization also protects rotated refresh tokens between workers.
            locks = sorted({*lease.targets, f"credential:{row.user_id}"})
            with self.repository.target_locks(locks) as acquired:
                if not acquired:
                    self.repository.defer(lease)
                    return False, True
                with self._heartbeat(lease) as lost, spotify_deadline(self.operation_timeout):
                    return self._execute(lease, lost), True

    def _execute(self, lease, lost):
        runtime = None
        try:
            runtime = OperationRuntime(self.repository, lease, api_factory=self.api_factory, lease_lost=lost)
            handler = self.handlers.get(runtime.operation.kind)
            if handler is None:
                self._finish(lease, runtime, "failed", result={
                    "success": False, "error_code": "unsupported_operation", "category": "error",
                    "message": "This worker cannot run this operation.", "retryable": False,
                })
                return True
            with runtime.journal.observe():
                if lease.recovering:
                    document = runtime.get_journal()
                    terminal = document.get("runtime", {}).get("terminal_finalization")
                    if terminal is not None:
                        self._recover_terminal(lease, runtime, terminal)
                        return True
                    failure = document.get("runtime", {}).get("failure")
                    if failure is not None:
                        return self._record_failure(lease, runtime, result=failure["result"])
                    if document.get("abort"):
                        return self._record_failure(lease, runtime, result=document["abort"].get("result"))
                    runtime.check_pending_control()
                    self._protect_recovery(runtime, document)
                    recovered = runtime.journal.resume()
                    if recovered["state"] == "needs_attention":
                        self._finish(lease, runtime, "needs_attention", result=self._attention())
                        return True
                    if recovered["state"] == "rolled_back":
                        self._finish(lease, runtime, "rolled_back", result={
                            "success": False, "error_code": "operation_rolled_back", "category": "error",
                            "message": "The interrupted operation's changes were verified as restored.",
                            "retryable": False,
                        })
                        return True
                outcome = handler(runtime)
                if runtime.verified_step_failure is not None:
                    if isinstance(outcome, OperationResult) and outcome.state == "needs_attention":
                        body = (self._attention(outcome.result, invalidate_all=True)
                                if runtime.verified_step_conflicted else outcome.result)
                        self._finish(lease, runtime, "needs_attention", result=body)
                        return True
                    return self._record_failure(lease, runtime)
                runtime.check_pending_control()
                if isinstance(outcome, OperationResult):
                    state, result = outcome.state, outcome.result
                else:
                    result = outcome
                    state = "succeeded" if result.get("success", True) else "failed"
                if state == "failed":
                    return self._record_failure(lease, runtime, result=result)
                if state == "succeeded" and self._all_acknowledged(runtime.journal.doc):
                    # The handler has finished every provider batch. Persist its
                    # terminal direction before another reconciliation checkpoint.
                    try:
                        self._save_terminal(runtime, state, result, done=False)
                        self._recover_terminal(lease, runtime, {"state": state, "result": result, "done": False})
                    except (LeaseLost, OperationDeferred, TerminalPending):
                        raise
                    except Exception as error:
                        with observe_mutations(None):
                            propagate_control(error)
                        raise TerminalPending() from error
                    return True
                reconciled = runtime.journal.resume() if state == "succeeded" else {"state": state}
            if reconciled["state"] == "needs_attention" and state != "needs_attention":
                state, result = "needs_attention", self._attention(result)
            self._finish(lease, runtime, state, result=result)
            return True
        except TerminalPending:
            logger.warning("Operation %s awaits terminal database publication", lease.operation_id)
            return False
        except OperationDeferred as deferred:
            try:
                delay = max(0.001, (deferred.next_attempt_at - datetime.now(timezone.utc)).total_seconds())
                self.repository.defer(lease, delay_seconds=delay)
                return False
            except LeaseLost:
                return False
        except LeaseLost:
            logger.warning("Operation %s stopped after losing its lease", lease.operation_id)
            return False
        except Exception as error:
            logger.exception("Operation %s did not complete", lease.operation_id)
            try:
                verified_abort = verified_attention = False
                try:
                    with observe_mutations(None):
                        propagate_control(error)
                except OperationAborted:
                    verified_abort = True
                except NeedsAttention:
                    verified_attention = True
                result = getattr(error, "result", None)
                return self._record_failure(lease, runtime, result=result, verified_abort=verified_abort,
                                            verified_attention=verified_attention)
            except TerminalPending:
                logger.warning("Operation %s awaits terminal database publication", lease.operation_id)
                return False
            except OperationDeferred as deferred:
                try:
                    delay = max(0.001, (deferred.next_attempt_at - datetime.now(timezone.utc)).total_seconds())
                    self.repository.defer(lease, delay_seconds=delay)
                    return False
                except LeaseLost:
                    return False
            except LeaseLost:
                logger.warning("Operation %s lost its lease while reporting failure", lease.operation_id)
                return False
        finally:
            if runtime is not None:
                runtime.close()

    def _record_failure(self, lease, runtime, *, result=None, verified_abort=False, verified_attention=False):
        if runtime is not None and runtime.verified_step_failure is not None and not verified_abort:
            applied = runtime.verified_step_failure.applied
            public = applied.get("public_result") if isinstance(applied, dict) else None
            if verified_attention or runtime.verified_step_conflicted:
                self._finish(lease, runtime, "needs_attention",
                             result=self._attention(result or public, invalidate_all=True))
                return True
            self._finish(lease, runtime, "needs_attention", result={
                **(deepcopy(public) if isinstance(public, dict) else {}),
                "success": False, "status": "unknown", "error_code": "verified_step_publication_failed",
                "category": "error",
                "message": "Playlist changes were verified, but their local records could not be confirmed. "
                           "Review the result before another operation.",
                "retryable": False,
            })
            return True
        journal = runtime.get_journal() if runtime is not None else {}
        if result is None:
            result = journal.get("runtime", {}).get("failure", {}).get("result")
        if result is None and journal.get("abort"):
            result = journal["abort"].get("result")
        if journal.get("status") == "needs_attention":
            self._finish(lease, runtime, "needs_attention", result=self._attention(result))
            return True
        state = "failed"
        result = result if result is not None else {
            "success": False, "error_code": "operation_failed", "category": "error",
            "message": "The operation did not complete.", "retryable": False,
        }
        has_effects = self._has_dispatched(journal)
        if runtime is not None and journal.get("boundaries") is not None:
            has_effects = runtime.journal.compensation_has_effects(journal)
        if runtime is not None:
            recorded_failure = journal.setdefault("runtime", {}).get("failure")
            if recorded_failure is None:
                journal["runtime"]["failure"] = {"result": result}
                # Recovery must retain the abort direction even if planning its rollback is interrupted.
                self.repository.checkpoint(
                    lease, journal, state="compensating" if has_effects else None,
                )
            else:
                result = recorded_failure["result"]
        if runtime is not None and (has_effects or journal.get("boundaries") is not None):
            try:
                compensated = runtime.journal.compensate()
                state = compensated["state"]
            except (LeaseLost, OperationDeferred):
                raise
            except Exception:
                logger.exception("Operation %s compensation needs attention", lease.operation_id)
                state = "needs_attention"
            if state == "needs_attention":
                result = self._attention(result)
        self._finish(lease, runtime, state, result=result)
        return True

    @staticmethod
    def _all_acknowledged(document):
        return all(item["state"] == "acknowledged" for plan in document.get("plans", [])
                   for item in plan["batches"])

    def _protect_recovery(self, runtime, document):
        """Retain freshly proved recovered effects until their own acceptance."""
        boundaries = document.get("boundaries", {})
        current = boundaries.get("current")
        accepted = boundaries.get("accepted", [])
        boundary = current or (accepted[-1] if accepted else None)
        if (document.get("phase") != "forward" or not document.get("plans")
                or boundary is None or not self._all_acknowledged(document)):
            return
        resources = []
        for resource, stored in sorted(document.get("resources", {}).items()):
            plans = [item for item in document["plans"] if item["resource"] == resource]
            desired = plans[-1]["desired"] if plans else stored["baseline"]
            metadata = stored.get("scope") == "metadata"
            mutation = ("metadata" if metadata else "track_order"
                        if Counter(desired["track_uris"]) == Counter(stored["baseline"]["track_uris"])
                        else "track_membership")
            fact = {
                "resource": resource, "resource_name": desired.get("metadata", {}).get("name"),
                "step": boundary["name"], "mutation_type": mutation,
                "status": "complete" if plans else "not_required", "dispatched": any(p["batches"] for p in plans),
                "affected_count": None if plans else 0, "error_code": None, "retryable": False,
                "verification": "metadata" if metadata else "ordered",
            }
            fact["current_metadata" if metadata else "current_track_uris"] = deepcopy(
                desired.get("metadata", {}) if metadata else desired["track_uris"],
            )
            resources.append(fact)
        public = {"operation": {
            "kind": runtime.operation.kind, "aggregate_status": "unknown", "resources": resources,
            "bookkeeping": [], "error_code": "verified_step_publication_failed", "retryable": False,
        }}
        runtime.journal.verify_terminal(on_verified=lambda: runtime.protect_verified(
            boundary["name"], public, resuming=True,
        ))

    def _finish(self, lease, runtime, state, *, result):
        """Finalize local effects, then publish history and the settled result together.

        A terminal candidate is durable before any callback. Persistence errors
        deliberately leave its lease recoverable and must never enter failure
        compensation solely because a verified outcome could not be published.
        """
        try:
            if runtime is not None:
                runtime.ensure_lease()
                state, result = self._finalize(runtime, state, result)
                retained = state == "failed" and bool(runtime.get_journal().get("boundaries", {}).get("accepted"))
                if state in {"succeeded", "rolled_back"} or state == "failed" and retained:
                    from shuffify.services.operations.history import HistoryError

                    try:
                        runtime.stage_history(state)
                    except HistoryError:
                        logger.exception("Operation %s could not confirm its history", lease.operation_id)
                        state = "needs_attention"
                        result = dict(result, success=False, error_code="history_confirmation_failed", category="error",
                                      message="The playlist was verified, but its saved history needs review.",
                                      retryable=False)
                        # The changed terminal state needs its own domain receipt;
                        # cached success can never erase this downgrade on restart.
                        state, result = self._finalize(runtime, state, result)
            operation = result.get("operation")
            code = (result.get("error_code") or result.get("skipped_reason")
                    or (operation.get("error_code") if isinstance(operation, dict) else None))
            legacy_status = "skipped" if (state == "failed" and result.get("status") == "skipped"
                                          and code == "drip_disabled") else None
            self.repository.finish(lease, state, result=result, legacy_status=legacy_status,
                                   error_message=self._error_message(state, result),
                                   tracks_added=self._count(result.get("tracks_added")),
                                   tracks_total=self._count(result.get("tracks_total")))
        except (LeaseLost, OperationDeferred):
            raise
        except Exception as error:
            logger.exception("Operation %s terminal publication is pending", lease.operation_id)
            raise TerminalPending() from error

    def _recover_terminal(self, lease, runtime, terminal):
        """Revalidate a terminal candidate without ever re-entering failure compensation."""
        try:
            # Read-only reconciliation is allowed through an abort; an outage
            # leaves the candidate recoverable instead of authorizing a write.
            recovered = runtime.journal.verify_terminal()
            state, result = terminal["state"], terminal["result"]
            if (recovered["state"] == "needs_attention"
                    or state == "succeeded" and (runtime.journal.doc.get("phase") != "forward"
                                                  or not self._all_acknowledged(runtime.journal.doc))):
                changed = self._attention(result, invalidate_all=True)
                if state != "needs_attention" or changed != result:
                    # Fresh reconciliation can invalidate resource facts even
                    # when the candidate already required local-record review.
                    # Domain receipts remain idempotent; the public projection
                    # must be finalized from this newly verified uncertainty.
                    self._save_terminal(runtime, "needs_attention", changed, done=False)
                state, result = "needs_attention", changed
            elif state == "succeeded":
                document = runtime.get_journal()
                if document.get("status") != "succeeded":
                    # A verified no-op can still have a ready journal. Publish
                    # its settled state without dispatching a forward plan.
                    document.update(status="succeeded", phase="forward",
                                    recovery={"available": False, "action": None, "reason": None})
                    runtime.checkpoint(document)
            self._finish(lease, runtime, state, result=result)
        except (LeaseLost, OperationDeferred, TerminalPending):
            raise
        except Exception as error:
            logger.exception("Operation %s terminal verification is pending", lease.operation_id)
            raise TerminalPending() from error

    @staticmethod
    def _count(value):
        return value if type(value) is int and value >= 0 else None

    @staticmethod
    def _error_message(state, result):
        if state != "succeeded":
            for name in ("error", "message"):
                value = result.get(name)
                if isinstance(value, str) and value:
                    return value
        return None

    @staticmethod
    def _json_result(result):
        if not isinstance(result, dict):
            raise ValueError("A terminal finalizer must return a result object.")
        return json.loads(json.dumps(result, allow_nan=False))

    def _save_terminal(self, runtime, state, result, *, done):
        document = runtime.get_journal()
        document.setdefault("runtime", {})["terminal_finalization"] = {
            "state": state, "result": self._json_result(result), "done": done,
        }
        # Do not use forward Runtime.local_step/control: failed and aborted
        # journals still require their independent, DB-only terminal receipts.
        self.repository.checkpoint(runtime.lease, document)

    def _finalize(self, runtime, state, result):
        marker = runtime.get_journal().get("runtime", {}).get("terminal_finalization")
        if marker is not None and marker["state"] == state:
            result = self._json_result(marker["result"])
            if marker["done"]:
                return state, result
        else:
            self._save_terminal(runtime, state, result, done=False)
        original = self._json_result(result)
        callback = getattr(self.handlers.get(runtime.operation.kind), "finalize", None)
        if callback is not None:
            try:
                # Trusted domain finalizers perform DB work only. They use
                # repository.local_step(lease, state-qualified name, callback)
                # for replay-safe records and never construct or use Spotify.
                outcome = callback(runtime, state, deepcopy(original))
                if (not isinstance(outcome, OperationResult)
                        or outcome.state not in {state, "needs_attention"}):
                    raise ValueError("A terminal finalizer may only retain state or require attention.")
                result = self._json_result(outcome.result)
                state = outcome.state
            except (LeaseLost, OperationDeferred):
                raise
            except Exception:
                logger.exception("Operation %s local finalization needs review", runtime.operation.id)
                state, result = "needs_attention", self._finalization_attention(original)
        self._save_terminal(runtime, state, result, done=True)
        return state, result

    @staticmethod
    def _finalization_attention(result):
        """Local record failure cannot invalidate already verified Spotify facts."""
        retained = deepcopy(result)
        if isinstance(retained.get("operation"), dict):
            retained["operation"].update(aggregate_status="unknown", error_code="terminal_finalization_failed",
                                         retryable=False)
        return {**retained, "success": False, "error_code": "terminal_finalization_failed", "category": "error",
                "message": "The operation's local records could not be confirmed. Its playlist result is retained.",
                "retryable": False}

    @staticmethod
    def _has_dispatched(journal):
        return any(
            batch.get("state") in {"dispatched", "acknowledged"}
            for plan in journal.get("plans", []) + journal.get("compensation", [])
            for batch in plan.get("batches", [])
        )

    @staticmethod
    def _attention(result=None, *, invalidate_all=False):
        retained = deepcopy(result) if isinstance(result, dict) else {}
        operation = retained.get("operation")
        if isinstance(operation, dict):
            operation.update(aggregate_status="unknown", error_code="needs_attention", retryable=False)
            for resource in operation.get("resources", []):
                if resource.get("dispatched"):
                    resource.update(status="unknown", affected_count=None, retryable=False)
                if resource.get("dispatched") or invalidate_all:
                    for name in ("current_track_uris", "current_metadata", "loaded_at"):
                        resource.pop(name, None)
            for entry in operation.get("bookkeeping", []):
                if entry.get("kind") == "session_history":
                    entry.update(status="unknown", affected_count=None, error_code="needs_attention")
        if invalidate_all:
            for name in ("current_track_uris", "current_tracks", "saved_track_uris", "loaded_at", "playlist_state",
                         "playlist", "public"):
                retained.pop(name, None)
            if "target_status" in retained:
                retained["target_status"] = "unknown"
        return {**retained,
            "success": False, "error_code": "needs_attention", "category": "error",
            "message": "Review the playlist's current state before another operation.", "retryable": False,
        }

    def drain(self):
        """Finish currently available commands, then yield to an event or known deadline."""
        count = 0
        while not self._stopping.is_set():
            completed, claimed = self._run_one()
            if completed:
                count += 1
            if not claimed:
                break
        return count

    def serve(self):
        """Own queue, memory scheduler and private control until graceful drain ends."""
        from shuffify.services.operations.wake import PostgresWakeListener
        from shuffify.services.worker_control import WorkerControlServer
        from shuffify.services.worker_scheduler import WorkerScheduler

        if self._stopping.is_set():
            return
        controller = WorkerScheduler(self.app, self.repository, wake_callback=self.wake)
        self._scheduler_controller = controller
        self._control_server = WorkerControlServer(
            controller, self._control_status, token=self.app.config.get("WORKER_CONTROL_TOKEN"),
            host=self.app.config.get("WORKER_CONTROL_HOST", "127.0.0.1"),
            port=self.app.config.get("WORKER_CONTROL_PORT", 5001), wake_callback=self.wake,
        )
        self._serving = True
        try:
            with controller, self._control_server, PostgresWakeListener(
                self.repository, self.wake, schedule_callback=controller.request_refresh,
            ):
                self._serve_waiting()
        finally:
            self._serving = False
            controller.close()

    def _control_status(self):
        return {"running": self._serving, "stopping": self._stopping.is_set(),
                "scheduler_enabled": bool(self.app.config.get("SCHEDULER_ENABLED", True))}

    def _serve_waiting(self):
        """Wake hints trigger durable reads; lease deadlines arrange their own wake."""
        self._wake.set()
        timeout = None
        while not self._stopping.is_set():
            self._wake.wait(timeout=timeout)
            self._wake.clear()
            if not self._stopping.is_set():
                self.drain()
                ready_at = self.repository.next_ready_at()
                timeout = max(0, (ready_at - datetime.now(timezone.utc)).total_seconds()) if ready_at else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Drain currently available operations and exit")
    args = parser.parse_args(argv)
    from shuffify import create_app

    app = create_app(start_scheduler=False)
    from shuffify.services.operations.commands.registry import COMMAND_HANDLERS

    runner = OperationWorker(app, handlers=COMMAND_HANDLERS)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: runner.request_stop())
    logger.info("Operation worker started")
    runner.drain() if args.once else runner.serve()


if __name__ == "__main__":
    main()
