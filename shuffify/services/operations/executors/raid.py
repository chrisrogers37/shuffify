"""Freeze independent discovery results before applying retained Collect contributions."""

import random
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from shuffify.enums import ActivityType
from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.services.operations.control import propagate_control
from shuffify.services.source_resolver import SourceResolver
from shuffify.services.source_resolver.base import ResolveResult
from shuffify.services.source_resolver.direct_api_pathway import DirectAPIPathway
from shuffify.services.source_resolver.public_scraper_pathway import CACHE_TTL, PublicScraperPathway
from shuffify.services.source_resolver.search_pathway import SearchPathway

from .collect_common import apply, validate

MAX_METADATA_LOOKUPS = 50


@dataclass(frozen=True)
class RaidSource:
    id: int | None
    source_type: str
    source_playlist_id: str | None
    source_name: str | None = None
    search_query: str | None = None
    raid_count: int = 5
    source_url: str | None = None


@dataclass(frozen=True)
class RaidConfig:
    user_id: int
    target_playlist_id: str
    collection_playlist_id: str | None = None
    archive_playlist_id: str | None = None
    sources: tuple[RaidSource, ...] = ()
    extra_source_playlist_ids: tuple[str, ...] = ()
    pending_track_uris: tuple[str, ...] = ()

    def __post_init__(self):
        for field in ('sources', 'extra_source_playlist_ids', 'pending_track_uris'):
            object.__setattr__(self, field, tuple(getattr(self, field)))

    def document(self):
        value = asdict(self)
        value['sources'] = [asdict(source) for source in self.sources]
        for field in ('extra_source_playlist_ids', 'pending_track_uris'):
            value[field] = list(getattr(self, field))
        return value


class _PureScraper(PublicScraperPathway):
    """Use detached cache reads and return cache writes for the final fenced receipt."""

    def __init__(self, cached):
        self.cached, self.updates = deepcopy(cached), []

    def _get_cached(self, playlist_id):
        return deepcopy(self.cached.get(playlist_id))

    def _set_cached(self, playlist_id, uris, pathway='unknown'):
        self.updates.append({'playlist_id': playlist_id, 'track_uris': list(uris), 'pathway': pathway,
                             'scraped_at': datetime.now(timezone.utc).isoformat(), 'ttl_seconds': CACHE_TTL})


def _validate(runtime, config, prepare_key):
    if not isinstance(config, RaidConfig):
        raise JobExecutionError("Collect requires captured configuration")
    if config.target_playlist_id in (config.collection_playlist_id, config.archive_playlist_id):
        raise JobExecutionError("The collection and archive must be distinct from the target")
    validate(runtime, config, prepare_key, tuple(dict.fromkeys(
        (config.target_playlist_id, config.collection_playlist_id, config.archive_playlist_id),
    )))
    if (any(not isinstance(source, RaidSource) or source.source_type not in {'own', 'external', 'search_query'}
            or type(source.raid_count) is not int or not 1 <= source.raid_count <= 100
            or source.id is not None and (type(source.id) is not int or source.id < 1)
            or source.source_type == 'search_query' and not source.search_query
            or source.source_type != 'search_query' and not source.source_playlist_id
            for source in config.sources)
            or any(not isinstance(value, str) or not value for value in config.extra_source_playlist_ids)
            or any(not isinstance(value, str) or not value for value in config.pending_track_uris)):
        raise JobExecutionError("Collect requires normalized sources and pending exclusions")


def _sources(config):
    sources = list(config.sources)
    found = {source.source_playlist_id for source in sources if source.source_playlist_id}
    for playlist_id in config.extra_source_playlist_ids:
        if playlist_id not in found:
            sources.append(RaidSource(None, 'external', playlist_id))
            found.add(playlist_id)
    return sources


def _resolver(runtime, sources):
    cached = {}
    repository = getattr(runtime, 'repository', None)
    if repository is not None:
        from .collect_storage import read_scraper_cache

        # Release the short read session before any source HTTP request.
        try:
            with Session(repository.engine) as storage:
                cached = read_scraper_cache(storage, [source.source_playlist_id for source in sources
                                                      if source.source_playlist_id])
        except Exception as error:
            propagate_control(error)
    scraper = _PureScraper(cached)
    resolver = SourceResolver(pathways=[DirectAPIPathway(), SearchPathway(), scraper])
    return resolver, scraper


def _metadata(runtime, values, provenance):
    try:
        raw = runtime.api.get_tracks(values[:MAX_METADATA_LOOKUPS]) if values else []
    except Exception as error:
        propagate_control(error)
        raw = []
    metadata = {}
    for track in raw:
        if not track.get('uri'):
            continue
        album = track.get('album') or {}
        images = album.get('images') or []
        metadata[track['uri']] = {
            'uri': track['uri'], 'name': track.get('name', 'Unknown'),
            'artists': [artist.get('name', '') for artist in track.get('artists', [])],
            'album_name': album.get('name', ''), 'album_image_url': images[0].get('url', '') if images else '',
            'duration_ms': track.get('duration_ms'),
        }
    return [{**metadata.get(value, {'uri': value}), **provenance[value]} for value in values]


def _prepare(runtime, config):
    target, collection, archive = config.target_playlist_id, config.collection_playlist_id, config.archive_playlist_id
    before = {target: runtime.read_resource(target)}
    sources, resolved, selected, provenance, tracking, activities = _sources(config), [], [], {}, [], []
    exclusions = set(config.pending_track_uris) | set(before[target]['track_uris'])
    if sources:
        for resource in (collection, archive):
            if resource and resource not in before:
                before[resource] = runtime.read_resource(resource)
                exclusions.update(before[resource]['track_uris'])
    resolver, scraper = _resolver(runtime, sources)
    seen, failed = set(), []
    for source in sources:
        runtime.check_pending_control()
        try:
            resolution = resolver.resolve(source, api=runtime.api)
        except Exception as error:
            propagate_control(error)
            resolution = ResolveResult([], 'unavailable', False, error_message='Source resolution failed')
        runtime.check_pending_control()
        resolution = resolution or ResolveResult([], 'unavailable', False)
        candidates = [value for value in resolution.track_uris if isinstance(value, str) and value not in exclusions]
        chosen = candidates if len(candidates) <= source.raid_count else random.sample(candidates, source.raid_count)
        status = 'success' if resolution.success else 'partial' if resolution.partial else 'failed'
        fact = {'source_id': source.id, 'source_playlist_id': source.source_playlist_id,
                'source_name': source.source_name, 'pathway': resolution.pathway_name, 'status': status,
                'partial': bool(resolution.partial), 'resolved_count': len(resolution.track_uris),
                'selected_uris': list(chosen), 'resolved_at': datetime.now(timezone.utc).isoformat()}
        resolved.append(fact)
        if source.id is not None:
            tracking.append({**deepcopy(fact), 'source': asdict(source)})
        if not resolution.success:
            failure = {key: fact[key] for key in ('source_id', 'source_playlist_id', 'status')}
            failure['status'] = 'failed'
            failure['error_code'] = 'source_resolution_incomplete' if resolution.partial else 'source_resolution_failed'
            failed.append(failure)
            activities.append({
                'activity_type': ActivityType.RAID_SOURCE_RESOLVE_FAILED,
                'description': f"Raid source '{source.source_name or source.source_playlist_id or 'unknown source'}' "
                               f"failed to resolve via {resolution.pathway_name}",
                'metadata': {**{key: fact[key] for key in ('source_id', 'source_playlist_id', 'source_name',
                                                          'pathway', 'partial')},
                             'error_message': failure['error_code']},
            })
        for value in chosen:
            if value not in seen:
                seen.add(value)
                selected.append(value)
                provenance[value] = {'source_playlist_id': source.source_playlist_id, 'source_name': source.source_name}
    desired, actions = deepcopy(before), []
    if collection and selected:
        desired[collection]['track_uris'] += selected
        actions.append({'kind': 'add', 'resource': collection, 'track_uris': selected, 'position': None,
                        'before': deepcopy(before[collection])})
    result = {'success': not failed, 'tracks_added': len(selected), 'tracks_total': len(before[target]['track_uris']),
              'aggregate_status': 'partial' if failed else 'complete'}
    if failed:
        result.update(error_code='source_resolution_failed', retryable=False, failed_sources=failed)
    return {'version': 1, 'config': config.document(), 'before': before, 'desired': desired, 'actions': actions,
            'choices': {'selected_uris': selected, 'source_resolutions': resolved}, 'result': result,
            'local_actions': {'pending_stage': {'target_playlist_id': target,
                                                 'tracks': _metadata(runtime, selected, provenance)},
                              'source_tracking': tracking, 'activities': activities, 'scraper_cache': scraper.updates}}


def prepare_raid(runtime, config, *, prepare_key):
    _validate(runtime, config, prepare_key)
    prepared = runtime.prepare(prepare_key, lambda: _prepare(runtime, config))
    if prepared.get('version') != 1 or prepared.get('config') != config.document():
        raise JobExecutionError("The saved Collect configuration changed")
    return prepared


def execute_raid(runtime, config, *, prepare_key):
    prepared = prepare_raid(runtime, config, prepare_key=prepare_key)
    return apply(runtime, prepared, 'raid')
