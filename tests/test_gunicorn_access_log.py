"""Access logs retain request diagnostics without OAuth or header contents."""

import base64
import io
import logging
import runpy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from gunicorn.config import Config
from gunicorn.glogging import Logger

ROOT = Path(__file__).resolve().parents[1]


def render_access_line(method, path, status, protocol):
    settings = runpy.run_path(str(ROOT / "gunicorn.conf.py"))
    config = Config()
    for name, value in settings.items():
        if name in config.settings:
            config.set(name, value)

    output = io.StringIO()
    sink = logging.Logger("access-format-test")
    sink.addHandler(logging.StreamHandler(output))
    # Use Gunicorn's actual access/atoms/escaping pipeline with an isolated sink.
    logger = Logger.__new__(Logger)
    logger.cfg = config
    logger.access_log = sink

    authorization = "Basic " + base64.b64encode(b"CANARY_USER:CANARY_PASSWORD").decode()
    query = "code=CANARY_CODE&state=CANARY_STATE"
    referer = "https://accounts.example.invalid/authorize?token=CANARY_REFERER"
    headers = {
        "Authorization": authorization,
        "Cookie": "session=CANARY_COOKIE",
        "Referer": referer,
        "User-Agent": "CANARY_AGENT",
    }
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "RAW_URI": path + "?" + query,
        "QUERY_STRING": query,
        "SERVER_PROTOCOL": protocol,
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_AUTHORIZATION": authorization,
        "HTTP_COOKIE": headers["Cookie"],
        "HTTP_REFERER": referer,
        "HTTP_USER_AGENT": headers["User-Agent"],
    }
    response = SimpleNamespace(status=status, sent=19, headers={"Set-Cookie": "CANARY_SET_COOKIE"})
    logger.access(response, headers, environ, timedelta(microseconds=123456))
    return output.getvalue().strip(), authorization


@pytest.mark.parametrize(
    "method,path,status,protocol",
    [
        ("GET", "/callback", "302 Found", "HTTP/1.1"),
        ("POST", "/login", "400 Bad Request", "HTTP/1.1"),
        ("GET", "/api/session", "200 OK", "HTTP/2"),
    ],
)
def test_access_log_keeps_path_and_timing_without_query_or_headers(method, path, status, protocol):
    line, authorization = render_access_line(method, path, status, protocol)

    for sensitive in (
        "CANARY_CODE", "CANARY_STATE", "CANARY_USER", "CANARY_PASSWORD", "CANARY_COOKIE",
        "CANARY_REFERER", "CANARY_AGENT", "CANARY_SET_COOKIE", authorization,
    ):
        assert sensitive not in line
    assert line == f'"{method} {path} {protocol}" {status.split()[0]} 19 0.123456'
