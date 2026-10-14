"""
Tests for the /settings routes.

Tests cover rendering the settings page and updating settings.
"""

from html.parser import HTMLParser
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.datastructures import MultiDict

from shuffify.models.db import User, UserSettings, db
from shuffify.spotify.api import SpotifyAPI


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

    @patch("shuffify.routes.AuthService")
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
                "/settings",
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


class SettingsMarkup(HTMLParser):
    """Read real browser-successful controls from the rendered settings form."""

    def __init__(self, html):
        super().__init__()
        self.forms = {}
        self.nodes = {}
        self.current_form = None
        self.current_select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.nodes[attrs["id"]] = (tag, attrs)
        if tag == "form":
            self.current_form = attrs.get("id", "anonymous-form")
            self.forms[self.current_form] = {"attrs": attrs, "data": MultiDict()}
        if not self.current_form:
            return
        data = self.forms[self.current_form]["data"]
        if tag == "input" and attrs.get("name"):
            if attrs.get("type") != "checkbox" or "checked" in attrs:
                data.add(attrs["name"], attrs.get("value", "on"))
        if tag == "select":
            self.current_select = attrs["name"]
        if tag == "option" and "selected" in attrs:
            data.add(self.current_select, attrs["value"])

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None
        if tag == "select":
            self.current_select = None


class TestDedicatedSettings:
    """Exercise the rendered settings form as a browser submits it."""

    @patch("shuffify.routes.AuthService", autospec=True)
    @pytest.mark.parametrize("theme,notifications,checked,ajax", [
        ("system", False, False, False),
        ("dark", True, True, False),
        ("light", True, False, True),
        ("system", False, True, True),
    ])
    def test_rendered_form_preserves_unavailable_preferences(
        self, mock_auth, auth_client, db_app, test_user,
        theme, notifications, checked, ajax,
    ):
        mock_auth.get_authenticated_api.return_value = MagicMock(spec=SpotifyAPI)
        mock_auth.validate_session_token.return_value = True
        mock_auth.get_user_data.return_value = {
            "id": "route_test_user", "display_name": "Route Test User", "images": [],
        }
        with db_app.app_context():
            user_id = User.query.filter_by(spotify_id="route_test_user").one().id
            db.session.add(UserSettings(
                user_id=user_id,
                theme=theme,
                default_algorithm="BasicShuffle",
                notifications_enabled=notifications,
                auto_snapshot_enabled=not checked,
                dashboard_show_recent_activity=not checked,
            ))
            db.session.commit()

        html = auth_client.get("/settings").get_data(as_text=True)
        markup = SettingsMarkup(html)
        form = markup.forms["settings-form"]
        assert form["attrs"]["method"].lower() == "post"
        assert form["attrs"]["action"] == "/settings"
        for name in ("theme", "notifications_enabled"):
            assert markup.nodes[name][1]["type"] == "hidden"
        assert len(form["data"].getlist("notifications_enabled")) == 1
        assert "csrf_token" in form["data"]
        for name in ("auto_snapshot_enabled", "dashboard_show_recent_activity"):
            assert len(form["data"].getlist(name)) <= 1
            form["data"].poplist(name)
            if checked:
                form["data"].add(name, "true")
        form["data"]["max_snapshots_per_playlist"] = "17"
        form["data"]["default_algorithm"] = ""
        headers = {"X-Requested-With": "XMLHttpRequest"} if ajax else {}
        with patch("shuffify.routes.require_auth", return_value=MagicMock(spec=SpotifyAPI)):
            response = auth_client.post("/settings", data=form["data"], headers=headers)
        if ajax:
            assert response.status_code == 200
            assert response.get_json()["success"] is True
        else:
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/settings")
        with db_app.app_context():
            saved = UserSettings.query.filter_by(user_id=user_id).one()
            assert saved.theme == theme
            assert saved.default_algorithm is None
            assert saved.notifications_enabled is notifications
            assert saved.auto_snapshot_enabled is checked
            assert saved.dashboard_show_recent_activity is checked
            assert saved.max_snapshots_per_playlist == 17

    @patch("shuffify.routes.AuthService", autospec=True)
    def test_form_has_accessible_help_errors_and_account_logout(
        self, mock_auth, auth_client, test_user,
    ):
        mock_auth.get_authenticated_api.return_value = MagicMock(spec=SpotifyAPI)
        mock_auth.validate_session_token.return_value = True
        mock_auth.get_user_data.return_value = {
            "id": "route_test_user", "display_name": "Route Test User", "images": [],
        }
        html = auth_client.get("/settings").get_data(as_text=True)
        markup = SettingsMarkup(html)
        assert "sh-shell sh-app-shell" in html
        assert "Snapshots &amp; History" in html
        assert "Snapshots &amp;amp; History" not in html
        assert "js/settings.js" in html
        for field in ("default_algorithm", "auto_snapshot_enabled",
                      "dashboard_show_recent_activity", "max_snapshots_per_playlist"):
            attrs = markup.nodes[field][1]
            assert attrs["aria-describedby"]
            for description in attrs["aria-describedby"].split():
                assert description in markup.nodes
        maximum = markup.nodes["max_snapshots_per_playlist"][1]
        assert maximum["min"] == "1"
        assert maximum["max"] == "50"
        assert "required" in maximum
        assert markup.nodes["settings-error"][1]["role"] == "alert"
        logout = markup.forms["settings-logout-form"]
        assert logout["attrs"]["method"].lower() == "post"
        assert logout["attrs"]["action"] == "/logout"
        assert "csrf_token" in logout["data"]

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
        response = auth_client.post("/settings", json=payload)
        assert response.status_code == 200
        with db_app.app_context():
            saved = UserSettings.query.filter_by(user_id=user_id).one()
            assert saved.default_algorithm == expected
