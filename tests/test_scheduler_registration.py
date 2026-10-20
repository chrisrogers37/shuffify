"""API registration is a batched observation of worker memory, never a DB guess."""

from copy import deepcopy
from datetime import timedelta

import pytest

import shuffify.scheduler as scheduler_module
from shuffify.services.schedule_utils import schedule_revision, serialize_timing
from tests.services.operations.test_scheduled_admission import NOW
from tests.services.operations.test_scheduled_admission import schedule as schedule


def snapshot(row):
    return {'version': 1, 'leader': True, 'scheduler_running': True, 'generation': 'claim',
            'effective_timezone': 'UTC', 'registrations': {str(row.id): {
                'installed_revision': schedule_revision(row), 'effective_timezone': 'UTC',
                'next_run_at': (NOW + timedelta(days=1)).isoformat()}}}


def test_matching_registration_is_memory_observed_and_never_a_local_job(schedule, monkeypatch):
    value = snapshot(schedule)
    calls = []

    class Client:
        def registrations(self, ids):
            calls.append(ids)
            return value

    monkeypatch.setattr(scheduler_module, 'WorkerControlClient', Client)
    assert scheduler_module.get_schedule_registration(schedule) == {
        'status': 'registered', 'next_run_at': (NOW + timedelta(days=1)).isoformat(), 'effective_timezone': 'UTC'}
    schedule.algorithm_params = {'keep_first': 2}
    assert scheduler_module.get_schedule_registration(schedule)['status'] == 'needs_attention'
    assert calls == [[schedule.id], [schedule.id]]


@pytest.mark.parametrize('bad', ['missing', 'paused', 'stale', 'naive', 'malformed', 'zone'])
def test_enabled_registration_requires_actual_running_matching_job(schedule, monkeypatch, bad):
    value = snapshot(schedule)
    job = value['registrations'][str(schedule.id)]
    if bad == 'missing':
        value['registrations'][str(schedule.id)] = None
    elif bad == 'paused':
        value['scheduler_running'] = False
    elif bad == 'stale':
        job['installed_revision'] = 'other'
    elif bad == 'naive':
        job['next_run_at'] = '2026-10-15T00:00:00'
    elif bad == 'malformed':
        job['next_run_at'] = 'bad'
    else:
        job['effective_timezone'] = 'Europe/London'
    monkeypatch.setattr(scheduler_module.WorkerControlClient, 'registrations', lambda self, ids: value)
    result = scheduler_module.get_schedule_registration(schedule)
    assert result['status'] == ('unavailable' if bad == 'paused' else 'needs_attention')
    assert result['next_run_at'] is None


def test_paused_and_unavailable_do_not_fabricate_registered(schedule, monkeypatch):
    schedule.is_enabled = False
    value = snapshot(schedule)
    monkeypatch.setattr(scheduler_module.WorkerControlClient, 'registrations', lambda self, ids: value)
    assert scheduler_module.get_schedule_registration(schedule)['status'] == 'needs_attention'
    value['registrations'][str(schedule.id)] = None
    assert scheduler_module.get_schedule_registration(schedule)['status'] == 'paused'
    value['leader'] = False
    assert scheduler_module.get_schedule_registration(schedule)['status'] == 'unavailable'


def test_payload_list_reads_worker_once_and_passes_effective_timezone(schedule, monkeypatch):
    from shuffify.services.routine_activation_service import schedule_payloads

    schedule.timezone = None
    value = snapshot(schedule)
    value['effective_timezone'] = value['registrations'][str(schedule.id)]['effective_timezone'] = 'Europe/London'
    calls = []

    def registrations(self, ids):
        calls.append(ids)
        return deepcopy(value)

    monkeypatch.setattr(scheduler_module.WorkerControlClient, 'registrations', registrations)
    rows = schedule_payloads([schedule])
    assert len(calls) == 1 and rows[0]['state'] == 'Active'
    assert rows[0]['timing']['effective_timezone'] == 'Europe/London'
    assert serialize_timing(schedule)['effective_timezone'] is None


def test_empty_batch_performs_no_network(monkeypatch):
    monkeypatch.setattr(scheduler_module.WorkerControlClient, 'registrations', lambda *args: pytest.fail('empty read'))
    assert scheduler_module.get_schedule_registrations([]) == {}
