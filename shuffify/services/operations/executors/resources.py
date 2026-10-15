"""Bind frozen complete resource reads to the journal, including read-only dependencies."""

from copy import deepcopy

from shuffify.services.operations.journal import NeedsAttention
from shuffify.services.operations.planner import content


def observe_resources(runtime, before):
    """Persist/fresh-check all inputs before the first mutation; recovery keeps checking them."""
    for resource, frozen in sorted(before.items()):
        runtime.check_pending_control()
        runtime.journal.observe_resource(resource, expected_baseline=frozen)


def expected_baseline(runtime, resource, frozen):
    """Only our acknowledged writes can advance a frozen baseline's snapshot."""
    expected = deepcopy(frozen)
    for step in runtime.get_journal().get('plans', [])[:runtime.journal.cursor]:
        if step['resource'] != resource:
            continue
        for item in step['batches']:
            if item['state'] != 'acknowledged':
                raise NeedsAttention('unfinished_plan')
            if item.get('snapshot_id'):
                expected['snapshot_id'] = item['snapshot_id']
    return expected


def verify_resources(runtime, desired, label):
    after = {}
    for resource, frozen in desired.items():
        runtime.check_pending_control()
        expected = expected_baseline(runtime, resource, frozen)
        observed = runtime.read_resource(resource)
        if content(observed) != content(frozen) or observed.get('snapshot_id') != expected.get('snapshot_id'):
            raise NeedsAttention(label + '_verification_failed')
        after[resource] = observed
    protect_verified_resources(runtime)
    return after


def protect_verified_resources(runtime):
    """Retain a composite's complete fresh proof before local control or output I/O."""
    current = runtime.journal.doc.get('boundaries', {}).get('current')
    if current is not None and runtime.verified_step_failure is None:
        runtime.protect_verified(current['name'], {})
