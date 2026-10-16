"""Serve the built React app and real local API on one guarded test origin."""

import argparse
import functools
import http.client
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROXIED = {"/login", "/callback", "/logout", "/health", "/api"}
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
               "transfer-encoding", "upgrade", "content-length"}


def security_headers(root):
    config = (root / "web" / "security-headers.ts").read_text()
    match = re.search(r"export const securityHeaders\s*=\s*(\{.*?\});", config, re.S)
    if match is None:
        raise ValueError("The frontend security headers must be available to the test origin")

    def join_literals(value):
        items = json.loads(re.sub(r",\s*]", "]", value[1]))
        return json.dumps(value[2].join(items))

    literal = re.sub(r'(\[\s*(?:"(?:\\.|[^"\\])*"\s*,?\s*)+])\.join\("([^"]*)"\)', join_literals, match[1])
    return json.loads(re.sub(r",\s*}", "}", literal))


class PreviewHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, root, backend, headers, **kwargs):
        self.root, self.backend, self.security = root / "web" / "dist", backend, headers
        super().__init__(*args, **kwargs)

    def do_GET(self):
        self.respond()

    def do_HEAD(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def do_PUT(self):
        self.respond()

    def do_PATCH(self):
        self.respond()

    def do_DELETE(self):
        self.respond()

    def do_OPTIONS(self):
        self.respond()

    def respond(self):
        path = urlsplit(self.path).path
        if path in PROXIED or path.startswith("/api/"):
            self.proxy()
            return
        if self.command not in {"GET", "HEAD"}:
            self.error(405)
            return
        decoded = unquote(path)
        target = (self.root / decoded.lstrip("/")).resolve()
        if not target.is_relative_to(self.root):
            self.error(404)
            return
        if target.is_dir():
            target /= "index.html"
        if not target.is_file():
            if decoded.startswith("/assets/") or Path(decoded).suffix:
                self.error(404)
                return
            target = self.root / "index.html"
        if not target.is_file():
            self.error(503)
            return
        body = target.read_bytes()
        self.send_response(200)
        for name, value in self.security.items():
            self.send_header(name, value)
        self.send_header("Content-Type", mimetypes.guess_type(target)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def proxy(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 32 * 1024 * 1024:
                self.error(413)
                return
        except ValueError:
            self.error(400)
            return
        body = self.rfile.read(length)
        headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_HEADERS}
        connection = http.client.HTTPConnection(self.backend.hostname, self.backend.port, timeout=125)
        try:
            # One dispatch only: an unknown reply must never cause a mutation replay.
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (OSError, http.client.HTTPException):
            self.error(502)
        finally:
            connection.close()

    def error(self, status):
        body = json.dumps({"success": False, "message": "The local test request could not be served."}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, *args):
        pass


def local_origin(value):
    backend = urlsplit(value)
    if backend.scheme != "http" or backend.hostname != "127.0.0.1" or backend.port is None or (
        backend.username or backend.password or backend.path or backend.query or backend.fragment
    ):
        raise ValueError("The frontend preview requires an explicit loopback origin")
    return backend


def make_server(root, port, api_url, *, fake_origin=None):
    root = Path(root).resolve()
    backend = local_origin(api_url)
    if fake_origin is not None:
        local_origin(fake_origin)
    headers = security_headers(root)
    if fake_origin is not None:
        policy = headers["Content-Security-Policy"].split("; ")
        form = next((i for i, directive in enumerate(policy) if directive.startswith("form-action ")), None)
        if form is None:
            raise ValueError("The canonical frontend form policy is missing")
        policy[form] += " " + fake_origin
        headers["Content-Security-Policy"] = "; ".join(policy)
    handler = functools.partial(PreviewHandler, root=root, backend=backend, headers=headers)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--fake-origin", help="Explicit local OAuth fake origin for this isolated test harness")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    server = make_server(args.root, args.port, args.api_url, fake_origin=args.fake_origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
