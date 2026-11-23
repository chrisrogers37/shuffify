"""Fixtures and helpers shared by the route tests."""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from itsdangerous import URLSafeTimedSerializer

from shuffify.models.db import RaidPlaylistLink, User, db
from shuffify.services.upstream_source_service import UpstreamSourceService

CSRF_SESSION_TOKEN = "test-session-csrf"


@pytest.fixture
def limiter_app(monkeypatch, tmp_path):
    """Real route limits with private in-memory counters, SQLite and session files."""
    from config import TestConfig
    from shuffify import create_app

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test_id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test_secret")
    monkeypatch.setattr(TestConfig, "REDIS_URL", None)
    monkeypatch.chdir(tmp_path)
    return create_app("testing")


@pytest.fixture
def signed_in_limiter_client(limiter_app, sample_token):
    from shuffify.services import UserService

    def sign_in(user_id):
        profile = {"id": user_id, "display_name": "Workshop user"}
        with limiter_app.app_context():
            UserService.upsert_from_spotify(profile)
        client = limiter_app.test_client()
        with client.session_transaction() as session:
            session.update(user_data=profile, spotify_token=sample_token)
        return client

    return sign_in


@pytest.fixture
def csrf_client(db_app):
    """A client with Flask-WTF enforcement on and a CSRF token in its session."""
    db_app.config.update(WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=60)
    with db_app.test_client() as client:
        with client.session_transaction() as session:
            session["csrf_token"] = CSRF_SESSION_TOKEN
        yield client


@pytest.fixture
def csrf_headers(db_app):
    """Build headers carrying a missing, expired or valid token for that session."""

    def headers(token_state):
        if token_state == "missing":
            return {}
        serializer = URLSafeTimedSerializer(db_app.secret_key, salt="wtf-csrf-token")
        if token_state == "expired":
            with patch(
                "itsdangerous.timed.TimestampSigner.get_timestamp",
                return_value=int(time.time()) - 120,
            ):
                return {"X-CSRFToken": serializer.dumps(CSRF_SESSION_TOKEN)}
        return {"X-CSRFToken": serializer.dumps(CSRF_SESSION_TOKEN)}

    return headers


# --- Live actions -----------------------------------------------------------


@pytest.fixture
def raid_target(auth_client):
    """user123's 'target', linked to its 'collection' (Drip paused, one track per run), watching 'persisted'."""
    user = User.query.filter_by(spotify_id="user123").one()
    link = RaidPlaylistLink(
        user_id=user.id, target_playlist_id="target", target_playlist_name="Saved target",
        raid_playlist_id="collection", raid_playlist_name="Saved collection",
        drip_count=1, drip_enabled=False,
    )
    db.session.add(link)
    db.session.commit()
    source = UpstreamSourceService.add_source(
        "user123", "target", "persisted", source_type="own", source_name="Persisted source",
    )
    return SimpleNamespace(client=auth_client, user=user, link=link, source=source)
