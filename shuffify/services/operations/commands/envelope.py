"""Verify private command identity before constructing credentials or history."""


def validate_command(runtime, schema):
    payload = runtime.operation.payload
    if not isinstance(payload, dict) or type(payload.get('version')) is not int or payload['version'] != 1:
        raise ValueError('Unsupported command envelope')
    command = schema.model_validate(payload.get('command'))
    expected = [command.playlist_id]
    if runtime.operation.targets != expected or tuple(runtime.lease.targets) != tuple(expected):
        raise ValueError('Command target differs from its execution lease')
    snapshot = getattr(command, 'snapshot', None)
    if snapshot is not None and snapshot.user_id != runtime.user.id:
        raise ValueError('Snapshot belongs to another user')
    return command
