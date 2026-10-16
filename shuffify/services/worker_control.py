"""Bounded private worker control; registration reads inspect memory only."""

import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import requests
from flask import current_app, has_app_context
from urllib3.exceptions import HTTPError

logger = logging.getLogger(__name__)
MAX_IDS = 5000
MAX_BODY = 64 * 1024
MAX_REPLY = 2 * 1024 * 1024


def valid_ids(ids):
    return isinstance(ids, list) and len(ids) <= MAX_IDS and all(type(value) is int and value > 0 for value in ids)


class WorkerControlClient:
    def __init__(self, app=None, *, url=None, token=None, timeout=None, session=None):
        config = (app or current_app).config if app is not None or has_app_context() else {}
        self.url = url if url is not None else config.get("WORKER_CONTROL_URL")
        self.token = token if token is not None else config.get("WORKER_CONTROL_TOKEN")
        self.timeout = max(0.01, min(float(timeout or config.get("WORKER_CONTROL_TIMEOUT", 0.5)), 5))
        self._session = session
        if self.url:
            parts = urlsplit(self.url)
            if (
                parts.scheme not in ("http", "https")
                or not parts.hostname
                or any((parts.username, parts.password, parts.query, parts.fragment))
            ):
                self.url = None

    def _post(self, path, body):
        if not self.url or not isinstance(self.token, str) or not self.token:
            return None
        started = time.monotonic()
        session = self._session or requests.Session()
        session.trust_env = False
        try:
            reply = session.post(
                self.url.rstrip("/") + path,
                json=body,
                headers={"Authorization": "Bearer " + self.token},
                timeout=(min(0.2, self.timeout), self.timeout),
                allow_redirects=False,
                stream=True,
            )
            if reply.status_code != 200:
                return None
            if hasattr(reply, "iter_content"):
                chunks, length = [], 0
                raw = getattr(reply, "raw", None)
                if raw is not None and hasattr(raw, "read1"):
                    # read1 returns available bytes rather than waiting to fill a large chunk.
                    # Checking between reads bounds trickled bodies as well as a silent peer.
                    stream = iter(lambda: raw.read1(65536, decode_content=True), b"")
                else:
                    stream = reply.iter_content(1)
                for chunk in stream:
                    length += len(chunk)
                    if length > MAX_REPLY or time.monotonic() - started > self.timeout:
                        return None
                    chunks.append(chunk)
                value = json.loads(b"".join(chunks))
            else:
                value = reply.json()
            return value if isinstance(value, dict) and time.monotonic() - started <= self.timeout else None
        except (requests.RequestException, HTTPError, ValueError, TypeError):
            # Never include request objects, URLs, credentials or response bodies in logs.
            logger.debug("Worker control unavailable")
            return None
        finally:
            if "reply" in locals() and hasattr(reply, "close"):
                reply.close()
            if self._session is None:
                session.close()

    def poke(self):
        return self._post("/internal/wake", {}) == {"accepted": True}

    def registrations(self, ids):
        if not valid_ids(ids):
            raise ValueError("Use at most 5000 positive schedule IDs")
        value = self._post("/internal/registrations", {"ids": ids})
        if (
            value is None
            or type(value.get("version")) is not int
            or value["version"] != 1
            or not isinstance(value.get("registrations"), dict)
            or set(value["registrations"]) != {str(value) for value in ids}
            or any(row is not None and not isinstance(row, dict) for row in value["registrations"].values())
        ):
            return None
        return value

    def health(self):
        return self._post("/internal/health", {})


class WorkerControlServer:
    """A dedicated private listener, disabled unless a shared token is configured."""

    def __init__(self, controller, worker_status, *, token, host="127.0.0.1", port=5001, wake_callback=None):
        self.controller, self.worker_status = controller, worker_status
        self._wake = wake_callback
        self.token = token if isinstance(token, str) else None
        self.host, self.port = host, port
        self._server = self._thread = None
        self._closed = False

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def url(self):
        return f"http://{self.host}:{self._server.server_port}"

    def _ready(self):
        worker = self.worker_status()
        # A replacement must serve while the incumbent holds scheduler leadership.
        # Private control reports leadership independently; only the leader schedules.
        return bool(worker.get("running") and not worker.get("stopping"))

    def start(self):
        if self._closed:
            raise RuntimeError("A closed control listener cannot restart")
        if not self.token or self._server is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(0.5)

            def handle(self):
                try:
                    super().handle()
                except OSError:
                    # Clients may time out or disconnect; never print a request/header traceback.
                    pass

            def log_message(self, *args):
                pass

            def send_json(self, status, value):
                body = json.dumps(value, allow_nan=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def do_GET(self):
                if self.path == "/health":
                    ready = owner._ready()
                    return self.send_json(200 if ready else 503, {"status": "ready" if ready else "unavailable"})
                self.send_json(404, {"error": "not_found"})

            def do_POST(self):
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied.encode(), ("Bearer " + owner.token).encode()):
                    return self.send_json(401, {"error": "unauthorized"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= MAX_BODY:
                        return self.send_json(413, {"error": "invalid_body"})
                    body = json.loads(self.rfile.read(length)) if length else {}
                    if not isinstance(body, dict):
                        raise ValueError()
                    if self.path == "/internal/wake" and body == {}:
                        owner.controller.request_refresh()
                        if owner._wake is not None:
                            owner._wake()
                        return self.send_json(200, {"accepted": True})
                    if self.path == "/internal/health" and body == {}:
                        return self.send_json(
                            200,
                            {"worker": owner.worker_status(), "scheduler": owner.controller.registration_snapshot([])},
                        )
                    if self.path == "/internal/registrations" and set(body) == {"ids"} and valid_ids(body["ids"]):
                        return self.send_json(200, owner.controller.registration_snapshot(body["ids"]))
                    return self.send_json(400, {"error": "invalid_request"})
                except (ValueError, OSError):
                    return self.send_json(400, {"error": "invalid_body"})
                except Exception:
                    logger.warning("Worker control request failed")
                    return self.send_json(503, {"error": "unavailable"})

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05), name="worker-control", daemon=True
        )
        self._thread.start()
        return self

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exception):
        self.close()
