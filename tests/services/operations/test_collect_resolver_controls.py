"""Source fallback must never consume durable worker controls."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from shuffify.services.operations.journal import NeedsAttention, OperationAborted, OperationDeferred
from shuffify.services.operations.repository import LeaseLost
from shuffify.services.source_resolver.direct_api_pathway import DirectAPIPathway
from shuffify.services.source_resolver.public_scraper_pathway import PublicScraperPathway
from shuffify.services.source_resolver.search_pathway import SearchPathway


@pytest.mark.parametrize('pathway', ['direct', 'search', 'scraper'])
@pytest.mark.parametrize('kind', ['lease', 'attention', 'abort', 'deferred'])
def test_control_escapes_the_source_pathway_before_any_fallback(pathway, kind, monkeypatch):
    error = {'lease': lambda: LeaseLost('lost'), 'attention': lambda: NeedsAttention('review'),
             'abort': lambda: OperationAborted('stop'),
             'deferred': lambda: OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=120))}[kind]()

    def fail(*args, **kwargs):
        raise error

    source = SimpleNamespace(source_playlist_id='upstream', search_query='query', source_type='own')
    api = SimpleNamespace(get_playlist_tracks=fail, search_tracks=fail)
    with pytest.raises(type(error)) as caught:
        if pathway == 'direct':
            DirectAPIPathway().resolve(source, api)
        elif pathway == 'search':
            SearchPathway().resolve(source, api)
        else:
            monkeypatch.setattr('shuffify.services.source_resolver.public_scraper_pathway.request_with_deadline', fail)
            PublicScraperPathway()._scrape_embed('upstream')
    assert caught.value is error


@pytest.mark.parametrize('kind', ['attention', 'abort', 'deferred'])
def test_partial_track_metadata_does_not_swallow_control_after_one_success(kind):
    from shuffify.spotify.api import SpotifyAPI
    from tests.services.operations.test_crash_recovery import uri

    api = object.__new__(SpotifyAPI)
    api._ensure_valid_token = lambda: None
    error = {'attention': lambda: NeedsAttention('review'), 'abort': lambda: OperationAborted('stop'),
             'deferred': lambda: OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=120))}[kind]()

    def read(path):
        if path.endswith('0000000000000000000002'):
            raise error
        return {'uri': uri(1)}

    api._http = SimpleNamespace(get=read)
    with pytest.raises(type(error)) as caught:
        api.get_tracks([uri(1), uri(2)])
    assert caught.value is error
