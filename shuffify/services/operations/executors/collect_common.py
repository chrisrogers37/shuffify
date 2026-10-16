"""Shared pure validation, frozen baselines, and observed application for Collect/Drip."""

from copy import deepcopy

from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.spotify.observation import current_observer

from .resources import expected_baseline, observe_resources, verify_resources


def validate(runtime, config, prepare_key, resources):
    if type(config.user_id) is not int or config.user_id != runtime.user.id:
        raise JobExecutionError("The captured configuration belongs to another user")
    resources = [value for value in resources if value is not None]
    if any(not isinstance(value, str) or not value for value in resources) or len(set(resources)) != len(resources):
        raise JobExecutionError("Target, collection, and archive must be distinct playlists")
    if not set(resources).issubset(runtime.lease.targets):
        raise JobExecutionError("The captured configuration includes an unreserved playlist")
    if not isinstance(prepare_key, str) or not prepare_key or len(prepare_key) > 120:
        raise JobExecutionError("The executor requires a preparation key of 1 to 120 characters")
    runtime.check_pending_control()


def apply(runtime, prepared, label):
    if current_observer() is not runtime.journal:
        raise JobExecutionError("The executor requires the operation's outer journal observer")
    runtime.check_pending_control()
    observe_resources(runtime, prepared['before'])
    for action in prepared['actions']:
        runtime.check_pending_control()
        resource, values = action['resource'], action['track_uris']
        baseline = expected_baseline(runtime, resource, action['before'])
        options = {'position': action['position']} if action['kind'] == 'add' else {}
        getattr(runtime.journal, 'ensure_' + action['kind'])(resource, values, expected_baseline=baseline, **options)
        if not getattr(runtime.api, 'playlist_' + action['kind'] + '_items')(resource, values, **options):
            raise JobExecutionError(f"{label} playlist_{action['kind']}_items returned falsy")
    runtime.check_pending_control()
    after = verify_resources(runtime, prepared['desired'], label)
    return {'before': deepcopy(prepared['before']), 'after': after, 'result': deepcopy(prepared['result']),
            'choices': deepcopy(prepared['choices']), 'local_actions': deepcopy(prepared['local_actions'])}
