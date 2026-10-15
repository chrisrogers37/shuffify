"""Lease-owned command execution with graceful draining and durable interruption state."""

import argparse
import logging
import signal
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from shuffify.services.operations.journal import OperationDeferred
from shuffify.services.operations.repository import LeaseLost, OperationRepository
from shuffify.services.operations.runtime import OperationResult, OperationRuntime
from shuffify.spotify.deadline import spotify_deadline

logger = logging.getLogger(__name__)


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

    def request_stop(self):
        """Prevent new claims; the active command retains its lease until it settles."""
        self._stopping.set()
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
        if self._stopping.is_set():
            return False
        with self.app.app_context():
            lease = self.repository.claim(operation_id=operation_id)
            if lease is None:
                return False
            row = self.repository.get(lease.operation_id)
            # User credential serialization also protects rotated refresh tokens between workers.
            locks = sorted({*lease.targets, f"credential:{row.user_id}"})
            with self.repository.target_locks(locks) as acquired:
                if not acquired:
                    self.repository.defer(lease)
                    return False
                with self._heartbeat(lease) as lost, spotify_deadline(self.operation_timeout):
                    return self._execute(lease, lost)

    def _execute(self, lease, lost):
        runtime = None
        try:
            runtime = OperationRuntime(self.repository, lease, api_factory=self.api_factory, lease_lost=lost)
            handler = self.handlers.get(runtime.operation.kind)
            if handler is None:
                self.repository.finish(lease, "failed", result={
                    "success": False, "error_code": "unsupported_operation", "category": "error",
                    "message": "This worker cannot run this operation.", "retryable": False,
                })
                return True
            with runtime.journal.observe():
                if lease.recovering:
                    failure = runtime.get_journal().get("runtime", {}).get("failure")
                    if failure is not None:
                        return self._record_failure(lease, runtime, result=failure["result"])
                    recovered = runtime.journal.resume()
                    if recovered["state"] == "needs_attention":
                        self.repository.finish(lease, "needs_attention", result=self._attention())
                        return True
                    if recovered["state"] == "rolled_back":
                        self.repository.finish(lease, "rolled_back", result={
                            "success": False, "error_code": "operation_rolled_back", "category": "error",
                            "message": "The interrupted operation's changes were verified as restored.",
                            "retryable": False,
                        })
                        return True
                outcome = handler(runtime)
                if isinstance(outcome, OperationResult):
                    state, result = outcome.state, outcome.result
                else:
                    result = outcome
                    state = "succeeded" if result.get("success", True) else "failed"
                if state == "failed":
                    return self._record_failure(lease, runtime, result=result)
                reconciled = runtime.journal.resume() if state == "succeeded" else {"state": state}
            if reconciled["state"] == "needs_attention":
                state, result = "needs_attention", self._attention()
            runtime.ensure_lease()
            self.repository.finish(lease, state, result=result)
            return True
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
        except Exception:
            logger.exception("Operation %s did not complete", lease.operation_id)
            try:
                return self._record_failure(lease, runtime)
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

    def _record_failure(self, lease, runtime, *, result=None):
        journal = runtime.get_journal() if runtime is not None else {}
        if journal.get("status") == "needs_attention":
            self.repository.finish(lease, "needs_attention", result=self._attention())
            return True
        state = "failed"
        result = result if result is not None else {
            "success": False, "error_code": "operation_failed", "category": "error",
            "message": "The operation did not complete.", "retryable": False,
        }
        if runtime is not None:
            recorded_failure = journal.setdefault("runtime", {}).get("failure")
            if recorded_failure is None:
                journal["runtime"]["failure"] = {"result": result}
                # Recovery must retain the abort direction even if planning its rollback is interrupted.
                self.repository.checkpoint(
                    lease, journal, state="compensating" if self._has_dispatched(journal) else None,
                )
            else:
                result = recorded_failure["result"]
        if runtime is not None and self._has_dispatched(journal):
            try:
                compensated = runtime.journal.compensate()
                state = compensated["state"]
            except (LeaseLost, OperationDeferred):
                raise
            except Exception:
                logger.exception("Operation %s compensation needs attention", lease.operation_id)
                state = "needs_attention"
            if state == "needs_attention":
                result = self._attention()
        self.repository.finish(lease, state, result=result)
        return True

    @staticmethod
    def _has_dispatched(journal):
        return any(
            batch.get("state") in {"dispatched", "acknowledged"}
            for plan in journal.get("plans", []) + journal.get("compensation", [])
            for batch in plan.get("batches", [])
        )

    @staticmethod
    def _attention():
        return {
            "success": False, "error_code": "needs_attention", "category": "error",
            "message": "Review the playlist's current state before another operation.", "retryable": False,
        }

    def drain(self):
        """Finish currently available commands, then yield to an event or known deadline."""
        count = 0
        while not self._stopping.is_set() and self.run_once():
            count += 1
        return count

    def serve(self):
        """Drain on startup and wake events, never by an idle database polling loop."""
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
    runner = OperationWorker(app)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: runner.request_stop())
    runner.drain() if args.once else runner.serve()


if __name__ == "__main__":
    main()
