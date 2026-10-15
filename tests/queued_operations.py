"""Explicit route-to-worker driver using real Spotify API journaling and a mutable provider."""

import json
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from shuffify.services.operations.commands.registry import COMMAND_HANDLERS
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.spotify.exceptions import (
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyTokenExpiredError,
    SpotifyWriteRejectedError,
)
from shuffify.worker import OperationWorker


@dataclass
class CompletedOperation:
    """The actual polling response and accepted receipt, with convenient rich-result access."""

    status_code: int
    operation: dict
    accepted: object
    polled: object = None

    def get_json(self):
        return self.operation["result"]

    def get_data(self, as_text=False):
        return self.polled.get_data(as_text=as_text)

    @property
    def headers(self):
        return self.polled.headers


def _response(status, body, *, retry_after=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def worker_api(provider, token):
    """Keep real decorated write/batch/HTTP paths; only the provider transport is local."""
    api = SpotifyAPI(TokenInfo.from_dict(token), auto_refresh=False)

    def metadata(playlist_id, **kwargs):
        raw = provider.get_playlist(playlist_id, **kwargs)
        if hasattr(provider, "revisions"):
            revision = provider.revisions[playlist_id]
        else:
            revision = len(provider.writes)
        return {"description": None, "public": False, "snapshot_id": str(revision), **raw}

    def send(method, url, *, json=None, **kwargs):
        path = urlsplit(url).path.removeprefix("/v1")
        parts = path.strip("/").split("/")
        assert parts[0] == "playlists" and len(parts) in {2, 3}
        playlist_id = parts[1]
        try:
            if len(parts) == 2:
                assert method == "PUT"
                provider.update_playlist_details(playlist_id, **json)
            elif method == "PUT":
                assert len(json["uris"]) <= 100
                if hasattr(provider, "_write"):
                    provider._write("update", playlist_id, json["uris"])
                else:
                    provider.update_playlist_tracks(playlist_id, json["uris"])
            elif method == "POST":
                assert len(json["uris"]) <= 100
                if hasattr(provider, "_write"):
                    provider._write("add", playlist_id, json["uris"], json.get("position"))
                else:
                    provider.playlist_add_items(playlist_id, json["uris"], json.get("position"))
            else:
                assert method == "DELETE"
                assert len(json["items"]) <= 100
                tracks = [track["uri"] for track in json["items"]]
                if hasattr(provider, "_write"):
                    provider._write("remove", playlist_id, tracks)
                else:
                    provider.playlist_remove_items(playlist_id, tracks)
        except Exception as error:
            if isinstance(error, SpotifyPartialBatchError) and error.completed_batches:
                # A failure after acknowledged provider batches leaves this
                # transport response uncertain.
                raise requests.Timeout("Example lost batch response") from error
            cause = error.cause if isinstance(error, SpotifyPartialBatchError) else error
            statuses = ((SpotifyForbiddenError, 403), (SpotifyNotFoundError, 404),
                        (SpotifyTokenExpiredError, 401), (SpotifyQuotaExceededError, 429),
                        (SpotifyRateLimitError, 429), (SpotifyWriteRejectedError, 400))
            status = next((code for kind, code in statuses if isinstance(cause, kind)), None)
            if status is None:
                raise
            body = {"error": {"status": status, "message": "Example provider rejection"}}
            quota = isinstance(cause, SpotifyQuotaExceededError)
            if quota:
                body["error"]["reason"] = "QUOTA_EXCEEDED"
            retry_after = getattr(cause, "retry_after", None) if status == 429 else None
            if status == 429 and not quota and retry_after is None:
                retry_after = 1
            return _response(status, body, retry_after=retry_after)
        revision = (provider.revisions[playlist_id] if hasattr(provider, "revisions")
                    else repr(provider.playlists[playlist_id]) if hasattr(provider, "playlists")
                    else len(provider.writes))
        return _response(200, {"snapshot_id": str(revision)})

    api.get_playlist = metadata
    for name in ("get_playlist_tracks", "get_tracks", "search_tracks"):
        if hasattr(provider, name):
            setattr(api, name, getattr(provider, name))
    if hasattr(provider, "get_playlist_items_raw"):
        api.get_playlist_items_raw = provider.get_playlist_items_raw
    api._http._session.request = send
    return api


def post_and_run(client, provider, path, **kwargs):
    """Assert admission makes no provider calls, run one explicit worker, then poll its result."""
    writes = getattr(provider, "writes", None)
    if not isinstance(writes, list):
        return client.post(path, **kwargs)
    before = deepcopy(writes)
    reads = {name: deepcopy(getattr(provider, name)) for name in ("reads", "metadata_reads", "track_reads")
             if hasattr(provider, name)}
    response = client.post(path, **kwargs)
    if response.status_code != 202:
        return response
    assert provider.writes == before
    for name, before_reads in reads.items():
        assert getattr(provider, name) == before_reads
    return run_operation(client, provider, response.get_json()["operation"]["id"], response)


def run_operation(client, provider, operation_id, accepted_response):
    """Drive the same durable operation again after its known retry deadline."""
    with client.session_transaction() as session:
        token = dict(session["spotify_token"])
    runner = OperationWorker(client.application, handlers=COMMAND_HANDLERS,
                             api_factory=lambda user: worker_api(provider, token), heartbeat_interval=0)
    runner.run_once(operation_id)
    polled = client.get(accepted_response.headers["Location"])
    assert polled.status_code == 200
    return CompletedOperation(polled.status_code, polled.get_json()["operation"], accepted_response, polled)


def scoped_history(client, playlist_id):
    from shuffify.models.db import User
    from shuffify.services.operations.history import HistoryStore

    with client.session_transaction() as session:
        scope = session.get("history_scope")
        legacy = deepcopy(session.get("playlist_states", {}).get(playlist_id))
        spotify_id = session["user_data"]["id"]
    user = User.query.filter_by(spotify_id=spotify_id).one()
    if scope is None:
        return legacy
    return HistoryStore().load(user.id, scope, playlist_id, legacy=legacy)[0]
