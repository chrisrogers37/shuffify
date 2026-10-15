"""Only serving operation workers own the scheduler/control listener lifecycle."""

import threading
import time

from shuffify.services.worker_control import WorkerControlClient
from shuffify.worker import OperationWorker
from tests.services.operations.test_scheduled_admission import schedule as schedule


def test_serve_owns_memory_scheduler_control_and_stops_admissions(schedule, db_app):
    db_app.config.update(SCHEDULER_ENABLED=True, WORKER_CONTROL_TOKEN="test-secret", WORKER_CONTROL_PORT=0)
    runner = OperationWorker(db_app, heartbeat_interval=0)
    thread = threading.Thread(target=runner.serve)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            server = getattr(runner, "_control_server", None)
            if server is not None and server.running and runner._scheduler_controller.ready.is_set():
                break
            time.sleep(0.005)
        assert server is not None and server.running
        client = WorkerControlClient(url=server.url, token="test-secret")
        value = client.registrations([schedule.id])
        assert value["scheduler_running"] and value["registrations"][str(schedule.id)]["next_run_at"]
        controller = runner._scheduler_controller
        runner.request_stop()
        assert not controller.registration_snapshot([schedule.id])["leader"]
        thread.join(3)
        assert not thread.is_alive() and not server.running
        assert not controller._thread.is_alive()
    finally:
        runner.request_stop()
        thread.join(3)


def test_drain_once_never_starts_scheduler_or_private_server(schedule, db_app, monkeypatch):
    db_app.config["SCHEDULER_ENABLED"] = True

    def forbidden(*args, **kwargs):
        raise AssertionError("One-shot drain must not schedule new work")

    monkeypatch.setattr("shuffify.services.worker_scheduler.WorkerScheduler.start", forbidden)
    monkeypatch.setattr("shuffify.services.worker_control.WorkerControlServer.start", forbidden)
    assert OperationWorker(db_app).drain() == 0
