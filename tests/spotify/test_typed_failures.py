"""Spotify's typed failures: a rejected app, a revoked grant, an account the app does not list.

Each keeps its cause from the token endpoint or the 403 to the code that acts on it, under the
exception classes the existing handlers already catch.
"""

import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from shuffify.services import AuthenticationError, JobExecutionError
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyAppRejected,
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyGrantRevoked,
    SpotifyPartialBatchError,
    SpotifyTokenError,
    SpotifyTokenExpiredError,
    SpotifyUserNotAllowed,
    spotify_failure_code,
    spotify_failure_of,
)
from shuffify.spotify.http_client import SpotifyHTTPClient

NOT_REGISTERED = "Check settings on developer.spotify.com/dashboard, the user may not be registered."
# The other way Spotify says it.
NOT_REGISTERED_IN_DASHBOARD = "User not registered in the Developer Dashboard"


def _response(status_code=200, json_data=None, text=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data if json_data is not None else {}
    response.headers = {}
    response.text = text if text is not None else str(json_data or "")
    response.content = b"{}"
    return response


def _forbidden(message):
    return _response(403, {"error": {"status": 403, "message": message}})


@pytest.fixture
def manager():
    return SpotifyAuthManager(SpotifyCredentials("client", "secret", "http://localhost/callback"))


def _api(on_typed_failure=None):
    """A real SpotifyAPI whose HTTP session answers from ``api.answers``."""
    api = SpotifyAPI(
        TokenInfo("access", "Bearer", time.time() + 3600, refresh_token="refresh"),
        auto_refresh=False,
        on_typed_failure=on_typed_failure,
    )
    api.answers = []
    api._http._session.request = lambda *a, **kw: api.answers.pop(0)
    return api


class TestTheHierarchyIsPreserved:
    @pytest.mark.parametrize("kind", [SpotifyAppRejected, SpotifyGrantRevoked])
    def test_a_token_that_cannot_be_refreshed_still_matches_the_token_catchers(self, kind):
        for catcher in (SpotifyTokenExpiredError, SpotifyTokenError, SpotifyAuthError):
            with pytest.raises(catcher):
                raise kind("failed")
        assert not issubclass(kind, SpotifyAPIError)
        assert SpotifyAuthError not in kind.__bases__

    def test_a_user_not_allowed_is_still_a_403(self):
        for catcher in (SpotifyForbiddenError, SpotifyAPIError):
            with pytest.raises(catcher):
                raise SpotifyUserNotAllowed("failed")
        assert not issubclass(SpotifyUserNotAllowed, SpotifyAuthError)

    def test_a_typed_failure_keeps_the_oauth_error_and_description(self):
        rejected = SpotifyAppRejected("m", error="invalid_client", error_description="Invalid client")
        revoked = SpotifyGrantRevoked("m", error_description="Refresh token revoked")
        assert (rejected.error, rejected.error_description) == ("invalid_client", "Invalid client")
        assert (revoked.error, revoked.error_description) == ("invalid_grant", "Refresh token revoked")
        assert str(rejected) == "m"

    def test_the_defaults_name_the_oauth_error_each_one_stands_for(self):
        assert SpotifyAppRejected("m").error == "invalid_client"
        assert SpotifyGrantRevoked("m").error == "invalid_grant"
        assert SpotifyUserNotAllowed("m").error is None


class TestEachOAuthErrorMapsToItsException:
    @pytest.mark.parametrize("method", ["refresh", "exchange"])
    @pytest.mark.parametrize(
        "error, description, kind",
        [
            ("invalid_client", "Invalid client", SpotifyAppRejected),
            ("invalid_grant", "Refresh token revoked", SpotifyGrantRevoked),
        ],
    )
    def test_each_body_maps_to_its_type_and_keeps_its_fields(self, manager, method, error, description, kind):
        body = {"error": error, "error_description": description}
        with patch("shuffify.spotify.auth.requests.post", return_value=_response(400, body)):
            with pytest.raises(kind) as caught:
                if method == "refresh":
                    manager.refresh_token(TokenInfo("a", "Bearer", 0, refresh_token="r"))
                else:
                    manager.exchange_code("code")
        assert caught.value.error == error
        assert caught.value.error_description == description
        assert description in str(caught.value)

    @pytest.mark.parametrize("body", [{"error": "invalid_request"}, {"error_description": "Nope"}, {}])
    def test_any_other_body_stays_a_plain_token_error(self, manager, body):
        with patch("shuffify.spotify.auth.requests.post", return_value=_response(400, body)):
            with pytest.raises(SpotifyTokenError) as caught:
                manager.refresh_token(TokenInfo("a", "Bearer", 0, refresh_token="r"))
        assert type(caught.value) is SpotifyTokenError

    def test_a_body_that_is_not_json_stays_a_plain_token_error_with_its_text(self, manager):
        broken = _response(400, text="<html>bad gateway</html>")
        broken.json.side_effect = ValueError("not json")
        with patch("shuffify.spotify.auth.requests.post", return_value=broken):
            with pytest.raises(SpotifyTokenError, match="bad gateway") as caught:
                manager.refresh_token(TokenInfo("a", "Bearer", 0, refresh_token="r"))
        assert type(caught.value) is SpotifyTokenError

    def test_an_error_without_a_description_keeps_its_code(self, manager):
        with patch("shuffify.spotify.auth.requests.post", return_value=_response(400, {"error": "invalid_grant"})):
            with pytest.raises(SpotifyGrantRevoked) as caught:
                manager.refresh_token(TokenInfo("a", "Bearer", 0, refresh_token="r"))
        assert caught.value.error == "invalid_grant" and caught.value.error_description is None


class TestForbidden:
    def test_a_not_registered_403_is_a_user_not_allowed_and_still_a_forbidden(self):
        api = _api()
        api.answers = [_forbidden(NOT_REGISTERED)]
        with pytest.raises(SpotifyForbiddenError) as caught:
            api.get_current_user()
        assert type(caught.value) is SpotifyUserNotAllowed
        assert caught.value.error_description == NOT_REGISTERED
        assert str(caught.value) == f"API error 403: {NOT_REGISTERED}"

    @pytest.mark.parametrize(
        "message",
        [NOT_REGISTERED, NOT_REGISTERED_IN_DASHBOARD, "User NOT REGISTERED in the Developer Dashboard"],
    )
    def test_either_wording_is_matched_whatever_its_case(self, message):
        api = _api()
        api.answers = [_forbidden(message)]
        with pytest.raises(SpotifyUserNotAllowed):
            api.get_current_user()

    def test_a_write_403_for_an_unregistered_user_is_one_too(self):
        client = SpotifyHTTPClient("token")
        client._session = MagicMock()
        client._session.request.return_value = _forbidden(NOT_REGISTERED)
        with pytest.raises(SpotifyUserNotAllowed):
            client.put("/playlists/p/items", json={"uris": []})

    @pytest.mark.parametrize(
        "message",
        ["Forbidden", "You cannot add tracks to a playlist you don't own", "Insufficient client scope", ""],
    )
    def test_a_playlist_403_stays_a_plain_forbidden_error(self, message):
        api = _api()
        api.answers = [_forbidden(message)]
        with pytest.raises(SpotifyForbiddenError) as caught:
            api.get_playlist("p")
        assert type(caught.value) is SpotifyForbiddenError

    def test_a_403_whose_body_is_not_json_is_matched_on_its_text(self):
        client = SpotifyHTTPClient("token")
        client._session = MagicMock()
        plain = _response(403, text=NOT_REGISTERED)
        plain.json.side_effect = ValueError("not json")
        client._session.request.return_value = plain
        with pytest.raises(SpotifyUserNotAllowed):
            client.get("/me")


class TestARefreshFailedInsideA401Retry:
    """A 401, then a failed refresh, reaches the caller as the refresh's own failure."""

    @pytest.mark.parametrize("kind", [SpotifyAppRejected, SpotifyGrantRevoked])
    def test_the_typed_cause_is_reraised(self, kind):
        client = SpotifyHTTPClient("old", on_token_refresh=MagicMock(side_effect=kind("refresh failed")))
        client._session = MagicMock()
        client._session.request.return_value = _response(401)
        with pytest.raises(kind) as caught:
            client.get("/me")
        assert str(caught.value) == "refresh failed"

    def test_any_other_refresh_failure_is_still_token_expired(self):
        client = SpotifyHTTPClient("old", on_token_refresh=MagicMock(side_effect=SpotifyTokenError("boom")))
        client._session = MagicMock()
        client._session.request.return_value = _response(401)
        with pytest.raises(SpotifyTokenExpiredError, match="Token expired or invalid") as caught:
            client.get("/me")
        assert type(caught.value) is SpotifyTokenExpiredError

    def test_through_a_real_auth_manager_the_oauth_failure_surfaces(self, manager):
        api = SpotifyAPI(
            TokenInfo("access", "Bearer", time.time() + 3600, refresh_token="refresh"), manager, auto_refresh=True
        )
        api._http._session.request = lambda *a, **kw: _response(401)
        body = {"error": "invalid_grant", "error_description": "Refresh token revoked"}
        with patch("shuffify.spotify.auth.requests.post", return_value=_response(400, body)):
            with pytest.raises(SpotifyGrantRevoked) as caught:
                api.get_current_user()
        assert caught.value.error_description == "Refresh token revoked"


class TestTheFailureHook:
    def test_it_is_called_once_with_the_failure_when_a_not_registered_403_is_raised(self):
        seen = []
        api = _api(seen.append)
        api.answers = [_forbidden(NOT_REGISTERED)]
        with pytest.raises(SpotifyUserNotAllowed) as caught:
            api.get_current_user()
        assert seen == [caught.value]

    def test_it_is_not_called_for_a_playlist_403(self):
        seen = []
        api = _api(seen.append)
        api.answers = [_forbidden("Forbidden")]
        with pytest.raises(SpotifyForbiddenError):
            api.get_playlist("p")
        assert seen == []

    def test_it_is_not_called_for_failures_that_are_not_typed(self):
        seen = []
        api = _api(seen.append)
        api.answers = [_response(404, {"error": {"status": 404, "message": "Not found"}})]
        with pytest.raises(Exception):
            api.get_playlist("p")
        assert seen == []

    def test_a_hook_that_raises_does_not_change_the_failure_and_is_logged(self, caplog):
        def broken(failure):
            raise RuntimeError("hook exploded")

        api = _api(broken)
        api.answers = [_forbidden(NOT_REGISTERED)]
        with caplog.at_level(logging.WARNING, logger="shuffify.spotify.http_client"):
            with pytest.raises(SpotifyUserNotAllowed) as caught:
                api.get_current_user()
        assert str(caught.value) == f"API error 403: {NOT_REGISTERED}"
        assert "on_typed_failure hook failed" in caplog.text and "hook exploded" in caplog.text

    def test_with_no_hook_nothing_changes(self):
        api = _api()
        api.answers = [_forbidden(NOT_REGISTERED)]
        with pytest.raises(SpotifyUserNotAllowed):
            api.get_current_user()

    def test_the_api_hands_its_hook_to_the_http_client_it_builds(self):
        def hook(failure):
            pass

        assert _api(hook)._http._on_typed_failure is hook
        assert _api()._http._on_typed_failure is None

    def test_the_http_client_takes_the_hook_directly(self):
        seen = []
        client = SpotifyHTTPClient("token", on_typed_failure=seen.append)
        client._session = MagicMock()
        client._session.request.return_value = _forbidden(NOT_REGISTERED)
        with pytest.raises(SpotifyUserNotAllowed) as caught:
            client.get("/me")
        assert seen == [caught.value]

    def test_a_service_that_catches_the_403_cannot_hide_it(self):
        """PlaylistService.get_playlist re-maps a 403 to PlaylistAccessError; the hook has fired by then."""
        from shuffify.services.playlist_service import PlaylistAccessError, PlaylistService

        seen = []
        api = _api(seen.append)
        api.answers = [_forbidden(NOT_REGISTERED)]
        with pytest.raises(PlaylistAccessError) as caught:
            PlaylistService(api).get_playlist("p" * 22)
        assert isinstance(caught.value.__cause__, SpotifyUserNotAllowed)
        assert seen == [caught.value.__cause__]


class TestSpotifyFailureOf:
    def test_it_finds_a_typed_failure_given_directly(self):
        failure = SpotifyAppRejected("m")
        assert spotify_failure_of(failure) is failure

    @pytest.mark.parametrize("kind", [SpotifyAppRejected, SpotifyGrantRevoked, SpotifyUserNotAllowed])
    def test_it_finds_each_kind(self, kind):
        failure = kind("m")
        assert spotify_failure_of(JobExecutionError("wrapped", cause=failure)) is failure

    def test_it_finds_the_cause_through_a_job_execution_error(self):
        failure = SpotifyGrantRevoked("m")
        assert spotify_failure_of(JobExecutionError("Failed to refresh the Spotify token.", cause=failure)) is failure

    def test_it_finds_the_dunder_cause_through_a_job_execution_error(self):
        failure = SpotifyGrantRevoked("m")
        try:
            try:
                raise failure
            except SpotifyTokenError as error:
                raise JobExecutionError("Failed to refresh the Spotify token.") from error
        except JobExecutionError as wrapped:
            assert wrapped.cause is None
            assert spotify_failure_of(wrapped) is failure

    def test_it_finds_the_cause_through_an_authentication_error(self):
        failure = SpotifyUserNotAllowed("m")
        assert spotify_failure_of(AuthenticationError("Failed to fetch user profile", cause=failure)) is failure

    def test_it_finds_the_dunder_cause_through_an_authentication_error(self):
        failure = SpotifyAppRejected("m")
        try:
            try:
                raise failure
            except SpotifyTokenError as error:
                raise AuthenticationError("Invalid or expired token") from error
        except AuthenticationError as wrapped:
            assert spotify_failure_of(wrapped) is failure

    def test_it_finds_the_cause_of_a_partial_batch_error(self):
        failure = SpotifyUserNotAllowed("m")
        partial = SpotifyPartialBatchError("p", "add", 1, 2, ["a"], ["b"], cause=failure)
        assert spotify_failure_of(partial) is failure

    def test_it_finds_one_behind_several_wrappers(self):
        failure = SpotifyGrantRevoked("m")
        assert spotify_failure_of(JobExecutionError("a", cause=AuthenticationError("b", cause=failure))) is failure

    def test_it_returns_none_for_an_unrelated_error(self):
        assert spotify_failure_of(ValueError("nope")) is None
        assert spotify_failure_of(JobExecutionError("nope")) is None
        assert spotify_failure_of(AuthenticationError("nope", cause=SpotifyTokenError("plain"))) is None
        assert spotify_failure_of(SpotifyForbiddenError("a playlist")) is None
        assert spotify_failure_of(None) is None

    def test_a_cycle_in_the_causes_ends_the_search(self):
        first, second = JobExecutionError("a"), JobExecutionError("b")
        first.cause, second.cause = second, first
        assert spotify_failure_of(first) is None

    def test_it_reads_a_context_only_when_it_is_a_cause(self):
        """A failure merely in flight while another error was raised is not that error's cause."""
        try:
            try:
                raise SpotifyAppRejected("m")
            except SpotifyAppRejected:
                raise JobExecutionError("unrelated")
        except JobExecutionError as wrapped:
            assert spotify_failure_of(wrapped) is None


class TestFailureCodes:
    @pytest.mark.parametrize(
        "failure, code",
        [
            (SpotifyAppRejected("m"), "spotify_app_rejected"),
            (SpotifyGrantRevoked("m"), "spotify_grant_revoked"),
            (SpotifyUserNotAllowed("m"), "spotify_user_not_allowed"),
        ],
    )
    def test_each_failure_has_its_code(self, failure, code):
        assert spotify_failure_code(failure) == code

    def test_an_untyped_error_has_none(self):
        with pytest.raises(TypeError):
            spotify_failure_code(SpotifyForbiddenError("m"))


class TestTheExistingHandlerStillAnswersTheTokenFailures:
    @pytest.mark.parametrize("kind", [SpotifyAppRejected, SpotifyGrantRevoked])
    def test_handle_spotify_token_expired_still_returns_its_401(self, app, kind):
        with app.test_request_context("/api/anything"):
            response, status = app.handle_user_exception(kind("m"))
        assert status == 401
        assert response.get_json()["message"] == "Session expired. Please log in again."

    def test_a_user_not_allowed_is_answered_by_the_handler_for_its_parent(self, app):
        with app.test_request_context("/api/anything"):
            typed = app.handle_user_exception(SpotifyUserNotAllowed("m"))
            plain = app.handle_user_exception(SpotifyForbiddenError("m"))
        assert typed[1] == plain[1]
        assert typed[0].get_json() == plain[0].get_json()
