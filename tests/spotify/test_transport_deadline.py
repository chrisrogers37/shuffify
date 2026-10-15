"""Real sockets cannot extend a Spotify deadline by trickling headers or JSON."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shuffify.services.source_resolver.public_scraper_pathway import PublicScraperPathway
from shuffify.spotify.auth import SpotifyAuthManager
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.deadline import SpotifyDeadlineError, spotify_deadline
from shuffify.spotify.http_client import SpotifyHTTPClient


@pytest.fixture
def trickle_server(monkeypatch):
    disconnected = threading.Event()
    body = b'{"access_token":"fixture","token_type":"Bearer","expires_in":3600}'

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            try:
                if self.path == '/headers':
                    self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                    for _ in range(32):
                        self.wfile.write(b'x')
                        self.wfile.flush()
                        time.sleep(0.025)
                    self.wfile.write(b'\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.025)
            except OSError:
                disconnected.set()
            finally:
                self.close_connection = True

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    try:
        yield f'http://127.0.0.1:{server.server_port}', disconnected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.parametrize('provider', ['api', 'oauth', 'public'])
@pytest.mark.parametrize('part', ['headers', 'body'])
def test_trickling_response_stops_within_whole_deadline_and_closes_socket(trickle_server, provider, part):
    base_url, disconnected = trickle_server
    url = base_url + '/' + part
    client = SpotifyHTTPClient('fixture-token')
    manager = SpotifyAuthManager(SpotifyCredentials(
        client_id='fixture-client', client_secret='fixture-secret',
        redirect_uri='https://example.invalid/callback',
    ))
    manager._TOKEN_URL = url
    started = time.monotonic()
    try:
        with spotify_deadline(0.12):
            with pytest.raises(SpotifyDeadlineError):
                if provider == 'api':
                    client._request_url('GET', url)
                elif provider == 'oauth':
                    manager.exchange_code('fixture-code')
                else:
                    PublicScraperPathway()._do_scrape(url, 'alice', 'Fixture')
        assert time.monotonic() - started < 0.5
        assert disconnected.wait(timeout=0.5), 'timed-out response socket must be closed'
    finally:
        client.close()
