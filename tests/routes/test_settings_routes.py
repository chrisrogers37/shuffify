"""
Tests for the /settings routes.

Tests cover rendering the settings page and updating settings.
"""

import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db, User, UserSettings


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

    def test_redirects_when_not_authenticated(
        self, db_app
    ):
        """Should redirect unauthenticated users."""
        with db_app.test_client() as client:
            response = client.get("/settings")
            assert response.status_code == 302

    @patch("shuffify.routes.settings.AuthService")
    def test_renders_settings_page(
        self, mock_auth, auth_client, test_user
    ):
        """Should render settings page for authenticated user."""
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

        response = auth_client.get("/settings")
        assert response.status_code == 200
        assert b"Settings" in response.data


    @patch("shuffify.routes.settings.AuthService")
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
            "/settings",
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
                "/settings",
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
                "/settings",
                data={"theme": "dark"},
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )
            assert response.status_code in (401, 302)


class TestSettingsHiddenInputRegression:
    """Regression tests for the 2026-03-30 sidebar bug that silently
    wedged ``auto_snapshot_enabled`` to ``False`` on every save.

    Root cause: ``settings_sidebar.html`` had ``<input type=hidden
    value=false>`` siblings to the bool checkboxes. When the checkbox
    was checked, the browser submitted BOTH ``false`` (hidden) and
    ``true`` (checkbox). ``request.form.to_dict(flat=True)`` returns
    the first occurrence — the hidden ``false`` — so the route
    persisted ``False`` regardless of the toggle's visual state.

    The fix has two layers:
    1. Template: remove the hidden inputs (the route already defaults
       missing fields to ``False`` for form submits).
    2. Route: read multi-valued fields via ``getlist`` and apply
       "any truthy wins", so even if a future template author
       reintroduces the pattern, the route won't be fooled.
    """

    @patch("shuffify.routes.require_auth")
    def test_route_honors_checkbox_over_hidden_default(
        self, mock_auth, auth_client, db_app, test_user
    ):
        """If both ``false`` (hidden) and ``true`` (checkbox) are
        submitted, the truthy value wins. Werkzeug's test client
        encodes a list value as multiple form fields with the
        same name, exactly mimicking the broken sidebar shape."""
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            response = auth_client.post(
                "/settings",
                data={
                    "theme": "dark",
                    # The exact shape the broken sidebar produced:
                    # hidden ``false`` (first) + checkbox ``true``.
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
        ``False``. Confirms we didn't break the unchecked path."""
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
                "/settings",
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
        """If the field is absent entirely (post-fix sidebar
        shape when the checkbox is unchecked), the field persists
        as ``False``."""
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
                "/settings",
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

    def test_sidebar_template_has_no_hidden_bool_inputs(self):
        """Template-level guard: the settings sidebar must not
        reintroduce the hidden+checkbox pattern for any bool
        field. The route's getlist hardening defends against this
        too, but the template fix is the primary line of
        defense."""
        import os

        template_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "shuffify",
            "templates",
            "partials",
            "settings_sidebar.html",
        )
        with open(template_path) as f:
            content = f.read()

        for bool_field in (
            "auto_snapshot_enabled",
            "dashboard_show_recent_activity",
            "notifications_enabled",
        ):
            forbidden = (
                f'type="hidden" name="{bool_field}"'
            )
            assert forbidden not in content, (
                f"settings_sidebar.html contains the "
                f"hidden+checkbox pattern for "
                f"'{bool_field}' — this re-introduces the "
                f"2026-03-30 silent-wedge bug. Delete the "
                f"hidden input; the route defaults absent "
                f"checkboxes to False."
            )
