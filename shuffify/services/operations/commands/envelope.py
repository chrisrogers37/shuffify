"""Verify private command identity before constructing credentials or history."""


def validate_command(runtime, schema):
    payload = runtime.operation.payload
    if not isinstance(payload, dict) or type(payload.get('version')) is not int or payload['version'] != 1:
        raise ValueError('Unsupported command envelope')
    command = schema.model_validate(payload.get('command'))
    expected = getattr(command, 'reserved_targets', [command.playlist_id])
    if runtime.operation.targets != expected or tuple(runtime.lease.targets) != tuple(expected):
        raise ValueError('Command target differs from its execution lease')
    kind = getattr(command, 'kind', None)
    if kind is not None and runtime.operation.kind != kind:
        raise ValueError('Command kind differs from its execution row')
    definition = getattr(command, 'definition', None)
    if definition is not None:
        if runtime.operation.schedule_id != definition.schedule_id:
            raise ValueError('Command schedule differs from its execution row')
        scheduled = command.triggered_by == 'scheduler'
        if scheduled != (runtime.operation.scheduled_for is not None):
            raise ValueError('Command origin differs from its execution row')
    snapshot = getattr(command, 'snapshot', None)
    if snapshot is not None and snapshot.user_id != runtime.user.id:
        raise ValueError('Snapshot belongs to another user')
    return command
