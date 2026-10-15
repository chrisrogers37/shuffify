"""Verified direct track actions; session history is confirmed after Spotify."""

import logging
from collections import Counter
from copy import deepcopy
from dataclasses import asdict

from shuffify.enums import ActivityType, SnapshotType
from shuffify.models.playlist import Playlist
from shuffify.services.activity_log_service import ActivityLogService
from shuffify.services.live_operation_service import (
    DirectFailure,
    LiveOperation,
    book_not_required,
    direct_rejected_result,
    direct_response_payload,
)
from shuffify.services.live_review_bindings import (
    require_bindings,
    shuffle_scope,
    snapshot_bindings,
    undo_bindings,
)
from shuffify.services.playlist_lock import playlist_lock
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistService
from shuffify.services.playlist_snapshot_service import PlaylistSnapshotService
from shuffify.services.shuffle_service import InvalidAlgorithmError, ShuffleService
from shuffify.services.state_service import (
    AlreadyAtOriginalError,
    NoHistoryError,
    StaleHistoryError,
    StateError,
    StateService,
)
from shuffify.services.workshop_commit_service import _TRACK_URI, UnsupportedPlaylistItems
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
)

logger = logging.getLogger(__name__)


def track_mutation_type(desired, current):
    """track_order when ``desired`` keeps every occurrence of ``current``, else track_membership."""
    return "track_order" if Counter(desired) == Counter(current) else "track_membership"


class LivePlaylistService:
    def __init__(self, api):
        self.api = api

    def shuffle(self, playlist_id, user, session, *, algorithm, params, reviewed=None, runtime=None):
        def select(before):
            # A reviewed run compares inside the lock, before any algorithm or write.
            if reviewed is not None:
                require_bindings(reviewed, {"reviewed_scope": shuffle_scope(playlist_id, algorithm, params)})
            if not before.track_uris:
                raise DirectFailure(400, "empty_playlist", "The playlist contains no tracks.")
            # Each occurrence retains its own added_at, including duplicate URIs.
            desired = ShuffleService.execute(algorithm, deepcopy(list(before.tracks)), dict(params or {}), api=self.api)
            if Counter(desired) != Counter(before.track_uris):
                raise DirectFailure(502, "invalid_shuffle_result", "The shuffle could not be safely applied.")
            return desired, None

        return self._execute("shuffle", playlist_id, user, session, select, algorithm=algorithm, runtime=runtime)

    def undo(self, playlist_id, user, session, *, reviewed=None, runtime=None, selected=None):
        def select(before):
            selection = StateService.peek_undo(session, playlist_id)
            if reviewed is not None:
                # The server still selects the order; these only prove it is the reviewed one.
                require_bindings(reviewed, undo_bindings(playlist_id, selection, before.track_uris))
            if selected is not None:
                StateService.assert_undo_selection(session, playlist_id, selected)
            return list(selection.desired_uris), selection

        return self._execute("undo", playlist_id, user, session, select, runtime=runtime)

    def restore_snapshot(self, snapshot, user, session, *, reviewed=None, runtime=None):
        if snapshot.user_id != user.id:
            return direct_rejected_result(
                "snapshot_restore", error_code="snapshot_not_found", http_status=404, message="Snapshot not found."
            )
        playlist_id, desired = snapshot.playlist_id, list(snapshot.track_uris)
        if not desired:
            return direct_rejected_result(
                "snapshot_restore", error_code="empty_snapshot", http_status=400, message="Snapshot contains no tracks."
            )
        snapshot_data = deepcopy(snapshot.to_dict())
        snapshot_id = snapshot.id

        def select(before):
            # A reviewed restore compares inside the lock, before any auto-snapshot, write or history.
            if reviewed is not None:
                require_bindings(reviewed, snapshot_bindings(playlist_id, snapshot_id, desired, before.track_uris))
            return desired, None

        result = self._execute("snapshot_restore", playlist_id, user, session, select, runtime=runtime)
        result.update(playlist_id=playlist_id, snapshot=snapshot_data)
        return result

    def _execute(self, kind, playlist_id, user, session, select, *, algorithm=None, runtime=None):
        if runtime is not None:
            return self._execute_durable(kind, playlist_id, user, session, select, algorithm, runtime)
        operation = LiveOperation(kind, self.api, user)
        metadata, result = None, {}
        lock_entered = False
        try:
            with playlist_lock(playlist_id, timeout_s=2.0) as acquired:
                lock_entered = True
                if not acquired:
                    return direct_rejected_result(
                        kind,
                        error_code="playlist_busy",
                        http_status=409,
                        retryable=True,
                        message="Another action is using this playlist. Try again shortly.",
                    )
                try:
                    metadata = PlaylistService(self.api).validate_user_can_edit(
                        playlist_id, user.spotify_id, skip_cache=True
                    )
                    before = operation.read(playlist_id)
                    desired, selection = select(before)
                    desired = list(desired)
                    self.validate_order(desired)
                    mutation = track_mutation_type(desired, before.track_uris)
                    resource = operation.plan(playlist_id, desired, kind, mutation_type=mutation)
                    if selection is not None:
                        StateService.assert_undo_selection(session, playlist_id, selection)
                    changed = desired != list(before.track_uris)
                    if changed and kind != "undo":
                        PlaylistSnapshotService.auto_snapshot_if_enabled(
                            user_id=user.id,
                            playlist_id=playlist_id,
                            playlist_name=before.name,
                            track_uris=list(before.track_uris),
                            snapshot_type=SnapshotType.AUTO_PRE_SHUFFLE
                            if kind == "shuffle"
                            else SnapshotType.AUTO_PRE_COMMIT,
                            trigger_description=f"Before {algorithm or 'snapshot restore'}",
                        )
                    # plan's baseline is not a post-dispatch observation. The writer
                    # supplies current state only after a complete verification read.
                    resource.pop("current_track_uris", None)
                    resource.pop("loaded_at", None)
                    operation.write("update_playlist_tracks", playlist_id, desired, kind)
                    after = operation.observed[playlist_id]
                    resource.update(current_track_uris=list(after.track_uris), loaded_at=after.loaded_at)
                    result["playlist"] = self._playlist(playlist_id, metadata, after)
                    self._confirm_history(operation, playlist_id, session, before, after, selection, changed, result)
                    if changed and kind != "undo":
                        self._log_activity(kind, user, playlist_id, after, algorithm)
                    message = self._success_message(kind, changed, selection)
                    outcome = operation.outcome()
                    complete = outcome["aggregate_status"] == "complete"
                    return {
                        **result,
                        "status": "success" if complete else "failed",
                        "http_status": 200 if complete else 502,
                        "message": message
                        if complete
                        else "Playlist contents are verified; saved history needs attention.",
                        "category": "info" if not changed else "success",
                        "operation": outcome,
                    }
                except Exception as error:
                    self._propagate_control(error)
                    return self._failure(operation, playlist_id, metadata, error)
        except Exception as error:
            self._propagate_control(error)
            if not lock_entered:
                return direct_rejected_result(
                    kind,
                    error_code="coordination_failed",
                    http_status=502,
                    retryable=True,
                    message="The playlist could not be locked. This attempt applied no changes.",
                )
            # Lock release can fail after a verified write/history update. Preserve
            # those facts and classify only the run-level coordination as unknown.
            operation.book("coordination", "unknown", error_code="coordination_failed")
            operation.error_code = "coordination_failed"
            return {
                **result,
                "status": "failed",
                "http_status": 502,
                "message": "Playlist results are retained; coordination could not be confirmed.",
                "operation": operation.outcome(),
            }

    @staticmethod
    def _propagate_control(error):
        from shuffify.services.operations.control import propagate_control

        propagate_control(error)

    def _execute_durable(self, kind, playlist_id, user, session, select, algorithm, runtime):
        """Freeze original observations once; replay only the journaled target effect."""
        from shuffify.services.state_service import UndoSelection
        from shuffify.services.workshop_commit_service import IncompletePlaylistRead, ObservedPlaylist

        operation = LiveOperation(kind, self.api, user)
        metadata = None
        try:
            def prepare():
                before = operation.read(playlist_id)
                fresh = PlaylistService(self.api).validate_user_can_edit(playlist_id, user.spotify_id, skip_cache=True)
                if fresh.get("snapshot_id") != before.snapshot_id:
                    raise IncompletePlaylistRead("Playlist changed during preparation")
                desired, selection = select(before)
                desired = list(desired)
                self.validate_order(desired)
                return {"before": asdict(before), "metadata": fresh, "desired": desired,
                        "selection": selection.state_dict() if selection is not None else None,
                        "changed": desired != list(before.track_uris)}

            prepared = runtime.prepare("direct.action.v1", prepare)
            metadata, desired = prepared["metadata"], prepared["desired"]
            raw = prepared["before"]
            before = ObservedPlaylist(raw["name"], tuple(raw["track_uris"]), tuple(raw["tracks"]),
                                      raw["loaded_at"], raw["snapshot_id"])
            chosen = prepared["selection"]
            selection = (UndoSelection(tuple(tuple(uris) for uris in chosen["states"]), chosen["current_index"])
                         if chosen else None)
            operation.observed[playlist_id] = before
            resource = operation.plan(playlist_id, desired, kind,
                                      mutation_type=track_mutation_type(desired, before.track_uris))
            changed = prepared["changed"]
            if changed and kind != "undo":
                def snapshot(db_session):
                    from shuffify.services.operations.bookkeeping import OperationBookkeeping

                    bookkeeping = OperationBookkeeping(db_session, user.id, runtime.operation.id)
                    return bookkeeping.optional(lambda: bookkeeping.snapshot(
                        playlist_id, before.name, list(before.track_uris),
                        SnapshotType.AUTO_PRE_SHUFFLE if kind == "shuffle" else SnapshotType.AUTO_PRE_COMMIT,
                        f"Before {algorithm or 'snapshot restore'}", auto=True,
                    ))

                runtime.local_step("direct.snapshot.v1", snapshot)
            if changed:
                resource.pop("current_track_uris", None)
                resource.pop("loaded_at", None)
                resource.update(dispatched=True, status="unknown", affected_count=None)

            def apply():
                if changed:
                    expected = {"track_uris": list(before.track_uris), "snapshot_id": before.snapshot_id,
                                "metadata": {key: metadata.get(key) for key in ("name", "public", "description")}}
                    runtime.journal.ensure_replace(playlist_id, desired, expected_baseline=expected)
                    self.api.update_playlist_tracks(playlist_id, desired)
                after = operation.reader.read_complete(playlist_id, user.spotify_id)
                if list(after.track_uris) != desired:
                    from shuffify.services.executors.base_executor import PlaylistVerificationError

                    raise PlaylistVerificationError(playlist_id, desired, list(after.track_uris), None, kind)
                return asdict(after)

            after_raw = runtime.step("direct.target.v1", apply)
            after = ObservedPlaylist(after_raw["name"], tuple(after_raw["track_uris"]), tuple(after_raw["tracks"]),
                                     after_raw["loaded_at"], after_raw["snapshot_id"])
            operation.observed[playlist_id] = after
            count = (sum(a != b for a, b in zip(before.track_uris, after.track_uris))
                     + abs(len(before.track_uris) - len(after.track_uris))) if kind == "shuffle" else (
                         sum((Counter(after.track_uris) - Counter(before.track_uris)).values())
                         + sum((Counter(before.track_uris) - Counter(after.track_uris)).values()))
            resource.update(status="complete" if changed else "not_required", affected_count=count,
                            current_track_uris=list(after.track_uris), loaded_at=after.loaded_at,
                            error_code=None, retryable=False)
            result = {"playlist": self._playlist(playlist_id, metadata, after)}

            def confirm():
                self._confirm_history(operation, playlist_id, session, before, after, selection, changed, result,
                                      runtime=runtime)
                return {"playlist_state": result.get("playlist_state"), "bookkeeping": operation.bookkeeping,
                        "error_code": operation.error_code}

            history = runtime.step("direct.history.v1", confirm)
            operation.bookkeeping, operation.error_code = history["bookkeeping"], history["error_code"]
            if history["error_code"] is None:
                result["playlist_state"] = history["playlist_state"]
            else:
                result.pop("playlist_state", None)
            if history["playlist_state"] is not None and any(
                    item["kind"] == "session_history" and item["status"] == "complete"
                    for item in operation.bookkeeping):
                histories = dict(session.get("playlist_states", {}))
                histories[playlist_id] = deepcopy(history["playlist_state"])
                session["playlist_states"], session.modified = histories, True
            if changed and kind != "undo":
                def activity(db_session):
                    from shuffify.services.operations.bookkeeping import OperationBookkeeping

                    bookkeeping = OperationBookkeeping(db_session, user.id, runtime.operation.id)
                    return bookkeeping.optional(lambda: bookkeeping.activity(
                        ActivityType.SHUFFLE if kind == "shuffle" else ActivityType.SNAPSHOT_RESTORE,
                        f"{kind.replace('_', ' ').capitalize()} completed on '{after.name}'",
                        playlist_id=playlist_id, playlist_name=after.name,
                        metadata={"action": kind, "algorithm": algorithm, "track_count": len(after.track_uris)},
                    ))

                runtime.local_step("direct.activity.v1", activity)
            outcome = operation.outcome()
            complete = outcome["aggregate_status"] == "complete"
            return {**result, "status": "success" if complete else "failed", "success": complete,
                    "http_status": 200 if complete else 502,
                    "message": self._success_message(kind, changed, selection) if complete else
                    "Playlist contents are verified; saved history needs attention.",
                    "category": "success" if changed else "info", "operation": outcome}
        except Exception as error:
            from shuffify.services.operations.journal import NeedsAttention

            if isinstance(error, NeedsAttention) and operation.resources:
                error.result = direct_response_payload(self._failure(operation, playlist_id, metadata, error), kind)[0]
            self._propagate_control(error)
            batches = [item for step in runtime.journal.doc["plans"] for item in step["batches"]]
            if (self._write_rejection(error) is not None and batches
                    and not any(item["state"] == "acknowledged" for item in batches)
                    and any(item["state"] == "rejected" for item in batches)):
                operation.error_code, operation.retryable = "write_rejected", True
                for item in operation.resources:
                    item.update(status="failed", affected_count=0, retryable=True, error_code="write_rejected")
            return self._failure(operation, playlist_id, metadata, error)

    @staticmethod
    def _success_message(kind, changed, selection):
        if kind == "shuffle":
            return "Playlist shuffled successfully." if changed else "Shuffle did not change the playlist order."
        if changed or selection is not None:
            return "Playlist restored successfully."
        return "This order is already on Spotify."

    @staticmethod
    def _confirm_history(operation, playlist_id, session, before, after, selection, changed, result, runtime=None):
        initial = deepcopy(session.get("playlist_states", {}).get(playlist_id))
        if not changed and selection is None:
            book_not_required("session_history", playlist_id, operation)
            result["playlist_state"] = initial
            return
        try:
            state = (
                StateService.confirm_undo(session, playlist_id, selection)
                if selection is not None
                else StateService.record_verified_change(
                    session, playlist_id, before_uris=before.track_uris, after_uris=after.track_uris
                )
            )
            result["playlist_state"] = deepcopy(state.to_dict())
            operation.book("session_history", "complete", resource=playlist_id, affected_count=1)
        except Exception as error:
            LivePlaylistService._propagate_control(error)
            if runtime is not None:
                runtime.mark_history_unconfirmed()
            logger.warning("Direct action history confirmation failed", exc_info=True)
            unchanged = session.get("playlist_states", {}).get(playlist_id) == initial
            operation.book(
                "session_history",
                "failed" if unchanged else "unknown",
                resource=playlist_id,
                error_code="history_confirmation_failed",
            )
            operation.error_code = "history_confirmation_failed"

    @staticmethod
    def _playlist(playlist_id, metadata, observed):
        return Playlist(
            id=playlist_id,
            name=observed.name,
            owner_id=metadata["owner"]["id"],
            description=metadata.get("description"),
            public=metadata.get("public"),
            total_tracks=len(observed.track_uris),
            tracks=deepcopy(list(observed.tracks)),
        ).to_dict()

    @staticmethod
    def validate_order(desired):
        if any(not isinstance(uri, str) or not _TRACK_URI.fullmatch(uri) for uri in desired):
            raise DirectFailure(400, "invalid_track_order", "The saved track order cannot be safely restored.")

    @staticmethod
    def preflight_failure(error):
        if isinstance(error, DirectFailure):
            return error.status, error.code, error.message, error.retryable
        if isinstance(error, SpotifyNotFoundError) or (
            isinstance(error, PlaylistAccessError) and isinstance(error.__cause__, SpotifyNotFoundError)
        ):
            return 404, "playlist_not_found", "Playlist not found.", False
        if isinstance(error, (PlaylistAccessError, SpotifyForbiddenError)):
            return 403, "playlist_access_denied", "You cannot modify this playlist.", False
        if isinstance(error, SpotifyAuthError):
            return 401, "authentication_required", "Please log in again before continuing.", True
        if isinstance(error, StaleHistoryError):
            # A fresh request must not silently select a different saved version.
            # Retrying this case requires an explicit history review.
            return 409, "history_changed", "The selected saved version changed. Review the current history.", False
        if isinstance(error, AlreadyAtOriginalError):
            return 400, "already_at_original", "Already at original playlist state.", False
        if isinstance(error, (NoHistoryError, StateError)):
            return 400, "history_unavailable", "No usable saved playlist history is available in this session.", False
        if isinstance(error, InvalidAlgorithmError):
            return 400, "invalid_algorithm", "Choose a valid shuffle method.", False
        if isinstance(error, UnsupportedPlaylistItems):
            return (
                409,
                "unsupported_playlist_items",
                "This playlist contains items that cannot be safely changed.",
                False,
            )
        return 502, "preflight_failed", "The playlist could not be checked. This attempt applied no changes.", True

    @staticmethod
    def _write_rejection(error):
        if isinstance(error, SpotifyPartialBatchError):
            if error.completed_batches:
                return None
            error = error.cause
        if isinstance(error, SpotifyAuthError):
            return 401, "Spotify rejected this action. Please log in again before trying again."
        if isinstance(error, SpotifyForbiddenError):
            return 403, "Spotify rejected this action. Check your access to this playlist before trying again."
        if isinstance(error, SpotifyNotFoundError):
            return 404, "Spotify could not find this playlist. Check that it is available before trying again."
        if isinstance(error, SpotifyQuotaExceededError):
            return 429, "Spotify's request quota has been reached. Try again after the quota resets."
        if isinstance(error, SpotifyRateLimitError):
            return 429, "Spotify is limiting requests. Wait a moment before trying again."
        if isinstance(error, SpotifyWriteRejectedError):
            return 502, "Spotify rejected this action before applying changes. Review the action before trying again."
        return None

    def _failure(self, operation, playlist_id, metadata, error):
        logger.warning("Direct %s action did not complete", operation.kind, exc_info=True)
        resources = operation.resources
        dispatched = any(r["dispatched"] for r in resources)
        if dispatched:
            operation.error_code = operation.error_code or "write_unverified"
            outcome = operation.outcome()
            # The writer proves zero effects before enabling retry. An exception's
            # type alone cannot establish that, especially after a partial batch
            # or when a typed rejection occurs during verification of a write.
            rejection = self._write_rejection(error) if outcome["retryable"] else None
            message = "The action did not complete. Review the playlist result before another attempt."
            status, message = rejection or (502, message)
            return {
                "status": "failed",
                "http_status": status,
                "message": message,
                "error": message,
                "operation": outcome,
            }
        status, code, message, retryable = self.preflight_failure(error)
        if operation.error_code == "resource_changed":
            status, code, message, retryable = (
                409,
                "resource_changed",
                "Playlist changed. Review its current contents.",
                True,
            )
        operation.error_code, operation.retryable = code, retryable
        if not resources and metadata:
            operation.resources.append(
                {
                    "resource": playlist_id,
                    "resource_name": metadata.get("name", "Playlist"),
                    "step": operation.kind,
                    "mutation_type": "track_order" if operation.kind == "shuffle" else "track_membership",
                    "status": "failed",
                    "dispatched": False,
                    "affected_count": 0,
                    "retryable": retryable,
                    "error_code": code,
                    "verification": "ordered",
                }
            )
        for resource in resources:
            resource.update(status="failed", affected_count=0, retryable=retryable, error_code=code)
        return {
            "status": "failed",
            "http_status": status,
            "message": message,
            "error": message,
            "operation": operation.outcome(),
        }

    @staticmethod
    def _log_activity(kind, user, playlist_id, observed, algorithm):
        try:
            activity_type = {
                "shuffle": ActivityType.SHUFFLE,
                "snapshot_restore": ActivityType.SNAPSHOT_RESTORE,
            }[kind]
            ActivityLogService.log(
                user_id=user.id,
                activity_type=activity_type,
                description=f"{kind.replace('_', ' ').capitalize()} completed on '{observed.name}'",
                playlist_id=playlist_id,
                playlist_name=observed.name,
                metadata={"action": kind, "algorithm": algorithm, "track_count": len(observed.track_uris)},
            )
        except Exception:
            logger.warning("Could not record verified direct action Activity", exc_info=True)

    def inspect_tracks(self, playlist_id, user, *, desired_track_uris=None):
        operation = LiveOperation("track_inspection", self.api, user)
        try:
            desired = None if desired_track_uris is None else list(desired_track_uris)
            if desired is not None:
                self.validate_order(desired)
            observed = operation.read(playlist_id)
            resource = operation.plan(
                playlist_id,
                desired if desired is not None else observed.track_uris,
                "track_inspection",
                mutation_type="track_order",
            )
            # An observation has no pre-write baseline to report.
            resource.pop("baseline_track_uris", None)
            matches = desired is not None and desired == list(observed.track_uris)
            if not matches:
                code = "intent_unavailable" if desired is None else "intent_not_observed"
                resource.update(status="unknown", affected_count=None, error_code=code)
                operation.error_code = code
                if desired is None:
                    resource.pop("desired_track_uris", None)
            return {
                "status": "success" if matches else "failed",
                "http_status": 200 if matches else 502,
                "inspection_only": True,
                "operation": operation.outcome(),
                "message": "Current contents match the retained order."
                if matches
                else "Current contents do not establish the earlier action's outcome.",
            }
        except Exception as error:
            status, code, message, _ = self.preflight_failure(error)
            return {
                **direct_rejected_result(
                    "track_inspection", http_status=status, error_code=code, message=message, unknown=True
                ),
                "inspection_only": True,
            }
