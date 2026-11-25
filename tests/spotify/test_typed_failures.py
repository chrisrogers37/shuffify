"""The Spotify failures the app acts on: typed where they are raised, found wherever they are wrapped.

A refused app (OAuth ``invalid_client``), a refused grant (``invalid_grant``)
and a user who isn't on the app's list (a 403 saying so) each raise their own
type, under the class that code already catches, and keep what Spotify said.
A hook given to the client sees the 403 where it is raised, before any caller
can catch it.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from shuffify.services.auth_service import AuthenticationError
from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistService
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyAppRejected,
    SpotifyForbiddenError,
    SpotifyGrantRevoked,
    SpotifyPartialBatchError,
    SpotifyTokenError,
    SpotifyTokenExpiredError,
    SpotifyUserNotAllowed,
    spotify_failure_of,
)
from shuffify.spotify.http_client import SpotifyHTTPClient

NOT_REGISTERED = "User not registered in the Developer Dashboard"
OAUTH_FAILURES = [("invalid_client", SpotifyAppRejected), ("invalid_grant", SpotifyGrantRevoked)]


def _response(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def _api_error(status, message):
    return _response(status, {"error": {"status": status, "message": message}})


@pytest.fixture
def manager():
    return SpotifyAuthManager(SpotifyCredentials("client-id", "client-secret", "http://127.0.0.1:8000/callback"))


def _token(expires_in=3600):
    return TokenInfo(
        access_token="access", token_type="Bearer", expires_at=time.time() + expires_in, refresh_token="refresh"
    )


def _client(responses, **hooks):
    client = SpotifyHTTPClient("access", **hooks)
    client._session.request = MagicMock(side_effect=responses)
    return client


def _api(responses, hook=None):
    api = SpotifyAPI(_token(), MagicMock(spec=SpotifyAuthManager), on_typed_failure=hook)
    api._http._session.request = MagicMock(side_effect=responses)
    return api


# ---------------------------------------------------------------------------
# The token endpoint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("error, failure", OAUTH_FAILURES)
def test_a_refused_refresh_raises_the_failure_its_oauth_error_names(manager, error, failure):
    body = {"error": error, "error_description": "Refused for the test"}
    with patch("shuffify.spotify.auth.requests.post", return_value=_response(400, body)):
        with pytest.raises(failure) as raised:
            manager.refresh_token(_token(-1))
    assert (raised.value.error, raised.value.error_description) == (error, "Refused for the test")
    assert str(raised.value) == "Token refresh failed: Refused for the test"


@pytest.mark.parametrize("error, failure", OAUTH_FAILURES)
def test_a_refused_code_exchange_raises_the_failure_its_oauth_error_names(manager, error, failure):
    body = {"error": error, "error_description": "Refused for the test"}
    with patch("shuffify.spotify.auth.requests.post", return_value=_response(400, body)):
        with pytest.raises(failure) as raised:
            manager.exchange_code("code")
    assert (raised.value.error, raised.value.error_description) == (error, "Refused for the test")
    assert str(raised.value) == "Token exchange failed: Refused for the test"


@pytest.mark.parametrize(
    "body",
    [
        {"error": "invalid_request", "error_description": "Missing grant"},
        {"error_description": "Invalid refresh token"},
        {"error": {"status": 400, "message": "Bad request"}},
    ],
)
def test_any_other_refused_token_request_stays_a_plain_token_error(manager, body):
    with patch("shuffify.spotify.auth.requests.post", return_value=_response(400, body)):
        with pytest.raises(SpotifyTokenError) as raised:
            manager.refresh_token(_token(-1))
    assert type(raised.value) is SpotifyTokenError


@pytest.mark.parametrize("error, failure", OAUTH_FAILURES)
def test_a_refresh_that_cannot_succeed_is_still_an_expired_token_to_its_catchers(error, failure):
    refusal = failure("Token refresh failed: refused", error=error)
    for caught_as in (SpotifyTokenExpiredError, SpotifyTokenError):
        with pytest.raises(caught_as):
            raise refusal


@pytest.mark.parametrize(
    "typed, plain",
    [
        (SpotifyAppRejected("Token refresh failed: x", error="invalid_client"), SpotifyTokenExpiredError("expired")),
        (SpotifyGrantRevoked("Token refresh failed: x", error="invalid_grant"), SpotifyTokenExpiredError("expired")),
        (SpotifyUserNotAllowed(f"API error 403: {NOT_REGISTERED}"), SpotifyForbiddenError("API error 403: Forbidden")),
    ],
)
def test_a_typed_failure_reaching_flask_gets_the_answer_its_base_class_gets(app, typed, plain):
    current = {}

    def fail():
        raise current["error"]

    app.add_url_rule("/raise-for-test", "raise_for_test", fail)
    client = app.test_client()
    answers = []
    for error in (plain, typed):
        current["error"] = error
        response = client.get("/raise-for-test")
        answers.append((response.status_code, response.get_json()["message"]))
    assert answers[1] == answers[0]
    if isinstance(typed, SpotifyTokenExpiredError):
        assert answers[1] == (401, "Session expired. Please log in again.")


# ---------------------------------------------------------------------------
# The Web API client
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [NOT_REGISTERED, "Check settings on developer.spotify.com/dashboard, the user may not be registered."],
)
def test_a_403_saying_the_user_is_not_registered_is_user_not_allowed_and_still_a_403(message):
    client = _client([_api_error(403, message)])
    with pytest.raises(SpotifyForbiddenError) as raised:
        client.get("/me")
    assert type(raised.value) is SpotifyUserNotAllowed
    assert raised.value.error_description == message
    assert str(raised.value) == f"API error 403: {message}"


def test_a_playlist_403_stays_a_plain_403():
    hook = MagicMock()
    client = _client([_api_error(403, "Forbidden")], on_typed_failure=hook)
    with pytest.raises(SpotifyForbiddenError) as raised:
        client.get("/playlists/p1/items")
    assert type(raised.value) is SpotifyForbiddenError
    hook.assert_not_called()


@pytest.mark.parametrize("error, failure", OAUTH_FAILURES)
def test_a_401_whose_refresh_is_refused_raises_the_refusal(error, failure):
    refusal = failure("Token refresh failed: refused", error=error)
    refresh = MagicMock(side_effect=refusal)
    client = _client([_api_error(401, "The access token expired")], on_token_refresh=refresh)
    with pytest.raises(SpotifyTokenExpiredError) as raised:
        client.get("/me")
    assert raised.value is refusal
    refresh.assert_called_once_with()


def test_the_hook_sees_a_not_registered_403_once_where_it_is_raised():
    seen = []
    api = _api([_api_error(403, NOT_REGISTERED)], seen.append)
    with pytest.raises(SpotifyUserNotAllowed) as raised:
        api.get_current_user()
    assert seen == [raised.value]


def test_the_hook_never_sees_a_playlist_403():
    seen = []
    api = _api([_api_error(403, "Forbidden")], seen.append)
    with pytest.raises(SpotifyForbiddenError):
        api.get_current_user()
    assert seen == []


def test_a_hook_that_raises_changes_nothing_the_caller_sees():
    def broken(failure):
        raise RuntimeError("the hook failed")

    with pytest.raises(SpotifyUserNotAllowed) as with_hook:
        _api([_api_error(403, NOT_REGISTERED)], broken).get_current_user()
    with pytest.raises(SpotifyUserNotAllowed) as without_hook:
        _api([_api_error(403, NOT_REGISTERED)]).get_current_user()
    assert str(with_hook.value) == str(without_hook.value)


def test_the_hook_sees_the_403_under_a_service_that_catches_it():
    """PlaylistService re-maps a 403 to PlaylistAccessError; the hook has already seen it."""
    seen = []
    api = _api([_api_error(403, NOT_REGISTERED)], seen.append)
    with pytest.raises(PlaylistAccessError) as raised:
        PlaylistService(api).get_playlist("p1")
    assert len(seen) == 1 and isinstance(seen[0], SpotifyUserNotAllowed)
    assert spotify_failure_of(raised.value) is seen[0]


def test_a_refresh_refused_before_a_call_is_still_a_generic_api_error_with_the_refusal_as_its_cause():
    """A token that expired after the client was built is refreshed before the next call."""
    refusal = SpotifyGrantRevoked("Token refresh failed: Refresh token revoked", error="invalid_grant")
    api = _api([])
    api._auth_manager.ensure_valid_token.side_effect = refusal
    api._token_info = _token(-1)
    with pytest.raises(SpotifyAPIError) as raised:
        api.get_current_user()
    assert type(raised.value) is SpotifyAPIError
    assert raised.value.__cause__ is refusal
    assert spotify_failure_of(raised.value) is refusal


# ---------------------------------------------------------------------------
# The failure behind a wrapper
# ---------------------------------------------------------------------------


def test_spotify_failure_of_finds_the_failure_through_the_wrappers_that_carry_it():
    refusal = SpotifyAppRejected("Token refresh failed: Invalid client", error="invalid_client")
    try:
        try:
            raise refusal
        except SpotifyTokenError as error:
            raise JobExecutionError("Failed to refresh the Spotify token.", cause=error) from error
    except JobExecutionError as error:
        job = error
    not_allowed = SpotifyUserNotAllowed(f"API error 403: {NOT_REGISTERED}")
    batch = SpotifyPartialBatchError("p1", "update", 1, 2, ["a"], ["b"], cause=not_allowed)

    assert spotify_failure_of(job) is refusal
    assert spotify_failure_of(AuthenticationError("Invalid or expired token", cause=refusal)) is refusal
    assert spotify_failure_of(batch) is not_allowed
    assert spotify_failure_of(refusal) is refusal


def test_spotify_failure_of_is_none_for_any_other_error():
    looped = ValueError("first")
    looped.__cause__ = ValueError("second")
    looped.__cause__.__cause__ = looped
    for error in (
        None,
        ValueError("unrelated"),
        JobExecutionError("A known algorithm is required for shuffle"),
        AuthenticationError("Failed to fetch user profile", cause=SpotifyForbiddenError("API error 403: Forbidden")),
        SpotifyTokenExpiredError("Token expired or invalid"),
        looped,
    ):
        assert spotify_failure_of(error) is None
