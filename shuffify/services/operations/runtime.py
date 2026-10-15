"""Fenced operation state and authenticated resources supplied to command handlers."""

import json
from copy import deepcopy
from dataclasses import dataclass

from shuffify.models.db import User, db


@dataclass(frozen=True)
class OperationResult:
    """A command's explicit terminal state and existing public service result."""

    state: str
    result: dict

    def __post_init__(self):
        if self.state not in {"succeeded", "failed", "rolled_back", "needs_attention"}:
            raise ValueError("A command result requires a terminal operation state")


class OperationRuntime:
    """Keep randomized choices and step results durable before later effects."""

    def __init__(self, repository, lease, *, api_factory=None, lease_lost=None):
        self.repository = repository
        self.lease = lease
        self.operation = repository.get(lease.operation_id)
        self.user = db.session.get(User, self.operation.user_id)
        if self.user is None:
            raise ValueError("Operation user is unavailable")
        self._api_factory = api_factory
        self._api = None
        self._journal = None
        self._lease_lost = lease_lost
        self._history = None
        self._history_loaded = False

    @property
    def history(self):
        """Load this browser scope's authoritative history without authenticating Spotify."""
        self.ensure_lease()
        if not self._history_loaded:
            from shuffify.services.operations.history import HistoryError, HistoryStore

            binding = (self.operation.payload or {}).get("history")
            if binding is not None:
                if not isinstance(binding, dict) or not isinstance(binding.get("revisions"), dict):
                    raise HistoryError("The command's saved history binding is unavailable.")
                self._history = HistoryStore(self.repository.engine).session_for(
                    self.user.id, binding.get("scope"), self.operation.targets or [],
                    legacy=binding.get("imports"), expected_revisions=binding["revisions"],
                )
                working = self.get_journal().get("runtime", {}).get("history_working")
                if working is not None:
                    self._history.restore(working)
            self._history_loaded = True
        return self._history

    def assert_history_revision(self, playlist_id):
        """Revalidate the captured Undo revision while the command holds all target locks."""
        from shuffify.services.operations.history import HistoryError, HistoryStore

        mapping = self.history
        if mapping is None or playlist_id not in mapping.revisions:
            raise HistoryError("The command's saved history revision is unavailable.")
        HistoryStore(self.repository.engine).assert_revision(
            self.user.id, mapping.scope, playlist_id, mapping.revisions[playlist_id],
        )

    def history_working(self, document):
        """Checkpoint the working mapping with its command step's output, preserving frozen before-state."""
        document = deepcopy(document)
        if self._history is not None:
            document.setdefault("runtime", {})["history_working"] = deepcopy(dict(self._history))
        return document

    def stage_history(self, state, *, confirmed=True):
        """Stage verified history for the same fenced transaction that completes this operation."""
        from shuffify.services.operations.history import HistoryError, HistoryStore

        document = self.get_journal()
        if document.get("runtime", {}).get("history_unconfirmed"):
            raise HistoryError("The verified playlist's saved history could not be confirmed.")
        if state not in {"succeeded", "rolled_back"} or self.history is None:
            return None
        document = HistoryStore(self.repository.engine).stage(
            document, self.history, state=state, confirmed=confirmed,
        )
        return self.checkpoint(document)

    def mark_history_unconfirmed(self):
        """Keep a history failure durable without reversing verified Spotify changes."""
        document = self.get_journal()
        document.setdefault("runtime", {})["history_unconfirmed"] = True
        return self.checkpoint(document)

    def ensure_lease(self):
        """Stop before another effect when an independent heartbeat lost ownership."""
        if self._lease_lost is not None and self._lease_lost.is_set():
            from shuffify.services.operations.repository import LeaseLost

            raise LeaseLost("Operation lease was lost")

    @property
    def api(self):
        """Refresh credentials only in the worker, on the first Spotify operation."""
        self.ensure_lease()
        if self._api is None:
            if self._api_factory is None:
                from shuffify.services.executors.base_executor import JobExecutorService

                self._api = JobExecutorService._get_spotify_api(self.user)
            else:
                self._api = self._api_factory(self.user)
        return self._api

    def get_journal(self):
        self.ensure_lease()
        return deepcopy(self.repository.get(self.lease.operation_id).journal or {})

    def checkpoint(self, document):
        self.ensure_lease()
        state = "compensating" if document.get("phase") == "compensating" else None
        return self.repository.checkpoint(self.lease, document, state=state).journal

    def prepare(self, name, calculate):
        """Freeze JSON command choices; a resumed command reuses the stored value."""
        document = self.get_journal()
        prepared = document.setdefault("runtime", {}).setdefault("prepared", {})
        if name not in prepared:
            value = calculate()
            # Reject accidental models, credentials objects, NaN, or mutable aliases.
            prepared[name] = json.loads(json.dumps(value, allow_nan=False))
            self.checkpoint(document)
        return deepcopy(prepared[name])

    def check_pending_control(self):
        """Propagate a deferred or aborted service action before caching an outcome."""
        self.ensure_lease()
        self.journal.check_pending_control()

    def local_step(self, name, callback):
        """Commit local database effects and their receipt in one fenced transaction."""
        self.check_pending_control()
        return self.repository.local_step(self.lease, name, callback)

    def step(self, name, execute):
        """Persist a command step's output for an idempotent continuation."""
        self.check_pending_control()
        document = self.get_journal()
        runtime = document.setdefault("runtime", {})
        outputs = runtime.setdefault("steps", {})
        if name in outputs:
            plan_range = runtime.get("step_plan_ranges", {}).get(name)
            if plan_range is not None:
                self.journal.skip_to(plan_range["end"])
            elif document.get("plans"):
                from shuffify.services.operations.journal import NeedsAttention

                raise NeedsAttention("step_plan_range_missing")
            return deepcopy(outputs[name])
        start = self.journal.cursor
        value = execute()
        self.check_pending_control()
        # The step can checkpoint its own mutations, so re-read before adding its output.
        document = self.get_journal()
        runtime = document.setdefault("runtime", {})
        runtime.setdefault("steps", {})[name] = json.loads(json.dumps(value, allow_nan=False))
        runtime.setdefault("step_plan_ranges", {})[name] = {"start": start, "end": self.journal.cursor}
        self.checkpoint(self.history_working(document))
        return deepcopy(value)

    def read_metadata_resource(self, playlist_id):
        """Authorize stable uncached metadata without reading any playlist items."""
        from shuffify.services.playlist_service import PlaylistService
        from shuffify.services.workshop_commit_service import IncompletePlaylistRead

        reader = PlaylistService(self.api)
        first = reader.validate_user_can_edit(playlist_id, self.user.spotify_id, skip_cache=True)
        second = reader.validate_user_can_edit(playlist_id, self.user.spotify_id, skip_cache=True)
        fields = ("name", "public", "description")
        if any(first.get(name) != second.get(name) for name in fields):
            raise IncompletePlaylistRead("Playlist metadata changed during reconciliation")
        return {"snapshot_id": second.get("snapshot_id"), "metadata": {name: second.get(name) for name in fields}}

    def read_resource(self, playlist_id):
        """Authorize two complete uncached reads with a stable snapshot and order."""
        from shuffify.services.workshop_commit_service import IncompletePlaylistRead, WorkshopCommitService

        reader = WorkshopCommitService(self.api)
        first = reader.read_complete(playlist_id, self.user.spotify_id)
        second = reader.read_complete(playlist_id, self.user.spotify_id)
        if first.track_uris != second.track_uris or first.snapshot_id != second.snapshot_id:
            raise IncompletePlaylistRead("Playlist changed during reconciliation")
        metadata = self.api.get_playlist(playlist_id, skip_cache=True)
        if metadata.get("snapshot_id") != second.snapshot_id:
            raise IncompletePlaylistRead("Playlist changed during reconciliation")
        return {
            "track_uris": list(second.track_uris), "snapshot_id": second.snapshot_id,
            "metadata": {name: metadata.get(name) for name in ("name", "public", "description")},
        }

    def dispatch(self, method, path, body):
        """Send a journaled batch through the authenticated transport."""
        self.ensure_lease()
        return self.api.dispatch_journaled_batch(method, path, body)

    @property
    def journal(self):
        if self._journal is None:
            from shuffify.services.operations.journal import DurableJournal

            self._journal = DurableJournal(
                self.get_journal, self.checkpoint, self.read_resource, self.dispatch,
                read_metadata=self.read_metadata_resource,
            )
        return self._journal

    def close(self):
        if self._api is not None:
            self._api.close()
