"""Strict CSRF uses the configured browser-facing HTTPS origin across a proxy."""

import pytest
from flask import jsonify
from flask_wtf.csrf import generate_csrf

FRONTEND = "https://frontend.example.invalid"
BACKEND = "https://backend.example.invalid"


@pytest.fixture
def origin_app(monkeypatch):
    from config import TestConfig
    from shuffify import create_app

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "fictional-client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "fictional-secret")
    monkeypatch.setattr(TestConfig, "PUBLIC_ORIGIN", FRONTEND, raising=False)
    monkeypatch.setattr(TestConfig, "WTF_CSRF_ENABLED", True)
    monkeypatch.setattr(TestConfig, "REDIS_URL", "")
    app = create_app("testing")
    entered = []

    @app.get("/api/__csrf_token")
    def token():
        return jsonify(csrf_token=generate_csrf())

    @app.route("/api/__csrf_review", methods=["POST", "PATCH", "DELETE"])
    def review():
        entered.append(True)
        return jsonify(success=True)

    return app, entered


def verified_client(app):
    client = app.test_client()
    response = client.get(
        "/api/__csrf_token",
        base_url=BACKEND,
        headers={
            "X-Forwarded-Host": "backend.example.invalid",
            "X-Forwarded-Proto": "https",
        },
    )
    assert response.status_code == 200
    return client, response.get_json()["csrf_token"]


def post(client, token, *, referrer=FRONTEND + "/library", forwarded_host="backend.example.invalid", method="POST"):
    headers = {"X-Forwarded-Host": forwarded_host, "X-Forwarded-Proto": "https"}
    if token is not None:
        headers["X-CSRFToken"] = token
    if referrer is not None:
        headers["Referer"] = referrer
    return client.open("/api/__csrf_review", method=method, base_url=BACKEND, headers=headers, json={})


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_public_https_referrer_with_valid_token_passes_backend_host_proxy(origin_app, method):
    app, entered = origin_app
    client, token = verified_client(app)
    response = post(client, token, method=method)
    assert response.status_code == 200
    assert entered == [True]
    assert app.config["WTF_CSRF_SSL_STRICT"] is True


@pytest.mark.parametrize("token", [None, "invalid-fictional-token"])
def test_missing_or_bad_token_never_reaches_review(origin_app, token):
    app, entered = origin_app
    client, _ = verified_client(app)
    response = post(client, token)
    assert response.status_code == 400
    assert response.get_json()["error_code"] == "invalid_request"
    assert "no-store" in response.headers["Cache-Control"]
    assert entered == []


@pytest.mark.parametrize(
    "referrer",
    [
        None,
        "https://hostile.example.invalid/",
        "http://frontend.example.invalid/",
        "https://frontend.example.invalid:8443/",
    ],
)
def test_missing_or_different_referrer_never_reaches_review(origin_app, referrer):
    app, entered = origin_app
    client, token = verified_client(app)
    response = post(client, token, referrer=referrer)
    assert response.status_code == 400
    assert entered == []


def test_client_forwarded_host_cannot_authorize_a_hostile_referrer(origin_app):
    app, entered = origin_app
    client, token = verified_client(app)
    response = post(
        client, token, referrer="https://hostile.example.invalid/", forwarded_host="hostile.example.invalid"
    )
    assert response.status_code == 400
    assert entered == []


@pytest.mark.parametrize(
    "origin",
    [
        "http://frontend.example.invalid",
        "https://user:pass@frontend.example.invalid",
        "https://frontend.example.invalid/path",
        "https://frontend.example.invalid?",
        "https://frontend.example.invalid#",
        "https://frontend.example.invalid:0",
        "https://frontend.example.invalid:",
        " https://frontend.example.invalid",
    ],
)
def test_invalid_configured_public_origin_refuses_startup(monkeypatch, origin):
    from config import TestConfig
    from shuffify import create_app

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "fictional-client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "fictional-secret")
    monkeypatch.setattr(TestConfig, "PUBLIC_ORIGIN", origin, raising=False)
    with pytest.raises(ValueError, match="PUBLIC_ORIGIN"):
        create_app("testing")


def test_read_redirect_and_native_logout_location_are_preserved(origin_app):
    from flask import redirect, url_for

    app, _ = origin_app

    @app.get("/__origin_redirect")
    def probe_redirect():
        return redirect(url_for("probe_target", _external=True))

    @app.get("/__origin_target")
    def probe_target():
        return "ok"

    client, token = verified_client(app)
    reply = client.get(
        "/__origin_redirect",
        base_url=BACKEND,
        headers={
            "X-Forwarded-Host": "backend.example.invalid",
            "X-Forwarded-Proto": "https",
        },
    )
    assert reply.status_code == 302
    assert reply.headers["Location"] == BACKEND + "/__origin_target"
    reply = client.post(
        "/logout",
        base_url=BACKEND,
        headers={
            "X-Forwarded-Host": "backend.example.invalid",
            "X-Forwarded-Proto": "https",
            "Referer": FRONTEND + "/settings",
            "X-CSRFToken": token,
        },
    )
    assert reply.status_code == 302
    assert reply.headers["Location"] == "/"
