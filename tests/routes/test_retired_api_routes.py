"""Retired API routes cannot authenticate, read Spotify or mutate data."""

from unittest.mock import patch

import pytest

from tests.routes.test_api_namespace import assert_envelope


@pytest.mark.parametrize("method,path,status", [
    ('GET', '/api/playlist/example-playlist', 404),
    ('GET', '/api/playlist/example-playlist/stats', 404),
    ('POST', '/api/playlist/example-playlist/raid-watch', 404),
    ('POST', '/api/playlist/example-playlist/raid-watch-search', 404),
    ('POST', '/api/playlist/example-playlist/raid-schedule-toggle', 404),
    ('POST', '/api/playlist/example-playlist/drip-schedule-toggle', 404),
    ('POST', '/api/playlist/example-playlist/raid-schedule-create', 404),
    ('PUT', '/api/playlist/example-playlist/raid-schedule', 404),
    ('GET', '/api/playlist/example-playlist/raid-schedule/history', 404),
    ('POST', '/api/playlist/example-playlist/pending-raids/promote', 404),
    ('POST', '/api/playlist/example-playlist/pending-raids/unpromote', 404),
    ('POST', '/api/playlist/example-playlist/pending-raids/promote-all', 404),
    ('POST', '/api/playlist/example-playlist/pending-raids/finalize-all', 404),
    ('GET', '/api/playlist/example-playlist/schedules', 404),
    ('POST', '/api/schedules/create', 404),
    ('PUT', '/api/schedules/1', 405),
    ('POST', '/api/schedules/1/toggle', 404),
    ('GET', '/api/playlist/example-playlist/rotation-status', 404),
    ('POST', '/api/workshop/search-playlists', 405),
])
def test_retired_route_uses_routing_error_without_running_services(app, method, path, status):
    with patch("shuffify.routes.require_auth", autospec=True) as authenticate, patch(
        "shuffify.routes.get_db_user", autospec=True
    ) as user, patch("shuffify.spotify.http_client.SpotifyHTTPClient._request_url", autospec=True) as spotify:
        response = app.test_client().open(path, method=method, json={})
    assert response.status_code == status
    assert_envelope(response)
    authenticate.assert_not_called()
    user.assert_not_called()
    spotify.assert_not_called()


def test_requested_direct_commands_remain_registered(app):
    rules = {(rule.rule, method) for rule in app.url_map.iter_rules() for method in rule.methods}
    assert ("/api/playlist/<playlist_id>/pair/unarchive", "POST") in rules
    assert ("/api/playlist/<playlist_id>/pending-raids/dismiss-all", "POST") in rules
