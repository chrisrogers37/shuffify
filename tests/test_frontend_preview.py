"""The local React origin preserves real API, cookie, and mutation boundaries."""

import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests


@pytest.fixture
def preview(tmp_path, request):
    module = importlib.import_module("tests.frontend_preview")
    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<main>React application</main>")
    (dist / "assets" / "app.js").write_text("export const active = true;")
    legal = dist / "terms"
    legal.mkdir()
    (legal / "index.html").write_text("<main>Terms of Service</main>")
    (tmp_path / "private.txt").write_text("private source")
    config = tmp_path / "web" / "security-headers.ts"
    config.write_text(
        'export const securityHeaders = {"Content-Security-Policy":["default-src \'self\'",'
        '"form-action \'self\' https://accounts.spotify.com",].join("; "),};'
    )
    calls = []

    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            self.answer()

        def do_POST(self):
            self.answer()

        def answer(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append((self.command, self.path, dict(self.headers), body))
            encoded = json.dumps({"success": self.path != "/api/missing"}).encode()
            self.send_response(404 if self.path == "/api/missing" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "session=fake-session; HttpOnly; SameSite=Lax; Path=/")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    api = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    api_url = f"http://127.0.0.1:{api.server_port}"
    options = {"fake_origin": api_url} if getattr(request, "param", False) else {}
    server = module.make_server(tmp_path, 0, api_url, **options)
    threads = [threading.Thread(target=service.serve_forever, daemon=True) for service in (api, server)]
    for thread in threads:
        thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        for service in (server, api):
            service.shutdown()
            service.server_close()
        for thread in threads:
            thread.join(2)


def test_react_routes_and_prerendered_legal_share_the_frontend_csp(preview):
    origin, calls = preview
    for path, text in [("/library", "React application"), ("/workshop/example", "React application"),
                       ("/terms", "Terms of Service")]:
        response = requests.get(origin + path, timeout=3)
        assert response.status_code == 200
        assert text in response.text
        assert response.headers["Content-Security-Policy"] == (
            "default-src 'self'; form-action 'self' https://accounts.spotify.com"
        )
    assert calls == []


@pytest.mark.parametrize("preview", [True], indirect=True)
def test_explicit_local_fake_is_allowed_only_for_form_navigation(preview):
    origin, _calls = preview
    policy = requests.get(origin + "/", timeout=3).headers["Content-Security-Policy"]
    directives = {part.split()[0]: part.split()[1:] for part in policy.split(";")}
    assert directives["default-src"] == ["'self'"]
    assert directives["form-action"][:2] == ["'self'", "https://accounts.spotify.com"]
    assert directives["form-action"][2].startswith("http://127.0.0.1:")


@pytest.mark.parametrize("url", [
    "https://example.invalid", "http://user:password@127.0.0.1:8000", "http://127.0.0.1:8000/path",
    "http://127.0.0.1:8000?next=external", "http://127.0.0.1:8000#fragment", "http://127.0.0.1",
])
def test_fake_form_exception_rejects_any_nonlocal_or_nonorigin_url(tmp_path, url):
    module = importlib.import_module("tests.frontend_preview")
    with pytest.raises(ValueError):
        module.make_server(tmp_path, 0, "http://127.0.0.1:8002", fake_origin=url)


def test_api_json_status_cookie_csrf_and_one_post_are_preserved(preview):
    origin, calls = preview
    with requests.Session() as browser:
        assert browser.get(origin + "/api/session", timeout=3).json() == {"success": True}
        response = browser.post(origin + "/api/example?review=1", json={"value": ["a", "a"]},
                                headers={"X-CSRFToken": "fake-csrf", "Idempotency-Key": "retained-key"}, timeout=3)
    assert response.status_code == 200
    assert len(calls) == 2
    method, path, headers, body = calls[1]
    assert (method, path) == ("POST", "/api/example?review=1")
    assert json.loads(body) == {"value": ["a", "a"]}
    assert headers["Cookie"] == "session=fake-session"
    assert headers["X-CSRFToken"] == "fake-csrf"
    assert headers["Idempotency-Key"] == "retained-key"
    missing = requests.get(origin + "/api/missing", timeout=3)
    assert missing.status_code == 404 and missing.json() == {"success": False}


@pytest.mark.parametrize("path", ["/assets/missing.js", "/%2e%2e/private.txt"])
def test_missing_assets_and_traversal_never_return_spa_or_source(preview, path):
    origin, calls = preview
    response = requests.get(origin + path, timeout=3)
    assert response.status_code == 404
    assert "React application" not in response.text and "private source" not in response.text
    assert calls == []
