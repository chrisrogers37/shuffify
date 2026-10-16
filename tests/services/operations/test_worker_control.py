"""Private memory control is authenticated, bounded and absent-safe."""

import importlib
import time
from types import SimpleNamespace

import pytest
import requests


def module():
    return importlib.import_module("shuffify.services.worker_control")


class Controller:
    def __init__(self):
        self.refreshes = self.wakes = 0
        self.ready = True
        self.ids = []

    def request_refresh(self):
        self.refreshes += 1

    def wake(self):
        self.wakes += 1

    def registration_snapshot(self, ids):
        self.ids.append(ids)
        return {
            "version": 1,
            "leader": self.ready,
            "scheduler_running": self.ready,
            "worker_id": "private-worker",
            "generation": "private-generation",
            "observed_at": "2026-10-15T00:00:00+00:00",
            "effective_timezone": "UTC",
            "registrations": {str(value): None for value in ids},
        }


@pytest.fixture
def private_server():
    controller = Controller()
    state = {"running": True, "stopping": False}
    with module().WorkerControlServer(
        controller, lambda: state, token="test-secret", port=0, wake_callback=controller.wake
    ) as server:
        yield controller, state, server


def test_private_control_auth_health_and_batched_memory_read(private_server):
    controller, state, server = private_server
    with requests.Session() as http:
        http.trust_env = False
        health = http.get(server.url + "/health", timeout=1)
        assert health.status_code == 200 and health.json() == {"status": "ready"}
        assert "private" not in health.text and "secret" not in health.text
        for path in ["/internal/wake", "/internal/registrations", "/internal/health"]:
            assert http.post(server.url + path, json={}, timeout=1).status_code == 401
        client = module().WorkerControlClient(url=server.url, token="test-secret")
        assert client.poke() is True and controller.refreshes == controller.wakes == 1
        snapshot = client.registrations([1, 2])
        assert snapshot["registrations"] == {"1": None, "2": None}
        assert controller.ids[-1] == [1, 2]
        private = client.health()
        assert private["worker"]["running"] and private["scheduler"]["leader"]
        state["stopping"] = True
        assert http.get(server.url + "/health", timeout=1).status_code == 503


def test_invalid_auth_and_payload_never_calls_memory_controller(private_server):
    controller, _, server = private_server
    with requests.Session() as http:
        http.trust_env = False
        for ids in [[True], [0], ["1"], list(range(1, 5002)), None]:
            reply = http.post(
                server.url + "/internal/registrations",
                json={"ids": ids},
                headers={"Authorization": "Bearer test-secret"},
                timeout=1,
            )
            assert reply.status_code in (400, 413)
        assert controller.ids == []
        assert (
            http.post(
                server.url + "/internal/wake", json={}, headers={"Authorization": "Bearer wrong"}, timeout=1
            ).status_code
            == 401
        )
        assert controller.refreshes == 0


def test_missing_wrong_and_slow_control_are_unavailable(private_server):
    _, _, server = private_server
    assert module().WorkerControlClient(url=server.url, token=None).registrations([1]) is None
    assert module().WorkerControlClient(url=None, token="secret").registrations([1]) is None
    assert module().WorkerControlClient(url=server.url, token="wrong").registrations([1]) is None

    class SlowSession:
        trust_env = True

        def post(self, *args, **kwargs):
            assert kwargs["timeout"][1] <= 0.05 and kwargs["allow_redirects"] is False
            time.sleep(0.06)
            return SimpleNamespace(status_code=200, content=b"{}", json=lambda: {})

    started = time.monotonic()
    assert (
        module()
        .WorkerControlClient(url=server.url, token="secret", timeout=0.05, session=SlowSession())
        .registrations([1])
        is None
    )
    assert time.monotonic() - started < 0.15


def test_control_server_stops_and_never_restarts(private_server):
    _, _, server = private_server
    server.close()
    assert not server.running
    with pytest.raises(RuntimeError):
        server.start()


def test_live_standby_passes_deployment_health_without_claiming_scheduler_leadership(private_server):
    controller, state, server = private_server
    controller.ready = False
    with requests.Session() as http:
        http.trust_env = False
        reply = http.get(server.url + "/health", timeout=1)
        assert reply.status_code == 200 and reply.json() == {"status": "ready"}
        assert controller.ids == []
        client = module().WorkerControlClient(url=server.url, token="test-secret")
        health = client.health()
        assert health["worker"]["running"] is True
        assert health["scheduler"]["leader"] is False
        assert health["scheduler"]["scheduler_running"] is False
        assert client.registrations([1])["registrations"] == {"1": None}
        state["scheduler_enabled"] = False
        assert http.get(server.url + "/health", timeout=1).json() == {"status": "ready"}
        state["running"] = False
        reply = http.get(server.url + "/health", timeout=1)
        assert reply.status_code == 503 and reply.json() == {"status": "unavailable"}
    with module().WorkerControlServer(controller, lambda: state, token=None, port=0) as disabled:
        assert not disabled.running


def test_actual_private_read_timeout_is_bounded_and_does_not_leak_token(private_server, caplog):
    import threading

    controller, _, server = private_server
    entered, release = threading.Event(), threading.Event()
    original = controller.registration_snapshot

    def blocked(ids):
        entered.set()
        release.wait(0.2)
        return original(ids)

    controller.registration_snapshot = blocked
    started = time.monotonic()
    try:
        assert (
            module().WorkerControlClient(url=server.url, token="test-secret", timeout=0.03).registrations([1]) is None
        )
        assert entered.is_set() and time.monotonic() - started < 0.15
    finally:
        release.set()
    assert "test-secret" not in caplog.text


def test_trickled_response_body_cannot_extend_the_private_read_budget():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.dumps({"version": 1, "registrations": {"1": None}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for byte in body:
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                except OSError:
                    break
                time.sleep(0.01)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        client = module().WorkerControlClient(
            url=f"http://127.0.0.1:{server.server_port}", token="test-secret", timeout=0.04
        )
        assert client.registrations([1]) is None
        assert time.monotonic() - started < 0.15
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)
