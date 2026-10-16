"""Library defaults respect saved choices without trusting stale parameters."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

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
def library_route(auth_client, sample_playlists):
    """Read the actual JSON library with only the external Spotify list mocked."""
    for playlist in sample_playlists:
        playlist["images"] = []
        playlist["external_urls"] = {"spotify": f"https://open.spotify.com/playlist/{playlist['id']}"}
    with patch("shuffify.routes.require_auth", autospec=True, return_value=object()), patch(
        "shuffify.routes.contexts.PlaylistService", autospec=True
    ) as playlists:
        playlists.return_value.get_user_playlists.return_value = sample_playlists
        user = User.query.filter_by(spotify_id="user123").one()
        yield SimpleNamespace(client=auth_client, settings=UserSettingsService.get_or_create(user.id))


def assert_library(library_route, expected_defaults):
    response = library_route.client.get("/api/library")
    assert response.status_code == 200, response.json
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json["shuffle_defaults"] == expected_defaults
    assert sum(len(rows) for rows in response.json["sections"].values()) == 3
    return response.json


def test_library_receives_saved_method_and_valid_parameters(library_route):
    library_route.settings.default_algorithm = "ArtistSpacingShuffle"
    library_route.settings.default_algorithm_params = {"min_spacing": 4, "keep_first": 3}
    db.session.commit()
    assert_library(library_route, {"algorithm": "ArtistSpacingShuffle", "params": {"min_spacing": 4}})


def test_library_without_a_saved_default_does_not_choose_basic(library_route):
    assert_library(library_route, {"algorithm": None, "params": {}})


def test_library_without_display_name_keeps_nullable_profile_facts(library_route):
    with library_route.client.session_transaction() as stored:
        stored["user_data"]["display_name"] = None
    result = assert_library(library_route, {"algorithm": None, "params": {}})
    assert result["greeting"]["display_name"] is None


@pytest.mark.parametrize("current_index", [None, 0, 1, 2])
def test_library_restore_eligibility_uses_actual_session_history(library_route, current_index):
    if current_index is not None:
        with library_route.client.session_transaction() as stored:
            stored["playlist_states"] = {"playlist1": {
                "states": [["before"], ["workshop-commit"], ["latest"]], "current_index": current_index,
            }}
    result = assert_library(library_route, {"algorithm": None, "params": {}})
    row = next(row for rows in result["sections"].values() for row in rows if row["id"] == "playlist1")
    assert row["can_undo"] is bool(current_index)


@pytest.mark.parametrize("service", [
    "DashboardService.get_dashboard_data", "UserSettingsService.get_default_shuffle_settings",
])
def test_library_personalization_failure_is_bounded_json_and_keeps_session(library_route, service):
    with patch("shuffify.routes.contexts." + service, autospec=True, side_effect=RuntimeError("private failure")):
        response = library_route.client.get("/api/library")
    assert response.status_code == 500
    assert response.json["success"] is False
    assert response.headers["Cache-Control"] == "no-store"
    assert "private failure" not in response.get_data(as_text=True)
    with library_route.client.session_transaction() as stored:
        assert stored["user_data"]["id"] == "user123"
