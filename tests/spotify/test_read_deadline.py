"""Spotify reads share a monotonic deadline through retries, pages and OAuth."""

import math
import time
from contextvars import copy_context
from unittest.mock import MagicMock, patch

import pytest
import requests

from shuffify.services.auth_service import AuthService
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.deadline import (
    SpotifyDeadlineError,
    bounded_sleep,
    remaining_seconds,
    remaining_timeout,
    spotify_deadline,
)
from shuffify.spotify.exceptions import SpotifyRateLimitError
from shuffify.spotify.http_client import SpotifyHTTPClient


class Clock:
    def __init__(self):
        self.elapsed = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.elapsed

    def advance(self, seconds):
        self.elapsed += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance(seconds)


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr('shuffify.spotify.deadline.time.monotonic', clock.monotonic)
    monkeypatch.setattr('shuffify.spotify.deadline.time.sleep', clock.sleep)
    return clock


def response(status=200, body=None, retry_after=None):
    result = requests.Response()
    result.status_code = status
    result._content = b'{}'
    result.json = MagicMock(spec=requests.Response.json, return_value={} if body is None else body)
    if retry_after is not None:
        result.headers['Retry-After'] = str(retry_after)
    return result


@pytest.fixture
def client():
    instance = SpotifyHTTPClient('fixture-token')
    instance._session.request = MagicMock(spec=requests.Session.request, return_value=response())
    yield instance
    instance.close()


@pytest.fixture
def manager():
    return SpotifyAuthManager(SpotifyCredentials(
        client_id='fixture-client', client_secret='fixture-secret',
        redirect_uri='https://example.invalid/callback',
    ))


def token(expired=False):
    return TokenInfo(
        access_token='fixture-token', token_type='Bearer',
        expires_at=time.time() + (-100 if expired else 3600),
        refresh_token='fixture-refresh',
    )


def test_no_scope_preserves_transport_default(clock):
    assert remaining_seconds() is None
    assert remaining_timeout(30) == 30
    bounded_sleep(2)
    assert clock.sleeps == [2]


def test_nested_scope_can_shorten_but_never_extend_parent(clock):
    with spotify_deadline(20):
        clock.advance(3)
        with spotify_deadline(60):
            assert remaining_timeout(30) == pytest.approx(17)
        with spotify_deadline(2):
            clock.advance(1)
            assert remaining_timeout(30) == pytest.approx(1)
        assert remaining_timeout(30) == pytest.approx(16)
    assert remaining_seconds() is None


def test_context_copies_do_not_leak_new_deadlines(clock):
    with spotify_deadline(12):
        context = copy_context()
    assert context.run(remaining_seconds) == pytest.approx(12)
    assert remaining_seconds() is None


@pytest.mark.parametrize('seconds', [-1, math.inf, math.nan])
def test_invalid_deadline_budget_is_rejected(seconds):
    with pytest.raises(ValueError):
        with spotify_deadline(seconds):
            pass


def test_exception_restores_parent_scope(clock):
    with spotify_deadline(8):
        with pytest.raises(RuntimeError):
            with spotify_deadline(1):
                raise RuntimeError('fixture')
        assert remaining_seconds() == pytest.approx(8)


def test_expired_scope_dispatches_nothing(clock, client):
    with spotify_deadline(3):
        clock.advance(3)
        with pytest.raises(SpotifyDeadlineError):
            client.get('/me')
    client._session.request.assert_not_called()


def test_transport_timeout_uses_remaining_budget(clock, client):
    client._session.request.return_value = response(body={'id': 'alice'})
    with spotify_deadline(20):
        clock.advance(7)
        assert client.get('/me') == {'id': 'alice'}
    assert client._session.request.call_args.kwargs['timeout'] == pytest.approx(13)


def test_retry_after_is_returned_without_sleeping_past_read_deadline(clock, client):
    client._session.request.return_value = response(429, {'error': {'status': 429}}, retry_after=18)
    with spotify_deadline(20):
        clock.advance(5)
        with pytest.raises(SpotifyRateLimitError) as error:
            client.get('/me')
    assert error.value.retry_after == 18
    assert clock.sleeps == []
    assert client._session.request.call_count == 1


def test_huge_retry_after_is_preserved_promptly(clock, client):
    client._session.request.return_value = response(429, retry_after=86400)
    with spotify_deadline(20):
        with pytest.raises(SpotifyRateLimitError) as error:
            client.get('/me')
    assert error.value.retry_after == 86400
    assert clock.sleeps == []
    assert client._session.request.call_count == 1


def test_rate_limit_backoff_within_budget_consumes_same_deadline(clock, client):
    client._session.request.side_effect = [response(429, retry_after=4), response(body={'id': 'alice'})]
    with spotify_deadline(9):
        assert client.get('/me') == {'id': 'alice'}
    assert clock.sleeps == [4]
    assert [item.kwargs['timeout'] for item in client._session.request.call_args_list] == [9, 5]


@pytest.mark.parametrize('failure', ['server', 'network', 'json'])
def test_transient_retry_stops_before_backoff_can_exhaust_budget(clock, client, failure):
    broken = response(503) if failure == 'server' else response()
    if failure == 'json':
        broken.json.side_effect = ValueError('fixture unreadable response')
    client._session.request.side_effect = requests.Timeout('fixture timeout') if failure == 'network' else None
    if failure != 'network':
        client._session.request.return_value = broken
    with spotify_deadline(5):
        with pytest.raises(SpotifyDeadlineError):
            client.get('/me')
    assert client._session.request.call_count == 2
    assert clock.sleeps == [2]
    assert clock.elapsed < 5


def test_complete_pagination_shares_one_budget_across_pages(clock, client):
    def page(_method, _url, **kwargs):
        assert client._session.request.call_count <= 3, 'pagination continued after its complete-read budget'
        clock.advance(4)
        return response(body={'items': [{'id': 'sample'}], 'next': 'https://example.invalid/next'})

    client._session.request.side_effect = page
    with spotify_deadline(10):
        with pytest.raises(SpotifyDeadlineError):
            client.get_all_pages('/me/playlists')
    assert [item.kwargs['timeout'] for item in client._session.request.call_args_list] == [10, 6, 2]
    assert clock.sleeps == []


def test_response_json_time_consumes_read_budget(clock, client):
    result = response()

    def body():
        clock.advance(5)
        return {'id': 'alice'}

    result.json.side_effect = body
    client._session.request.return_value = result
    with spotify_deadline(4):
        with pytest.raises(SpotifyDeadlineError):
            client.get('/me')
    assert client._session.request.call_count == 1


def test_refresh_and_retried_read_share_remaining_time(clock, manager):
    api = SpotifyAPI(token(), manager, auto_refresh=True)
    calls = []

    def read(_method, _url, **kwargs):
        calls.append(kwargs['timeout'])
        if len(calls) == 1:
            clock.advance(3)
            return response(401)
        return response(body={'id': 'alice'})

    def refresh(*args, **kwargs):
        assert kwargs['timeout'] == pytest.approx(7)
        clock.advance(2)
        return response(body={'access_token': 'fixture-refreshed', 'token_type': 'Bearer', 'expires_in': 3600})

    api._http._session.request = MagicMock(spec=requests.Session.request, side_effect=read)
    with patch('shuffify.spotify.auth.request_with_deadline', side_effect=refresh):
        with spotify_deadline(10):
            assert api.get_current_user()['id'] == 'alice'
    assert calls == [10, 5]


def test_expired_api_construction_caps_token_refresh(clock, manager):
    with patch('shuffify.spotify.auth.request_with_deadline', return_value=response(body={
        'access_token': 'fixture-refreshed', 'token_type': 'Bearer', 'expires_in': 3600,
    })) as post:
        with spotify_deadline(7):
            clock.advance(2)
            api = SpotifyAPI(token(expired=True), manager, auto_refresh=True)
        assert api.token_info.access_token == 'fixture-refreshed'
    assert post.call_args.kwargs['timeout'] == pytest.approx(5)


def test_exhaustion_during_refresh_remains_deadline_error(clock, manager):
    api = SpotifyAPI(token(), manager, auto_refresh=True)
    api._http._session.request = MagicMock(spec=requests.Session.request, return_value=response(401))

    def refresh(*args, **kwargs):
        clock.advance(6)
        raise SpotifyDeadlineError('Spotify request deadline exceeded.')

    with patch('shuffify.spotify.auth.request_with_deadline', side_effect=refresh):
        with spotify_deadline(5):
            with pytest.raises(SpotifyDeadlineError):
                api.get_current_user()
    assert api._http._session.request.call_count == 1


def test_auth_service_does_not_turn_budget_exhaustion_into_invalid_login(clock, manager):
    with (
        patch.object(AuthService, "_auth_manager", return_value=manager),
        patch("shuffify.services.auth_service.SpotifyAPI", autospec=True,
              side_effect=SpotifyDeadlineError("fixture deadline")),
    ):
        with pytest.raises(SpotifyDeadlineError):
            AuthService.get_authenticated_api(token().to_dict())


def test_oauth_exchange_and_revocation_cap_http_timeout(clock, manager):
    with patch('shuffify.spotify.auth.request_with_deadline', return_value=response(body={
        'access_token': 'fixture-exchanged', 'token_type': 'Bearer', 'expires_in': 3600,
    })) as post:
        with spotify_deadline(1.5):
            manager.exchange_code('fixture-code')
            manager.revoke_token('fixture-token')
    assert [item.kwargs['timeout'] for item in post.call_args_list] == [1.5, 1.5]


@pytest.mark.parametrize('method', ['exchange_code', 'refresh_token'])
def test_token_rate_limits_preserve_provider_delay(clock, manager, method):
    argument = 'fixture-code' if method == 'exchange_code' else token()
    with patch('shuffify.spotify.auth.request_with_deadline', return_value=response(429, retry_after=25)):
        with spotify_deadline(5):
            with pytest.raises(SpotifyRateLimitError) as error:
                getattr(manager, method)(argument)
    assert error.value.retry_after == 25
    assert clock.sleeps == []


def test_expired_budget_never_starts_token_refresh(clock, manager):
    with patch('shuffify.spotify.auth.request_with_deadline') as post:
        with spotify_deadline(0):
            with pytest.raises(SpotifyDeadlineError):
                manager.refresh_token(token())
    post.assert_not_called()


def test_profile_service_preserves_typed_budget_error():
    api = MagicMock(spec=SpotifyAPI)
    api.get_current_user.side_effect = SpotifyDeadlineError('fixture deadline')
    with pytest.raises(SpotifyDeadlineError):
        AuthService.get_user_data(api)


def test_long_operation_scope_keeps_short_attempts_and_remaining_time(clock, client):
    client._session.request.return_value = response(body={'id': 'alice'})
    with spotify_deadline(90):
        client.get('/me')
        clock.advance(80)
        client.get('/me')
    assert [item.kwargs['timeout'] for item in client._session.request.call_args_list] == [30, 10]


def test_refresh_after_budget_expires_never_restarts_api_read(clock, manager):
    api = SpotifyAPI(token(), manager, auto_refresh=True)
    api._http._session.request = MagicMock(spec=requests.Session.request, return_value=response(401))

    def refresh(*args, **kwargs):
        clock.advance(6)
        return response(body={'access_token': 'fixture-refreshed', 'token_type': 'Bearer', 'expires_in': 3600})

    with patch('shuffify.spotify.auth.request_with_deadline', side_effect=refresh):
        with spotify_deadline(5):
            with pytest.raises(SpotifyDeadlineError):
                api.get_current_user()
    assert api._http._session.request.call_count == 1
    assert api.token_info.access_token == 'fixture-refreshed'


def test_rate_limit_arriving_at_deadline_keeps_provider_retry_delay(clock, client):
    def limited(*args, **kwargs):
        clock.advance(5)
        return response(429, retry_after=11)

    client._session.request.side_effect = limited
    with spotify_deadline(5):
        with pytest.raises(SpotifyRateLimitError) as error:
            client.get('/me')
    assert error.value.retry_after == 11
    assert clock.sleeps == []


def test_public_page_rate_limit_never_retries_before_provider_delay(clock):
    from shuffify.services.source_resolver.public_scraper_pathway import PublicScraperPathway

    with patch('shuffify.services.source_resolver.public_scraper_pathway.request_with_deadline',
               return_value=response(429, retry_after=120)) as get:
        with spotify_deadline(20):
            with pytest.raises(SpotifyRateLimitError) as error:
                PublicScraperPathway()._scrape_embed('alice')
    assert error.value.retry_after == 120
    assert get.call_count == 1
    assert clock.sleeps == []


def test_public_embed_and_fallback_share_remaining_budget(clock):
    from types import SimpleNamespace

    from shuffify.services.source_resolver.public_scraper_pathway import PublicScraperPathway

    pathway = PublicScraperPathway()
    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs['timeout'])
        clock.advance(4)
        return response(404) if len(calls) == 1 else response()

    source = SimpleNamespace(source_type='external', source_playlist_id='alice')
    with (patch.object(pathway, '_get_cached', return_value=None),
          patch.object(pathway, '_set_cached') as cache,
          patch('shuffify.services.source_resolver.public_scraper_pathway.request_with_deadline', side_effect=get)):
        with spotify_deadline(6):
            with pytest.raises(SpotifyDeadlineError):
                pathway.resolve(source)
    assert calls == [6, 2]
    cache.assert_not_called()


@pytest.mark.parametrize('failure', ['server', 'network'])
def test_public_page_backoff_stops_within_workflow_budget(clock, failure):
    from shuffify.services.source_resolver.public_scraper_pathway import PublicScraperPathway

    with (patch('shuffify.services.source_resolver.public_scraper_pathway.random.uniform', return_value=0),
          patch('shuffify.services.source_resolver.public_scraper_pathway.request_with_deadline',
                side_effect=requests.Timeout('fixture') if failure == 'network' else None,
                return_value=response(503)) as get):
        with spotify_deadline(2):
            with pytest.raises(SpotifyDeadlineError):
                PublicScraperPathway()._scrape_embed('alice')
    assert get.call_count == 2
    assert clock.sleeps == [1]


def test_public_page_parsing_must_finish_before_read_deadline(clock):
    from shuffify.services.source_resolver.public_scraper_pathway import PublicScraperPathway

    def extract(*args):
        clock.advance(5)
        return ['spotify:track:aaaaaaaaaaaaaaaaaaaaaa']

    with (patch('shuffify.services.source_resolver.public_scraper_pathway.request_with_deadline',
                return_value=response()),
          patch('shuffify.services.source_resolver.public_scraper_pathway._extract_uris', side_effect=extract)):
        with spotify_deadline(4):
            with pytest.raises(SpotifyDeadlineError):
                PublicScraperPathway()._scrape_embed('alice')


def test_public_rate_limit_can_wait_full_header_with_long_worker_budget(clock):
    from shuffify.services.source_resolver.public_scraper_pathway import PublicScraperPathway

    with (patch('shuffify.services.source_resolver.public_scraper_pathway.random.uniform', return_value=0),
          patch('shuffify.services.source_resolver.public_scraper_pathway.request_with_deadline',
                side_effect=[response(429, retry_after=40), response()]) as get):
        with spotify_deadline(60):
            assert PublicScraperPathway()._scrape_embed('alice').confirmed is True
    assert clock.sleeps == [40]
    assert get.call_count == 2


@pytest.mark.parametrize('pathway_type', ['direct', 'search'])
def test_resolver_pathways_do_not_hide_budget_rate_limits(clock, pathway_type):
    from types import SimpleNamespace

    from shuffify.services.source_resolver.direct_api_pathway import DirectAPIPathway
    from shuffify.services.source_resolver.search_pathway import SearchPathway

    pathway = DirectAPIPathway() if pathway_type == 'direct' else SearchPathway()
    api = MagicMock(spec=SpotifyAPI)
    api.get_playlist_tracks.side_effect = SpotifyRateLimitError('fixture limited', retry_after=12)
    api.search_tracks.side_effect = SpotifyRateLimitError('fixture limited', retry_after=12)
    source = SimpleNamespace(source_type='search_query' if pathway_type == 'search' else 'own',
                             source_playlist_id='alice', search_query='sample songs')
    with spotify_deadline(3):
        with pytest.raises(SpotifyRateLimitError) as error:
            pathway.resolve(source, api=api)
    assert error.value.retry_after == 12


def test_search_deadline_cannot_turn_elapsed_budget_into_partial_success(clock):
    from types import SimpleNamespace

    from shuffify.services.source_resolver.search_pathway import SearchPathway

    def search(*args, **kwargs):
        clock.advance(3)
        return [{'uri': 'spotify:track:aaaaaaaaaaaaaaaaaaaaaa'}]

    api = MagicMock(spec=SpotifyAPI)
    api.search_tracks.side_effect = search
    source = SimpleNamespace(source_type='search_query', search_query='sample songs')
    with spotify_deadline(5):
        with pytest.raises(SpotifyDeadlineError):
            SearchPathway().resolve(source, api=api)
    assert api.search_tracks.call_count == 2


def test_public_429_without_header_still_reports_rate_limit_at_budget_end(clock):
    from shuffify.services.source_resolver.public_scraper_pathway import PublicScraperPathway

    with (patch('shuffify.services.source_resolver.public_scraper_pathway.random.uniform', return_value=0),
          patch('shuffify.services.source_resolver.public_scraper_pathway.request_with_deadline',
                return_value=response(429)) as get):
        with spotify_deadline(0.5):
            with pytest.raises(SpotifyRateLimitError) as error:
                PublicScraperPathway()._scrape_embed('alice')
    assert error.value.retry_after is None
    assert get.call_count == 1
    assert clock.sleeps == []


@pytest.mark.parametrize('phase', ['transport', 'json'])
def test_constructor_persists_rotated_credentials_before_expired_success_fails(clock, manager, phase):
    persisted = []
    result = response(body={
        'access_token': 'fixture-refreshed', 'refresh_token': 'fixture-rotated',
        'token_type': 'Bearer', 'expires_in': 3600,
    })

    def post(*args, **kwargs):
        if phase == 'transport':
            clock.advance(6)
        return result

    if phase == 'json':
        payload = result.json.return_value
        result.json.side_effect = lambda: (clock.advance(6), payload)[1]
    with patch('shuffify.spotify.auth.request_with_deadline', side_effect=post):
        with spotify_deadline(5):
            with pytest.raises(SpotifyDeadlineError):
                SpotifyAPI(token(expired=True), manager,
                           on_token_refresh=lambda value: persisted.append(value.to_dict()))
    assert persisted[0]['refresh_token'] == 'fixture-rotated'
    assert persisted[0]['access_token'] == 'fixture-refreshed'


def test_expiry_refresh_persists_rotation_before_budget_failure(clock, manager):
    persisted = []
    api = SpotifyAPI(token(), manager, on_token_refresh=lambda value: persisted.append(value.to_dict()))
    api._token_info = token(expired=True)

    def post(*args, **kwargs):
        clock.advance(6)
        return response(body={'access_token': 'fixture-refreshed', 'refresh_token': 'fixture-rotated',
                              'token_type': 'Bearer', 'expires_in': 3600})

    with patch('shuffify.spotify.auth.request_with_deadline', side_effect=post):
        with spotify_deadline(5):
            with pytest.raises(SpotifyDeadlineError):
                api._ensure_valid_token()
    assert persisted[0]['refresh_token'] == 'fixture-rotated'


@pytest.mark.parametrize('phase', ['transport', 'json'])
def test_direct_oauth_exchange_checks_completed_budget(clock, manager, phase):
    result = response(body={'access_token': 'fixture-exchanged', 'token_type': 'Bearer', 'expires_in': 3600})

    def post(*args, **kwargs):
        if phase == 'transport':
            clock.advance(6)
        return result

    if phase == 'json':
        payload = result.json.return_value
        result.json.side_effect = lambda: (clock.advance(6), payload)[1]
    with patch('shuffify.spotify.auth.request_with_deadline', side_effect=post):
        with spotify_deadline(5):
            with pytest.raises(SpotifyDeadlineError):
                manager.exchange_code('fixture-code')


def test_unscoped_oauth_rate_limit_keeps_authentication_error_contract(manager):
    from shuffify.services.auth_service import AuthenticationError

    with patch.object(AuthService, '_auth_manager', return_value=manager):
        with patch.object(manager, 'exchange_code', side_effect=SpotifyRateLimitError('fixture limit', retry_after=25)):
            with pytest.raises(AuthenticationError):
                AuthService.exchange_code_for_token('fixture-code')


def test_oauth_callback_rate_limit_redirects_and_keeps_one_time_notice(db_app, manager):
    with db_app.test_client() as web_client:
        with web_client.session_transaction() as saved_session:
            saved_session['oauth_state'] = 'fixture-state'
        with patch.object(AuthService, '_auth_manager', return_value=manager):
            with patch.object(manager, 'exchange_code',
                              side_effect=SpotifyRateLimitError('fixture limited', retry_after=25)):
                reply = web_client.get('/callback?code=fixture-code&state=fixture-state')
        assert reply.status_code == 302
        assert reply.headers['Location'] == '/'
        with web_client.session_transaction() as saved_session:
            assert saved_session['auth_notice'] == 'authentication_failed'
