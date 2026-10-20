"""An actual terminated process recovers its accepted creation without a second POST."""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, PlaylistPair, User, db
from shuffify.services.operations.commands.asset_capture import capture_asset
from shuffify.services.operations.repository import OperationRepository
from tests.services.operations.test_creation_executor import CreationWorld

ROOT = Path(__file__).resolve().parents[3]
CHILD = r"""
import json, os, sys, time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from config import TestConfig
TestConfig.SQLALCHEMY_DATABASE_URI = sys.argv[1]
from shuffify import create_app
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.operations.commands.assets import HANDLERS
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.worker import OperationWorker
from tests.services.operations.test_creation_executor import CreationWorld
app = create_app('testing')
statefile, mode, operation_id = Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
world = CreationWorld(ignored_private=True)
saved = json.loads(statefile.read_text())
world.playlists, world.writes, world.revision = saved['playlists'], saved['writes'], saved['revision']
def persist():
    temporary = statefile.with_suffix('.new')
    temporary.write_text(json.dumps({'playlists': world.playlists, 'writes': world.writes, 'revision': world.revision}))
    temporary.replace(statefile)
def factory(user):
    api = SpotifyAPI(TokenInfo.from_dict({'access_token': 'fake', 'token_type': 'Bearer',
                                         'expires_at': time.time()+3600, 'refresh_token': 'fake'}), auto_refresh=False)
    def metadata(resource, **kwargs):
        observed = world.read(resource)
        return {'id': resource, 'owner': {'id': 'owner'}, 'collaborative': False,
                'snapshot_id': observed['snapshot_id'], 'description': None, **observed['metadata']}
    def send(method, url, **kwargs):
        value = world.dispatch(method, urlsplit(url).path.removeprefix('/v1'), kwargs['json'])
        persist()
        if mode == 'unknown_create' and method == 'POST': os._exit(23)
        reply = requests.Response(); reply.status_code = 200
        reply._content = json.dumps(value).encode() if value else b''
        return reply
    api.get_playlist = metadata
    api._http._session.request = send
    return api
with app.app_context():
    repository = OperationRepository(lease_seconds=3)
    original_checkpoint, original_local = repository.checkpoint, repository.local_step
    def checkpoint(lease, document, **kwargs):
        result = original_checkpoint(lease, document, **kwargs)
        if mode == 'create_ack' and document.get('plans') and document['plans'][0].get('result', {}).get('id'):
            os._exit(23)
        return result
    def local(lease, name, callback, **kwargs):
        result = original_local(lease, name, callback, **kwargs)
        if mode == 'accepted' and name == 'accept.asset': os._exit(23)
        return result
    repository.checkpoint, repository.local_step = checkpoint, local
    worker = OperationWorker(app, repository=repository, handlers=HANDLERS, api_factory=factory, heartbeat_interval=0)
    assert worker.run_once(operation_id)
"""


@pytest.mark.parametrize("cut", ["create_ack", "accepted", "unknown_create"])
def test_process_exit_after_known_create_or_atomic_accept_never_recreates_or_duplicates(tmp_path, cut):
    engine = create_engine(f"sqlite:///{tmp_path / 'operations.sqlite'}")
    db.metadata.create_all(engine)
    repository = OperationRepository(engine, lease_seconds=3)
    with Session(engine) as storage, storage.begin():
        user = User(spotify_id="owner")
        storage.add(user)
        storage.flush()
        user_id = user.id
        command = capture_asset(storage, user_id, "target", kind="pair_create")
    row = repository.enqueue(
        kind=command.kind,
        user_id=user_id,
        targets=command.reserved_targets,
        payload={"version": 1, "command": command.model_dump(mode="json")},
        request_key="one-create",
    )
    world = CreationWorld(ignored_private=True)
    baseline = world.playlists["target"]
    statefile = tmp_path / "provider.json"
    statefile.write_text(json.dumps({"playlists": world.playlists, "writes": [], "revision": 0}))
    arguments = [sys.executable, "-c", CHILD, str(engine.url), str(statefile), cut, str(row.id)]
    interrupted = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert interrupted.returncode == 23, interrupted.stderr
    assert repository.claim(operation_id=row.id) is None
    limit = repository.get(row.id).lease_expires_at
    while datetime.now(timezone.utc) <= limit:
        time.sleep(0.02)
    arguments[-2] = "recover"
    recovered = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert recovered.returncode == 0, recovered.stderr
    completed = repository.get(row.id)
    assert completed.state == ("needs_attention" if cut == "unknown_create" else "succeeded")
    current = json.loads(statefile.read_text())
    assert current["playlists"]["target"] == baseline
    assert [method for method, _, _ in current["writes"]] == (["POST"] if cut == "unknown_create" else ["POST", "PUT"])
    if cut == "unknown_create":
        assert completed.journal["result"]["creation_unknown"] is True
        assert "playlist" not in completed.journal["result"]
    else:
        assert completed.journal["result"]["playlist"]["id"] == "created-1"
        assert completed.journal["result"]["privacy_confirmed"] is True
    with Session(engine) as storage:
        assert len(storage.scalars(select(PlaylistPair)).all()) == int(cut != "unknown_create")
        assert len(storage.scalars(select(ActivityLog)).all()) == int(cut != "unknown_create")
    replay = repository.enqueue(
        kind=command.kind,
        user_id=user_id,
        targets=command.reserved_targets,
        payload={"version": 1, "command": command.model_dump(mode="json")},
        request_key="one-create",
    )
    assert replay.id == row.id
    engine.dispose()
