"""Library defaults respect saved choices without trusting stale parameters."""

from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import template_rendered

from shuffify.models.db import User, db
from shuffify.services.user_settings_service import UserSettingsService


@pytest.fixture
def saved_settings(app_ctx):
    user = User(spotify_id="library_defaults", display_name="Library Defaults")
    db.session.add(user)
    db.session.commit()
    return UserSettingsService.get_or_create(user.id)


@pytest.mark.parametrize("algorithm", [None, "RetiredShuffle"])
def test_missing_or_stale_algorithm_requires_a_choice(saved_settings, algorithm):
    saved_settings.default_algorithm = algorithm
    saved_settings.default_algorithm_params = {"keep_first": 3}
    db.session.commit()

    assert UserSettingsService.get_default_shuffle_settings(saved_settings.user_id) == {
        "algorithm": None,
        "params": {},
    }


@pytest.mark.parametrize(
    "algorithm, saved, expected",
    [
        ("BasicShuffle", {"keep_first": 0}, {"keep_first": 0}),
        (
            "PercentageShuffle",
            {"shuffle_percentage": "25.5", "shuffle_location": "back", "keep_first": 4},
            {"shuffle_percentage": 25.5, "shuffle_location": "back"},
        ),
        ("StratifiedShuffle", {"keep_first": "2"}, {"keep_first": 2}),
    ],
)
def test_only_valid_saved_parameters_are_exposed(saved_settings, algorithm, saved, expected):
    saved_settings.default_algorithm = algorithm
    saved_settings.default_algorithm_params = saved
    db.session.commit()

    assert UserSettingsService.get_default_shuffle_settings(saved_settings.user_id) == {
        "algorithm": algorithm,
        "params": expected,
    }
    # Reading defaults does not change persisted preferences or introduce
    # generic schema defaults (Stratified's metadata default is five sections).
    assert saved_settings.default_algorithm_params == saved


@pytest.mark.parametrize("section_count", ["invalid", 0, 11, None])
def test_invalid_option_does_not_discard_other_valid_options(saved_settings, section_count):
    saved_settings.default_algorithm = "BalancedShuffle"
    saved_settings.default_algorithm_params = {
        "keep_first": 3,
        "section_count": section_count,
        "jitter": 10,
    }
    db.session.commit()

    assert UserSettingsService.get_default_shuffle_settings(saved_settings.user_id) == {
        "algorithm": "BalancedShuffle",
        "params": {"keep_first": 3},
    }


@pytest.mark.parametrize("params", [None, [], "stale"])
def test_malformed_parameter_storage_keeps_the_saved_method(saved_settings, params):
    saved_settings.default_algorithm = "AlbumSequenceShuffle"
    saved_settings.default_algorithm_params = params
    db.session.commit()

    assert UserSettingsService.get_default_shuffle_settings(saved_settings.user_id) == {
        "algorithm": "AlbumSequenceShuffle",
        "params": {},
    }


@pytest.fixture
def library_route(auth_client, db_app, sample_user, sample_playlists):
    """Render the real dashboard with only external and aggregation work mocked."""
    contexts = []

    def capture(sender, template, context, **extra):
        contexts.append((template.name, context))

    template_rendered.connect(capture, db_app)
    with (
        patch("shuffify.routes.core.is_authenticated", return_value=True),
        patch("shuffify.routes.core.AuthService", autospec=True) as auth,
        patch("shuffify.routes.core.PlaylistService") as playlists,
        patch("shuffify.routes.core.DashboardService") as dashboard,
        patch("shuffify.routes.core.PlaylistPreferenceService") as preferences,
    ):
        auth.get_user_data.return_value = sample_user
        for playlist in sample_playlists:
            playlist["images"] = []
            playlist["external_urls"] = {"spotify": f"https://open.spotify.com/playlist/{playlist['id']}"}
        playlists.return_value.get_user_playlists.return_value = sample_playlists
        dashboard.get_dashboard_data.return_value = {}
        preferences.get_user_preferences.return_value = {}
        user = User.query.filter_by(spotify_id="user123").one()
        yield SimpleNamespace(
            client=auth_client,
            contexts=contexts,
            settings=UserSettingsService.get_or_create(user.id),
            dashboard=dashboard,
            preferences=preferences,
            playlists=sample_playlists,
        )
    template_rendered.disconnect(capture, db_app)


def assert_dashboard(library_route, expected_defaults):
    response = library_route.client.get("/")
    assert response.status_code == 200
    template, context = library_route.contexts[-1]
    assert template == "dashboard.html"
    assert context["shuffle_defaults"] == expected_defaults
    assert len(context["playlists"]) == 3
    return context


def test_library_receives_saved_method_and_valid_parameters(library_route):
    library_route.settings.default_algorithm = "ArtistSpacingShuffle"
    library_route.settings.default_algorithm_params = {"min_spacing": 4, "keep_first": 3}
    db.session.commit()

    assert_dashboard(
        library_route,
        {"algorithm": "ArtistSpacingShuffle", "params": {"min_spacing": 4}},
    )


def test_library_without_a_saved_default_does_not_choose_basic(library_route):
    assert_dashboard(library_route, {"algorithm": None, "params": {}})


@pytest.mark.parametrize("failure", [None, RuntimeError("Database unavailable")])
def test_library_survives_unavailable_user_personalization(library_route, failure):
    with patch("shuffify.routes.core.get_db_user", return_value=None, side_effect=failure):
        assert_dashboard(library_route, {"algorithm": None, "params": {}})


def test_settings_failure_preserves_other_dashboard_personalization(library_route):
    library_route.dashboard.get_dashboard_data.return_value = {
        "is_returning_user": True,
        "activity_since_last_login_count": 0,
    }
    library_route.preferences.get_user_preferences.return_value = {
        "playlist1": {"is_pinned": True}
    }
    # Preserve three visible cards while exercising an independently loaded
    # preference payload; ranking itself has dedicated service coverage.
    library_route.preferences.apply_preferences.return_value = (
        [],
        library_route.playlists,
        [],
    )
    with patch(
        "shuffify.routes.core.UserSettingsService.get_default_shuffle_settings",
        side_effect=RuntimeError("Settings unavailable"),
    ):
        context = assert_dashboard(library_route, {"algorithm": None, "params": {}})
    assert context["dashboard"]["is_returning_user"] is True
    assert context["preferences"] == {"playlist1": {"is_pinned": True}}


def test_dashboard_aggregation_failure_does_not_hide_saved_method(library_route):
    library_route.settings.default_algorithm = "NewestFirstShuffle"
    db.session.commit()
    library_route.dashboard.get_dashboard_data.side_effect = RuntimeError("Stats unavailable")

    assert_dashboard(library_route, {"algorithm": "NewestFirstShuffle", "params": {}})


@pytest.mark.parametrize("current_index", [None, 0, 1, 2])
def test_library_restore_control_uses_actual_session_history(library_route, current_index):
    class Buttons(HTMLParser):
        def __init__(self):
            super().__init__()
            self.by_key = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "button" and "data-live-operation-key" in attrs:
                self.by_key[attrs["data-live-operation-key"]] = attrs

    if current_index is not None:
        with library_route.client.session_transaction() as stored:
            stored["playlist_states"] = {"playlist1": {
                "states": [["before"], ["workshop-commit"], ["latest"]],
                "current_index": current_index,
            }}
    response = library_route.client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    buttons = Buttons()
    buttons.feed(html)
    restore = buttons.by_key["undo:playlist1"]
    assert ("hidden" in restore["class"].split()) == (not current_index)
    assert "Restore previous saved version" in restore["aria-label"]
    assert "previous shuffle" not in html
    assert 'id="live-operation-results"' in html
    assert "data-panel-workspace" in html


def test_library_page_scripts_load_deferred_after_the_live_action_controller(library_route):
    html = library_route.client.get("/").get_data(as_text=True)
    order = [
        html.index('<script src="/static/js/playlist-operations.js" defer></script>'),
        html.index('<script src="/static/js/library-shuffle.js" defer></script>'),
        html.index('<script src="/static/js/library.js" defer></script>'),
    ]
    assert order == sorted(order)
    # Preferences, Organize and refresh live in library.js, not in an inline block.
    assert "function placeTile(" not in html and "'dash-001'" not in html
