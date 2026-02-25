"""
Tests for the PlaylistPreference model.

Covers creation, defaults, unique constraint, to_dict,
and repr.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from shuffify.models.db import db, User, PlaylistPreference


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"
    os.environ.pop("DATABASE_URL", None)

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///:memory:"
    )
    app.config["TESTING"] = True

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app_ctx(db_app):
    """Provide app context."""
    with db_app.app_context():
        yield


@pytest.fixture
def test_user(app_ctx):
    """Create a test user."""
    user = User(
        spotify_id="pref_test_user",
        display_name="Pref Test User",
    )
    db.session.add(user)
    db.session.commit()
    return user


class TestPlaylistPreferenceModel:
    """Tests for PlaylistPreference model."""

    def test_create_with_defaults(
        self, app_ctx, test_user
    ):
        """Should create with correct defaults."""
        pref = PlaylistPreference(
            user_id=test_user.id,
            spotify_playlist_id="abc123",
        )
        db.session.add(pref)
        db.session.commit()

        assert pref.id is not None
        assert pref.sort_order == 0
        assert pref.is_hidden is False
        assert pref.is_pinned is False
        assert pref.created_at is not None
        assert pref.updated_at is not None

    def test_unique_constraint(
        self, app_ctx, test_user
    ):
        """Duplicate user+playlist should raise."""
        pref1 = PlaylistPreference(
            user_id=test_user.id,
            spotify_playlist_id="abc123",
        )
        db.session.add(pref1)
        db.session.commit()

        pref2 = PlaylistPreference(
            user_id=test_user.id,
            spotify_playlist_id="abc123",
        )
        db.session.add(pref2)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_to_dict(self, app_ctx, test_user):
        """to_dict should include all fields."""
        pref = PlaylistPreference(
            user_id=test_user.id,
            spotify_playlist_id="xyz789",
            sort_order=3,
            is_hidden=True,
            is_pinned=True,
        )
        db.session.add(pref)
        db.session.commit()

        d = pref.to_dict()
        assert d["user_id"] == test_user.id
        assert d["spotify_playlist_id"] == "xyz789"
        assert d["sort_order"] == 3
        assert d["is_hidden"] is True
        assert d["is_pinned"] is True
        assert d["created_at"] is not None
        assert d["updated_at"] is not None

    def test_repr(self, app_ctx, test_user):
        """repr should include key fields."""
        pref = PlaylistPreference(
            user_id=test_user.id,
            spotify_playlist_id="abc123",
            sort_order=2,
            is_hidden=True,
            is_pinned=False,
        )
        db.session.add(pref)
        db.session.commit()

        r = repr(pref)
        assert "PlaylistPreference" in r
        assert "abc123" in r
        assert "hidden" in r
        assert "unpinned" in r

    def test_user_relationship(
        self, app_ctx, test_user
    ):
        """Backref should be accessible from User."""
        pref = PlaylistPreference(
            user_id=test_user.id,
            spotify_playlist_id="abc123",
        )
        db.session.add(pref)
        db.session.commit()

        prefs = test_user.playlist_preferences.all()
        assert len(prefs) == 1
        assert prefs[0].spotify_playlist_id == "abc123"
