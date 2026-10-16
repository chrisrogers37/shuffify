"""Settings JSON reads and updates preserve normalized and unedited preferences."""

from unittest.mock import MagicMock, patch

import pytest

from shuffify.models.db import User, UserSettings, db
from shuffify.spotify.api import SpotifyAPI
from tests.paths import api_path


@pytest.fixture
def test_user(db_app):
    """Create a test user."""
    with db_app.app_context():
        user = User(
            spotify_id="route_test_user",
            display_name="Route Test User",
        )
        db.session.add(user)
        db.session.commit()
        return user


@pytest.fixture
def auth_client(db_app, test_user):
    """Authenticated test client with mocked Spotify auth."""
    with db_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "test_refresh",
            }
            sess["user_data"] = {
                "id": "route_test_user",
                "display_name": "Route Test User",
                "images": [],
            }
        yield client


class TestSettingsGetRoute:
    """Tests for GET /settings."""

    @patch("shuffify.routes.AuthService")
    def test_returns_json_for_ajax_request(
        self, mock_auth, auth_client, test_user
    ):
        """Should return JSON settings data for AJAX GET."""
        mock_client = MagicMock()
        mock_auth.get_authenticated_client.return_value = (
            mock_client
        )
        mock_auth.get_user_data.return_value = {
            "id": "route_test_user",
            "display_name": "Route Test User",
            "images": [],
        }
        mock_auth.validate_session_token.return_value = (
            True
        )

        response = auth_client.get(
            api_path("/settings"),
            headers={
                "X-Requested-With": "XMLHttpRequest"
            },
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "settings" in data
        assert "algorithm_options" in data
        assert isinstance(data["algorithm_options"], list)


class TestSettingsPostRoute:
    """Tests for POST /settings."""

    @patch("shuffify.routes.require_auth")
    def test_update_settings_via_form(
        self, mock_auth, auth_client, db_app, test_user
    ):
        """Should update settings from form submission."""
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            response = auth_client.post(
                api_path("/settings"),
                data={
                    "theme": "dark",
                    "auto_snapshot_enabled": "true",
                    "notifications_enabled": "false",
                    "max_snapshots_per_playlist": "15",
                    "dashboard_show_recent_activity": "true",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True

    def test_update_settings_unauthenticated(
        self, db_app
    ):
        """Should reject unauthenticated settings updates."""
        with db_app.test_client() as client:
            response = client.post(
                api_path("/settings"),
                data={"theme": "dark"},
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )
            assert response.status_code in (401, 302)


class TestSettingsHiddenInputRegression:
    """Boolean settings fields submitted as form data.

    When a hidden ``false`` and a checked ``true`` are both submitted
    for one field, the truthy value wins: the route reads every value
    with ``getlist``. A lone ``false`` or an absent field means
    unchecked.
    """

    @patch("shuffify.routes.require_auth")
    def test_route_honors_checkbox_over_hidden_default(
        self, mock_auth, auth_client, db_app, test_user
    ):
        """If both ``false`` (hidden) and ``true`` (checkbox) are
        submitted, the truthy value wins. Werkzeug's test client
        encodes a list value as multiple form fields with the
        same name: a hidden ``false`` followed by a checked ``true``."""
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            response = auth_client.post(
                api_path("/settings"),
                data={
                    "theme": "dark",
                    # Hidden ``false`` (first) + checkbox ``true``.
                    "auto_snapshot_enabled": ["false", "true"],
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )
            assert response.status_code == 200

            user = User.query.filter_by(
                spotify_id="route_test_user"
            ).one()
            settings = UserSettings.query.filter_by(
                user_id=user.id
            ).first()
            assert settings is not None
            assert settings.auto_snapshot_enabled is True

    @patch("shuffify.routes.require_auth")
    def test_route_treats_hidden_only_as_unchecked(
        self, mock_auth, auth_client, db_app, test_user
    ):
        """If only ``false`` (hidden) is submitted — i.e. the
        checkbox was unchecked — the field persists as
        ``False``."""
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            user = User.query.filter_by(
                spotify_id="route_test_user"
            ).one()
            existing = UserSettings(
                user_id=user.id,
                auto_snapshot_enabled=True,
            )
            db.session.add(existing)
            db.session.commit()

            response = auth_client.post(
                api_path("/settings"),
                data={
                    "theme": "dark",
                    "auto_snapshot_enabled": "false",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )
            assert response.status_code == 200

            settings = UserSettings.query.filter_by(
                user_id=user.id
            ).first()
            assert settings.auto_snapshot_enabled is False

    @patch("shuffify.routes.require_auth")
    def test_route_treats_missing_field_as_unchecked(
        self, mock_auth, auth_client, db_app, test_user
    ):
        """If the field is absent entirely (an unchecked checkbox
        with no hidden input), the field persists as ``False``."""
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            user = User.query.filter_by(
                spotify_id="route_test_user"
            ).one()
            existing = UserSettings(
                user_id=user.id,
                auto_snapshot_enabled=True,
            )
            db.session.add(existing)
            db.session.commit()

            response = auth_client.post(
                api_path("/settings"),
                # No auto_snapshot_enabled field at all.
                data={"theme": "dark"},
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )
            assert response.status_code == 200

            settings = UserSettings.query.filter_by(
                user_id=user.id
            ).first()
            assert settings.auto_snapshot_enabled is False


class TestDedicatedSettings:
    """JSON changes preserve fields the current interface does not edit."""

    @pytest.mark.parametrize("theme,notifications,checked", [
        ("system", False, False), ("dark", True, True), ("light", True, False), ("system", False, True),
    ])
    def test_json_changes_preserve_unedited_preferences(self, auth_client, theme, notifications, checked):
        user_id = User.query.filter_by(spotify_id="route_test_user").one().id
        db.session.add(UserSettings(
            user_id=user_id, theme=theme, notifications_enabled=notifications,
            default_algorithm="BasicShuffle", auto_snapshot_enabled=not checked,
            dashboard_show_recent_activity=not checked,
        ))
        db.session.commit()
        with patch("shuffify.routes.require_auth", autospec=True, return_value=object()):
            response = auth_client.post("/api/settings", json={
                "auto_snapshot_enabled": checked, "dashboard_show_recent_activity": checked,
                "max_snapshots_per_playlist": 17, "default_algorithm": None,
            })
        assert response.status_code == 200
        assert response.json["success"] is True
        settings = UserSettings.query.filter_by(user_id=user_id).one()
        assert (settings.theme, settings.notifications_enabled) == (theme, notifications)
        assert settings.auto_snapshot_enabled is settings.dashboard_show_recent_activity is checked
        assert settings.default_algorithm is None and settings.max_snapshots_per_playlist == 17

    @patch("shuffify.routes.require_auth", return_value=MagicMock(spec=SpotifyAPI))
    @pytest.mark.parametrize("payload,expected", [
        ({"default_algorithm": ""}, None),
        ({"default_algorithm": None}, None),
        ({"max_snapshots_per_playlist": 17}, "BasicShuffle"),
    ])
    def test_clearing_default_differs_from_omitting_it(
        self, mock_auth, auth_client, db_app, test_user, payload, expected,
    ):
        with db_app.app_context():
            user = User.query.filter_by(spotify_id="route_test_user").one()
            user_id = user.id
            db.session.add(UserSettings(user_id=user_id, default_algorithm="BasicShuffle"))
            db.session.commit()
        response = auth_client.post(api_path("/settings"), json=payload)
        assert response.status_code == 200
        with db_app.app_context():
            saved = UserSettings.query.filter_by(user_id=user_id).one()
            assert saved.default_algorithm == expected
