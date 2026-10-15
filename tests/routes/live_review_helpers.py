"""Helpers shared by the live-action review route tests."""

from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

from shuffify.services.executors import JobExecutorService
from tests.paths import api_path


@contextmanager
def serving(api):
    """Answer every session and executor Spotify call from ``api``; yields the mocked auto-snapshot."""
    with patch("shuffify.routes.require_auth", return_value=api), patch.object(
        JobExecutorService, "_get_spotify_api", return_value=api,
    ), patch(
        "shuffify.services.playlist_snapshot_service.PlaylistSnapshotService.auto_snapshot_if_enabled",
    ) as auto_snapshot:
        yield auto_snapshot


def post_context(client, target, body):
    """POST one review request for ``target``; a review reply is never cached."""
    response = client.post(api_path(f"/playlist/{target}/live-context"), json=body)
    assert response.headers["Cache-Control"] == "no-store"
    return response


def seed_history(client, target, states, index=None):
    """Store ``target``'s saved versions in the session, at ``index`` (default: the newest)."""
    with client.session_transaction() as session:
        session["playlist_states"] = {
            target: {"states": deepcopy(states), "current_index": len(states) - 1 if index is None else index}
        }


def history(client, target):
    """A copy of ``target``'s saved versions in the session, or None."""
    with client.session_transaction() as session:
        return deepcopy(session.get("playlist_states", {}).get(target))


def binding_bundle(context):
    """The reviewed bindings a run posts back: each one ``context`` carries."""
    binding = context["review_binding"]
    return {f"reviewed_{name}": binding[name] for name in ("scope", "history", "before_order") if binding[name]}
