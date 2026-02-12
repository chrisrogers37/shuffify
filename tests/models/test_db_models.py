"""
Tests for SQLAlchemy database models.

Tests cover User, WorkshopSession, and UpstreamSource models
including creation, relationships, serialization, and constraints.
"""

import json
import pytest
from datetime import datetime, timezone

from shuffify.models.db import (
    db, User, WorkshopSession, UpstreamSource, LoginHistory
)


@pytest.fixture
def db_app():
    """Create a Flask app with an in-memory SQLite database for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True

    # Re-initialize db with in-memory URI
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def db_session(db_app):
    """Provide a database session within app context."""
    with db_app.app_context():
        yield db.session


class TestUserModel:
    """Tests for the User model."""

    def test_create_user(self, db_session):
        """Should create a user with required fields."""
        user = User(
            spotify_id="user123",
            display_name="Test User",
            email="test@example.com",
            profile_image_url="https://example.com/avatar.jpg",
        )
        db_session.add(user)
        db_session.commit()

        assert user.id is not None
        assert user.spotify_id == "user123"
        assert user.display_name == "Test User"
        assert user.created_at is not None
        assert user.updated_at is not None

    def test_spotify_id_is_unique(self, db_session):
        """Should not allow duplicate spotify_id values."""
        user1 = User(
            spotify_id="user123", display_name="User 1"
        )
        db_session.add(user1)
        db_session.commit()

        user2 = User(
            spotify_id="user123", display_name="User 2"
        )
        db_session.add(user2)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()
        db_session.rollback()

    def test_spotify_id_is_required(self, db_session):
        """Should not allow null spotify_id."""
        user = User(display_name="No ID User")
        db_session.add(user)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()
        db_session.rollback()

    def test_to_dict(self, db_session):
        """Should serialize all fields to a dictionary."""
        user = User(
            spotify_id="user123",
            display_name="Test User",
            email="test@example.com",
        )
        db_session.add(user)
        db_session.commit()

        d = user.to_dict()
        assert d["spotify_id"] == "user123"
        assert d["display_name"] == "Test User"
        assert d["email"] == "test@example.com"
        assert "created_at" in d
        assert "updated_at" in d

    def test_repr(self, db_session):
        """Should have a useful string representation."""
        user = User(
            spotify_id="user123", display_name="Test User"
        )
        assert "user123" in repr(user)
        assert "Test User" in repr(user)

    def test_nullable_optional_fields(self, db_session):
        """Should allow null for optional fields."""
        user = User(spotify_id="minimal_user")
        db_session.add(user)
        db_session.commit()

        assert user.display_name is None
        assert user.email is None
        assert user.profile_image_url is None

    def test_new_fields_defaults(self, db_session):
        """Should have correct defaults for new fields."""
        user = User(spotify_id="default_user")
        db_session.add(user)
        db_session.commit()

        assert user.login_count == 0
        assert user.is_active is True
        assert user.last_login_at is None
        assert user.country is None
        assert user.spotify_product is None
        assert user.spotify_uri is None

    def test_create_user_with_all_fields(self, db_session):
        """Should create a user with all new fields populated."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user = User(
            spotify_id="full_user",
            display_name="Full User",
            email="full@example.com",
            profile_image_url="https://example.com/img.jpg",
            last_login_at=now,
            login_count=5,
            is_active=True,
            country="US",
            spotify_product="premium",
            spotify_uri="spotify:user:full_user",
        )
        db_session.add(user)
        db_session.commit()

        assert user.last_login_at == now
        assert user.login_count == 5
        assert user.is_active is True
        assert user.country == "US"
        assert user.spotify_product == "premium"
        assert user.spotify_uri == "spotify:user:full_user"

    def test_to_dict_includes_new_fields(self, db_session):
        """Should include new fields in serialized dict."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user = User(
            spotify_id="dict_user",
            last_login_at=now,
            login_count=3,
            is_active=True,
            country="GB",
            spotify_product="free",
            spotify_uri="spotify:user:dict_user",
        )
        db_session.add(user)
        db_session.commit()

        d = user.to_dict()
        assert d["last_login_at"] == now.isoformat()
        assert d["login_count"] == 3
        assert d["is_active"] is True
        assert d["country"] == "GB"
        assert d["spotify_product"] == "free"
        assert d["spotify_uri"] == "spotify:user:dict_user"

    def test_to_dict_null_new_fields(self, db_session):
        """Should serialize None for unpopulated new fields."""
        user = User(spotify_id="null_fields_user")
        db_session.add(user)
        db_session.commit()

        d = user.to_dict()
        assert d["last_login_at"] is None
        assert d["login_count"] == 0
        assert d["is_active"] is True
        assert d["country"] is None
        assert d["spotify_product"] is None
        assert d["spotify_uri"] is None

    def test_is_active_can_be_set_false(self, db_session):
        """Should allow setting is_active to False."""
        user = User(
            spotify_id="inactive_user",
            is_active=False,
        )
        db_session.add(user)
        db_session.commit()

        fetched = User.query.filter_by(
            spotify_id="inactive_user"
        ).first()
        assert fetched.is_active is False


class TestWorkshopSessionModel:
    """Tests for the WorkshopSession model."""

    @pytest.fixture
    def user(self, db_session):
        """Create a test user."""
        user = User(
            spotify_id="user123", display_name="Test User"
        )
        db_session.add(user)
        db_session.commit()
        return user

    def test_create_workshop_session(self, db_session, user):
        """Should create a workshop session with track URIs."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="playlist456",
            session_name="My Arrangement",
        )
        ws.track_uris = [
            "spotify:track:a",
            "spotify:track:b",
            "spotify:track:c",
        ]

        db_session.add(ws)
        db_session.commit()

        assert ws.id is not None
        assert ws.track_uris == [
            "spotify:track:a",
            "spotify:track:b",
            "spotify:track:c",
        ]
        assert ws.session_name == "My Arrangement"

    def test_track_uris_property_getter(
        self, db_session, user
    ):
        """Should deserialize JSON to list."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json='["uri1", "uri2"]',
        )
        db_session.add(ws)
        db_session.commit()

        assert ws.track_uris == ["uri1", "uri2"]

    def test_track_uris_property_setter(
        self, db_session, user
    ):
        """Should serialize list to JSON."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
        )
        ws.track_uris = ["a", "b", "c"]
        db_session.add(ws)
        db_session.commit()

        assert json.loads(ws.track_uris_json) == [
            "a",
            "b",
            "c",
        ]

    def test_track_uris_empty_json(self, db_session, user):
        """Should return empty list for empty JSON."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json="",
        )
        assert ws.track_uris == []

    def test_track_uris_invalid_json(self, db_session, user):
        """Should return empty list for invalid JSON."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json="not valid json",
        )
        assert ws.track_uris == []

    def test_to_dict(self, db_session, user):
        """Should serialize with track count."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="My Session",
        )
        ws.track_uris = ["uri1", "uri2", "uri3"]
        db_session.add(ws)
        db_session.commit()

        d = ws.to_dict()
        assert d["session_name"] == "My Session"
        assert d["track_count"] == 3
        assert d["track_uris"] == ["uri1", "uri2", "uri3"]
        assert d["playlist_id"] == "p1"

    def test_user_relationship(self, db_session, user):
        """Should link to the parent User."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json="[]",
        )
        db_session.add(ws)
        db_session.commit()

        assert ws.user.spotify_id == "user123"
        assert len(user.workshop_sessions) == 1

    def test_cascade_delete(self, db_session, user):
        """Deleting user should delete workshop sessions."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json="[]",
        )
        db_session.add(ws)
        db_session.commit()
        ws_id = ws.id

        db_session.delete(user)
        db_session.commit()

        assert db_session.get(WorkshopSession, ws_id) is None


class TestUpstreamSourceModel:
    """Tests for the UpstreamSource model."""

    @pytest.fixture
    def user(self, db_session):
        """Create a test user."""
        user = User(
            spotify_id="user123", display_name="Test User"
        )
        db_session.add(user)
        db_session.commit()
        return user

    def test_create_upstream_source(self, db_session, user):
        """Should create an upstream source with required fields."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target_p1",
            source_playlist_id="source_p2",
            source_type="external",
            source_name="Cool Playlist",
        )
        db_session.add(source)
        db_session.commit()

        assert source.id is not None
        assert source.source_type == "external"
        assert source.source_name == "Cool Playlist"

    def test_to_dict(self, db_session, user):
        """Should serialize all fields."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target",
            source_playlist_id="source",
            source_type="own",
            source_url=(
                "https://open.spotify.com/playlist/source"
            ),
            source_name="My Source",
        )
        db_session.add(source)
        db_session.commit()

        d = source.to_dict()
        assert d["source_type"] == "own"
        assert d["source_url"] == (
            "https://open.spotify.com/playlist/source"
        )
        assert d["source_name"] == "My Source"

    def test_user_relationship(self, db_session, user):
        """Should link to parent User."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target",
            source_playlist_id="source",
            source_type="external",
        )
        db_session.add(source)
        db_session.commit()

        assert source.user.spotify_id == "user123"
        assert len(user.upstream_sources) == 1

    def test_cascade_delete(self, db_session, user):
        """Deleting user should delete upstream sources."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target",
            source_playlist_id="source",
            source_type="external",
        )
        db_session.add(source)
        db_session.commit()
        source_id = source.id

        db_session.delete(user)
        db_session.commit()

        assert (
            db_session.get(UpstreamSource, source_id) is None
        )

    def test_default_source_type(self, db_session, user):
        """Should default source_type to 'external'."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target",
            source_playlist_id="source",
        )
        db_session.add(source)
        db_session.commit()

        assert source.source_type == "external"


class TestLoginHistoryModel:
    """Tests for the LoginHistory model."""

    @pytest.fixture
    def user(self, db_session):
        """Create a test user."""
        user = User(
            spotify_id="user123", display_name="Test User"
        )
        db_session.add(user)
        db_session.commit()
        return user

    def test_create_login_history(self, db_session, user):
        """Should create a login history record."""
        entry = LoginHistory(
            user_id=user.id,
            ip_address="192.168.1.1",
            user_agent="TestBrowser/1.0",
            session_id="sess_abc123",
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        assert entry.id is not None
        assert entry.user_id == user.id
        assert entry.ip_address == "192.168.1.1"
        assert entry.user_agent == "TestBrowser/1.0"
        assert entry.session_id == "sess_abc123"
        assert entry.login_type == "oauth_initial"
        assert entry.logged_in_at is not None
        assert entry.logged_out_at is None

    def test_login_type_required(self, db_session, user):
        """Should require login_type field."""
        entry = LoginHistory(
            user_id=user.id,
        )
        db_session.add(entry)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()
        db_session.rollback()

    def test_nullable_fields(self, db_session, user):
        """Should allow null for optional fields."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        assert entry.ip_address is None
        assert entry.user_agent is None
        assert entry.session_id is None
        assert entry.logged_out_at is None

    def test_logged_out_at_update(self, db_session, user):
        """Should allow setting logged_out_at after creation."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        entry.logged_out_at = datetime.now(timezone.utc)
        db_session.commit()

        assert entry.logged_out_at is not None

    def test_to_dict(self, db_session, user):
        """Should serialize all fields."""
        entry = LoginHistory(
            user_id=user.id,
            ip_address="10.0.0.1",
            user_agent="Chrome/100",
            session_id="sess_xyz",
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        d = entry.to_dict()
        assert d["user_id"] == user.id
        assert d["ip_address"] == "10.0.0.1"
        assert d["user_agent"] == "Chrome/100"
        assert d["session_id"] == "sess_xyz"
        assert d["login_type"] == "oauth_initial"
        assert d["logged_in_at"] is not None
        assert d["logged_out_at"] is None

    def test_repr(self, db_session, user):
        """Should have a useful string representation."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        r = repr(entry)
        assert "LoginHistory" in r
        assert "oauth_initial" in r

    def test_user_relationship(self, db_session, user):
        """Should link to parent User."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        assert entry.user.spotify_id == "user123"
        assert len(user.login_history) == 1

    def test_cascade_delete(self, db_session, user):
        """Deleting user should delete login history."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()
        entry_id = entry.id

        db_session.delete(user)
        db_session.commit()

        assert db_session.get(LoginHistory, entry_id) is None

    def test_multiple_login_entries(self, db_session, user):
        """Should support multiple login records per user."""
        for i in range(5):
            entry = LoginHistory(
                user_id=user.id,
                login_type="oauth_initial",
                ip_address=f"10.0.0.{i}",
            )
            db_session.add(entry)
        db_session.commit()

        assert len(user.login_history) == 5
