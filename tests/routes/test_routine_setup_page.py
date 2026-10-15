"""Dedicated guided-task page and the exact same-target setup return."""

from unittest.mock import patch

import pytest

from shuffify.models.db import Schedule, User
from shuffify.routes.playlist_setup import return_link
from tests.playlist_setup_fixtures import SetupSpotify

pytestmark = pytest.mark.usefixtures("no_scraping")
BASE = "/playlist/target/routines/new"


def test_signed_out_browser_redirects_and_never_caches(db_app):
    with db_app.test_client() as client:
        response = client.get(BASE)
    assert response.status_code == 302
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("suffix", ["?schedule_id=0", "?schedule_id=-1", "?schedule_id=nope", "?from=external"])
def test_invalid_exact_selection_and_origin_do_not_become_create(auth_client, suffix):
    with patch("shuffify.routes.require_auth", return_value=SetupSpotify()):
        response = auth_client.get(BASE + suffix)
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"


def test_page_is_read_only_and_bootstraps_exact_id_and_same_origin_urls(auth_client, app_ctx):
    before = Schedule.query.count()
    owner = User.query.filter_by(spotify_id="user123").one()
    with patch("shuffify.routes.require_auth", return_value=SetupSpotify()):
        response = auth_client.get(BASE + "?schedule_id=17&from=routines")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert html.count("<h1") == 1
    assert 'id="routine-task"' in html
    assert 'id="routine-close" href="/schedules"' in html
    assert '"requestedScheduleId": 17' in html
    assert f'"ownerId": {owner.id}' in html
    assert '"context": "/api/playlist/target/routines/context"' in html
    assert 'src="/static/js/routines.js" defer' in html
    assert "onclick=" not in html and "onchange=" not in html
    assert response.headers.get("Content-Security-Policy")
    assert Schedule.query.count() == before


@pytest.mark.parametrize("value,expected", [
    ("/playlist/target/routines/new", "/playlist/target/routines/new"),
    ("/playlist/target/routines/new?schedule_id=7", "/workshop/target"),
    ("/playlist/other/routines/new", "/workshop/target"),
    ("/playlist/target/routines/new#edit", "/workshop/target"),
    ("https://evil.example/playlist/target/routines/new", "/workshop/target"),
])
def test_setup_return_is_only_exact_same_target_base_url(db_app, value, expected):
    with db_app.test_request_context():
        result = return_link("target", value)
    assert result["href"] == expected
    assert result["label"] == ("Back to routine" if expected.endswith("/routines/new") else "Back to playlist")
