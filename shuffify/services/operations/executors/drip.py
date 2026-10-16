"""Prepare Drip once, then journal target prepend before exact collection cleanup."""

import random
from copy import deepcopy
from dataclasses import asdict, dataclass

from shuffify.services.executors.base_executor import JobExecutionError

from .collect_common import apply, validate


@dataclass(frozen=True)
class PendingRow:
    id: int
    track_uri: str
    status: str = 'pending'


@dataclass(frozen=True)
class DripConfig:
    user_id: int
    target_playlist_id: str
    collection_playlist_id: str
    drip_count: int = 3
    drip_enabled: bool = False
    force: bool = False
    pending_rows: tuple[PendingRow, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'pending_rows', tuple(self.pending_rows))

    def document(self):
        value = asdict(self)
        value['pending_rows'] = [asdict(row) for row in self.pending_rows]
        return value


def _validate(runtime, config, prepare_key):
    if not isinstance(config, DripConfig):
        raise JobExecutionError("Drip requires captured configuration")
    validate(runtime, config, prepare_key, (config.target_playlist_id, config.collection_playlist_id))
    if not config.collection_playlist_id:
        raise JobExecutionError("Drip requires a linked collection")
    if (type(config.drip_count) is not int or config.drip_count < 1
            or type(config.drip_enabled) is not bool or type(config.force) is not bool
            or any(not isinstance(row, PendingRow) or type(row.id) is not int or row.id < 1
                   or not isinstance(row.track_uri, str) or not row.track_uri or row.status != 'pending'
                   for row in config.pending_rows)
            or len({row.id for row in config.pending_rows}) != len(config.pending_rows)):
        raise JobExecutionError("Drip requires normalized count, gate, and exact pending rows")


def _prepare(runtime, config):
    target, collection = config.target_playlist_id, config.collection_playlist_id
    before = {target: runtime.read_resource(target)}
    desired = deepcopy(before)
    actions, sampled, selected = [], [], []
    result = {'success': True, 'tracks_added': 0, 'tracks_total': len(before[target]['track_uris'])}
    local = {'pending_promotion': {'target_playlist_id': target, 'rows': []}, 'lock_reconcile': None}
    if not config.force and not config.drip_enabled:
        result['skipped_reason'] = 'drip_disabled'
    else:
        before[collection] = runtime.read_resource(collection)
        desired[collection] = deepcopy(before[collection])
        pool = before[collection]['track_uris']
        sampled = list(pool) if len(pool) <= config.drip_count else random.sample(pool, config.drip_count)
        existing = set(before[target]['track_uris'])
        selected = [value for value in sampled if value not in existing]
        if selected:
            desired[target]['track_uris'] = selected + before[target]['track_uris']
            chosen = set(selected)
            desired[collection]['track_uris'] = [value for value in pool if value not in chosen]
            actions = [
                {'kind': 'add', 'resource': target, 'track_uris': selected, 'position': 0,
                 'before': deepcopy(before[target])},
                {'kind': 'remove', 'resource': collection, 'track_uris': selected,
                 'before': deepcopy(before[collection])},
            ]
            local['pending_promotion']['rows'] = [asdict(row) for row in config.pending_rows
                                                   if row.track_uri in chosen]
            local['lock_reconcile'] = {'playlist_id': target, 'track_uris': desired[target]['track_uris']}
            result.update(tracks_added=len(selected), tracks_total=len(desired[target]['track_uris']))
    return {'version': 1, 'config': config.document(), 'before': before, 'desired': desired, 'actions': actions,
            'choices': {'sampled_uris': sampled, 'selected_uris': selected,
                        'gate': 'forced' if config.force else 'open' if config.drip_enabled else 'closed'},
            'result': result, 'local_actions': local}


def prepare_drip(runtime, config, *, prepare_key):
    _validate(runtime, config, prepare_key)
    prepared = runtime.prepare(prepare_key, lambda: _prepare(runtime, config))
    if prepared.get('version') != 1 or prepared.get('config') != config.document():
        raise JobExecutionError("The saved Drip configuration changed")
    return prepared


def execute_drip(runtime, config, *, prepare_key):
    prepared = prepare_drip(runtime, config, prepare_key=prepare_key)
    return apply(runtime, prepared, 'drip')
