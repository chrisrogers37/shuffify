"""Container startup and pre-deploy are separate process boundaries."""

import os
import runpy
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_container_startup_never_invokes_flask_or_changes_the_command(tmp_path):
    binary = tmp_path / "flask"
    binary.write_text("#!/bin/sh\necho UNEXPECTED-MIGRATION\nexit 97\n")
    binary.chmod(0o755)
    result = subprocess.run(
        [
            "sh",
            str(ROOT / "scripts/docker-entrypoint.sh"),
            sys.executable,
            "-c",
            "import sys; print(repr(sys.argv[1:]))",
            "two words",
            "literal;$value",
        ],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "['two words', 'literal;$value']"
    assert "UNEXPECTED-MIGRATION" not in result.stdout


def test_predeploy_disables_scheduler_and_sets_only_the_migration_process_marker(tmp_path):
    binary = tmp_path / "flask"
    binary.write_text('#!/bin/sh\nprintf "%s|%s|%s\\n" "$SCHEDULER_ENABLED" "$SHUFFIFY_MIGRATION_STEP" "$*"\n')
    binary.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/pre-deploy.sh")],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "SCHEDULER_ENABLED": "true",
            "SHUFFIFY_MIGRATION_STEP": "0",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and result.stdout.strip() == "false|1|db upgrade"


def test_api_uses_the_bounded_gthread_configuration_without_preload(monkeypatch):
    monkeypatch.setenv("PORT", "8456")
    config = runpy.run_path(str(ROOT / "gunicorn.conf.py"))
    assert {name: config[name] for name in ("worker_class", "workers", "threads", "timeout", "preload_app")} == {
        "worker_class": "gthread",
        "workers": 1,
        "threads": 8,
        "timeout": 30,
        "preload_app": False,
    }
    assert config["bind"] == "0.0.0.0:8456"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the unwritable home directory")
def test_api_server_clean_start_logs_no_error_with_an_unwritable_home(tmp_path):
    # The container runs as nobody, whose home directory cannot be written.
    home = tmp_path / "home"
    home.mkdir(mode=0o500)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "hello.py").write_text(
        "def app(environ, start_response):\n"
        "    start_response('200 OK', [('Content-Type', 'text/plain')])\n"
        "    return [b'ok']\n"
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {name: value for name, value in os.environ.items() if name != "XDG_RUNTIME_DIR"}
    env.update(HOME=str(home), PORT=str(port))
    process = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "--config", str(ROOT / "gunicorn.conf.py"),
         "--chdir", str(app_dir), "hello:app"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                    assert response.status == 200
                    break
            except OSError:
                if time.monotonic() > deadline or process.poll() is not None:
                    pytest.fail("The API server did not start")
                time.sleep(0.1)
        # Startup failures in background threads are logged within moments.
        time.sleep(1)
    finally:
        process.terminate()
        output, _ = process.communicate(timeout=20)
    assert "Booting worker" in output
    assert "[ERROR]" not in output, output


@pytest.mark.parametrize(
    "role,status,body,expected",
    [
        ("api", 200, b'{"status":"healthy"}', 0),
        ("api", 200, b'{"status":"degraded"}', 1),
        ("worker", 200, b'{"status":"ready"}', 0),
        ("worker", 200, b'{"status":"healthy"}', 1),
        ("api", 200, b'{"status":"ready"}', 1),
        ("worker", 503, b'{"status":"unavailable"}', 1),
        ("worker", 200, b'{"status":"unavailable"}', 1),
        ("api", 200, b"not-json", 1),
    ],
)
def test_container_health_uses_the_selected_local_service(monkeypatch, role, status, body, expected):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            assert self.headers.get("Authorization") is None
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            monkeypatch.setenv("SHUFFIFY_PROCESS_ROLE", role)
            monkeypatch.setenv("PORT", str(server.server_port))
            monkeypatch.setenv("WORKER_CONTROL_PORT", str(server.server_port))
            monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
            assert runpy.run_path(str(ROOT / "scripts/container-health.py"))["main"]() == expected
            assert paths == ["/health"]
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_worker_health_checks_its_actual_control_port_when_the_api_port_differs(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            monkeypatch.setenv("SHUFFIFY_PROCESS_ROLE", "worker")
            monkeypatch.setenv("PORT", "1")
            monkeypatch.setenv("WORKER_CONTROL_PORT", str(server.server_port))
            assert runpy.run_path(str(ROOT / "scripts/container-health.py"))["main"]() == 0
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_predeploy_returns_the_migration_failure_without_starting_a_service(tmp_path):
    binary = tmp_path / "flask"
    binary.write_text("#!/bin/sh\nexit 73\n")
    binary.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/pre-deploy.sh")],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 73
    assert result.stdout == ""


@pytest.mark.parametrize(
    "role,port,control_port",
    [("unknown", "8000", "5001"), ("api", "0", "5001"), ("api", "65536", "5001"),
     ("worker", "8000", "0"), ("worker", "8000", "not-a-port")],
)
def test_container_health_rejects_invalid_local_listener_configuration_without_network(
    monkeypatch, role, port, control_port,
):
    script = runpy.run_path(str(ROOT / "scripts/container-health.py"))
    opened = []
    script["main"].__globals__["build_opener"] = lambda *_: opened.append(True)
    monkeypatch.setenv("SHUFFIFY_PROCESS_ROLE", role)
    monkeypatch.setenv("PORT", port)
    monkeypatch.setenv("WORKER_CONTROL_PORT", control_port)
    assert script["main"]() == 1
    assert opened == []
