"""Verify Workshop source effects independently of the confirmed target.

These operations do not acquire the target lock and do not claim multi-resource
serialization. Fresh scope and order checks cannot exclude external Spotify
edits or writers operating under a different target key. Recovery receipts live
in the current page; append inspection never dispatches another append.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from shuffify.enums import PendingRaidStatus
from shuffify.services.pending_raid_service import (
    PendingRaidSelectionError,
    PendingRaidService,
)
from shuffify.services.playlist_pair_service import PlaylistPairService
from shuffify.services.playlist_service import PlaylistAccessError
from shuffify.services.raid_link_service import RaidLinkService
from shuffify.services.workshop_commit_service import (
    UnsupportedPlaylistItems,
    WorkshopCommitService,
)
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkshopFinalizeResult:
    resource: str | None
    resource_name: str | None
    status: str
    message: str
    http_status: int = 200
    affected_count: int | None = 0
    error_code: str | None = None
    retryable: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def request_rejected(
        cls, body, message, http_status, count_key, *, error_code="preflight_failed"
    ):
        """Reject invalid tasks; failed inspections prove no prior outcome.

        Callers cover schema and CSRF (400), authentication (401), and
        database availability (503). Inspection never proves a prior effect.
        """
        source = body.get("source_playlist_id") if isinstance(body, dict) else None
        inspect_only = isinstance(body, dict) and body.get("inspect_only") is True
        return cls(
            source if isinstance(source, str) else None,
            None,
            "unknown" if inspect_only else "failed",
            message,
            http_status=http_status,
            affected_count=None if inspect_only else 0,
            error_code=error_code,
            retryable=not inspect_only and http_status in (401, 403, 503),
            extra={count_key: None if inspect_only else 0},
        )

    def to_dict(self):
        complete = self.status in ("complete", "not_required")
        return {
            "success": complete and self.http_status == 200,
            "resource": self.resource,
            "resource_name": self.resource_name,
            "status": self.status,
            "message": self.message,
            "affected_count": self.affected_count,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "aggregate_status": (
                "complete"
                if complete
                else "unknown" if self.status == "unknown" else "partial"
            ),
            **self.extra,
        }


class _PreflightFailure(Exception):
    def __init__(self, code, message, http_status=409, retryable=False):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable


class WorkshopFinalizeService:
    def __init__(self, api):
        self.api = api
        self.reader = WorkshopCommitService(api)

    def source_context(self, target_id, user, *, raid=True, archive=True):
        """Resolve only requested sources with fresh authorized server names."""
        try:
            target = self._read(target_id, user)
            context = {
                "success": True,
                "target": {"id": target_id, "name": target.name},
                "raid": None,
                "archive": None,
            }
            for kind, requested in (("raid", raid), ("archive", archive)):
                if not requested:
                    continue
                source_id = self._linked_source(kind, target_id, user)
                if source_id is None:
                    continue
                self._distinct_source(target_id, source_id)
                observed = self._read(source_id, user)
                context[kind] = {"id": source_id, "name": observed.name}
                if kind == "archive":
                    context[kind]["baseline_track_uris"] = list(observed.track_uris)
            return context, 200
        except _PreflightFailure as error:
            return {
                "success": False,
                "message": error.message,
                "error_code": error.code,
                "retryable": error.retryable,
            }, error.http_status

    def finalize_raids(
        self,
        target_id,
        user,
        *,
        source_playlist_id,
        target_track_uris,
        track_ids,
        inspect_only=False,
    ):
        source_id, name = source_playlist_id, None
        try:
            self._scope("raid", target_id, user, source_id)
            try:
                tracks = PendingRaidService.get_for_finalize(
                    user.id, target_id, track_ids
                )
            except PendingRaidSelectionError:
                raise _PreflightFailure(
                    "invalid_track_selection",
                    "Some selected tracks are unavailable for this playlist.",
                ) from None
            except Exception as error:
                self._log("database_read", error)
                raise _PreflightFailure(
                    "database_read_failed",
                    "Could not read the selected tracks.",
                    502,
                    True,
                ) from None
            uris = tuple(t.track_uri for t in tracks)
            count = len(tracks)
            self._target(target_id, user, target_track_uris, uris)
            source = self._read(source_id, user) if source_id else None
            name = source.name if source else None
            # Count selected records whose source or database state still
            # needs work, rather than claiming every verified retry changed it.
            affected_uris = {
                track.track_uri
                for track in tracks
                if track.status == PendingRaidStatus.PENDING
            }
            if source:
                affected_uris.update(set(uris).intersection(source.track_uris))
        except _PreflightFailure as error:
            return self._failed(
                source_id, name, "promoted_count", error, inspect_only=inspect_only
            )

        # The discovery source on each pending row is deliberately irrelevant:
        # the captured, currently linked collection is the only removal target.
        if source:
            removal = self._remove(
                source_id,
                name,
                user,
                source.track_uris,
                uris,
                "promoted_count",
                inspect_only,
            )
            if removal.status not in ("complete", "not_required"):
                return removal
        pending = any(t.status == PendingRaidStatus.PENDING for t in tracks)
        if pending and inspect_only:
            return self._database_pending(source_id, name, "pending")
        if pending:
            try:
                PendingRaidService.complete_finalize(tracks)
            except Exception as error:
                self._log("database_update", error)
                return self._database_pending(source_id, name, "failed")
        return WorkshopFinalizeResult(
            source_id,
            name,
            "complete" if affected_uris else "not_required",
            "Collection changes verified.",
            affected_count=len(affected_uris),
            extra={
                "promoted_count": count,
                "source_status": "complete" if source else "not_required",
                "database_status": "complete",
            },
        )

    def finalize_restore(
        self,
        target_id,
        user,
        *,
        source_playlist_id,
        target_track_uris,
        track_uris,
        inspect_only=False,
    ):
        source_id, name = source_playlist_id, None
        try:
            self._scope("archive", target_id, user, source_id)
            self._target(target_id, user, target_track_uris, track_uris)
            source = self._read(source_id, user)
            name = source.name
        except _PreflightFailure as error:
            return self._failed(
                source_id, name, "restored_count", error, inspect_only=inspect_only
            )
        return self._remove(
            source_id,
            name,
            user,
            source.track_uris,
            tuple(track_uris),
            "restored_count",
            inspect_only,
        )

    def append_archive(
        self,
        target_id,
        user,
        *,
        source_playlist_id,
        target_track_uris,
        track_uris,
        baseline_track_uris,
        inspect_only=False,
    ):
        source_id, name = source_playlist_id, None
        payload, baseline = tuple(track_uris), tuple(baseline_track_uris)
        expected = baseline + payload
        try:
            self._scope("archive", target_id, user, source_id)
            self._target(target_id, user, target_track_uris)
            source = self._read(source_id, user)
            name = source.name
        except _PreflightFailure as error:
            return self._failed(
                source_id, name, "archived_count", error, inspect_only=inspect_only
            )

        if source.track_uris == expected:
            return self._complete(
                source_id, name, "archived_count", len(payload), affected=0
            )
        if inspect_only:
            # Even an unchanged baseline does not prove a dispatched append
            # never happened: another writer could have removed those items.
            return self._unknown(source_id, name, "archived_count")
        if source.track_uris != baseline:
            return self._failed(
                source_id,
                name,
                "archived_count",
                _PreflightFailure(
                    "source_changed",
                    "The archive changed. Review its current contents.",
                ),
            )
        try:
            self.api.playlist_add_items(source_id, list(payload))
        except Exception as error:
            return self._write_failure(source_id, name, "archived_count", error)
        try:
            after = self._read(source_id, user)
        except _PreflightFailure:
            return self._unknown(source_id, name, "archived_count")
        if after.track_uris != expected:
            return self._unknown(source_id, name, "archived_count")
        return self._complete(source_id, name, "archived_count", len(payload))

    def _remove(self, source_id, name, user, current, uris, count_key, inspect_only):
        requested = set(uris)
        present = [uri for uri in current if uri in requested]
        if not present:
            return self._complete(source_id, name, count_key, len(uris), affected=0)
        if inspect_only:
            return self._unknown(source_id, name, count_key)
        try:
            # Strict path: remove from the captured source, not whatever the link
            # names now, and verify the removal below; a failure is reported
            # rather than swallowed.
            self.api.playlist_remove_items(source_id, list(dict.fromkeys(uris)))
        except Exception as error:
            return self._write_failure(source_id, name, count_key, error)
        try:
            after = self._read(source_id, user)
        except _PreflightFailure:
            return self._unknown(source_id, name, count_key)
        if requested.intersection(after.track_uris):
            return self._unknown(source_id, name, count_key)
        return self._complete(
            source_id, name, count_key, len(uris), affected=len(present)
        )

    def _target(self, target_id, user, expected, membership=()):
        target = self._read(target_id, user)
        if target.track_uris != tuple(expected):
            raise _PreflightFailure(
                "target_changed",
                "The saved playlist changed. Review it before cleanup.",
            )
        if not set(membership).issubset(target.track_uris):
            raise _PreflightFailure(
                "target_tracks_missing",
                "The saved playlist does not contain every selected track.",
            )

    def _scope(self, kind, target_id, user, captured):
        linked = self._linked_source(kind, target_id, user)
        if linked != captured or (kind == "archive" and linked is None):
            raise _PreflightFailure(
                "source_changed",
                "The linked source changed. Review the original source.",
            )
        self._distinct_source(target_id, captured)

    @staticmethod
    def _distinct_source(target_id, source_id):
        if target_id == source_id:
            raise _PreflightFailure(
                "invalid_source",
                "The source must be different from the saved playlist.",
            )

    def _linked_source(self, kind, target_id, user):
        try:
            if kind == "raid":
                link = RaidLinkService.get_link_for_playlist(user.id, target_id)
                return link.raid_playlist_id if link else None
            pair = PlaylistPairService.get_pair_for_playlist(user.id, target_id)
            return pair.archive_playlist_id if pair else None
        except Exception as error:
            self._log("database_read", error)
            raise _PreflightFailure(
                "database_read_failed", "Could not read the linked source.", 502, True
            ) from None

    def _read(self, playlist_id, user):
        try:
            return self.reader.read_complete(playlist_id, user.spotify_id)
        except (PlaylistAccessError, SpotifyForbiddenError, SpotifyNotFoundError):
            raise _PreflightFailure(
                "playlist_access_denied",
                "This playlist is no longer available to edit.",
            ) from None
        except SpotifyAuthError:
            raise _PreflightFailure(
                "authentication_required", "Log in again to check this playlist.", 401
            ) from None
        except UnsupportedPlaylistItems:
            raise _PreflightFailure(
                "unsupported_playlist_items",
                "This playlist contains unsupported items.",
            ) from None
        except Exception as error:
            self._log("upstream_read", error)
            raise _PreflightFailure(
                "upstream_read_failed",
                "Could not read the complete Spotify playlist.",
                502,
                True,
            ) from None

    @staticmethod
    def _complete(source_id, name, count_key, count, affected=None):
        return WorkshopFinalizeResult(
            source_id,
            name,
            "not_required" if affected == 0 else "complete",
            "Source changes verified.",
            affected_count=count if affected is None else affected,
            extra={count_key: count},
        )

    @staticmethod
    def _failed(source_id, name, count_key, error, *, inspect_only=False):
        if inspect_only:
            # Failed authorization, scope validation, or observation cannot
            # establish that an earlier ambiguous write never applied.
            return WorkshopFinalizeResult(
                source_id,
                name,
                "unknown",
                error.message,
                http_status=error.http_status,
                affected_count=None,
                error_code=error.code,
                extra={count_key: None},
            )
        return WorkshopFinalizeResult(
            source_id,
            name,
            "failed",
            error.message,
            http_status=error.http_status,
            error_code=error.code,
            retryable=error.retryable,
            extra={count_key: 0},
        )

    @staticmethod
    def _unknown(source_id, name, count_key):
        return WorkshopFinalizeResult(
            source_id,
            name,
            "unknown",
            "Check the source playlist to verify this step.",
            http_status=502,
            affected_count=None,
            error_code="cleanup_unverified",
            retryable=False,
            extra={count_key: None},
        )

    def _write_failure(self, source_id, name, count_key, error):
        self._log("source_write", error)
        if isinstance(error, SpotifyPartialBatchError):
            if error.completed_batches:
                return self._unknown(source_id, name, count_key)
            error = error.cause
        if isinstance(
            error,
            (
                SpotifyWriteRejectedError,
                SpotifyRateLimitError,
                SpotifyNotFoundError,
                SpotifyAuthError,
                SpotifyForbiddenError,
            ),
        ):
            return self._failed(
                source_id,
                name,
                count_key,
                _PreflightFailure(
                    "source_write_rejected",
                    "Spotify did not accept this source change.",
                    502,
                    True,
                ),
            )
        return self._unknown(source_id, name, count_key)

    @staticmethod
    def _database_pending(source_id, name, status):
        return WorkshopFinalizeResult(
            source_id,
            name,
            "unknown",
            "Source removal is verified. The inbox update still needs to finish.",
            http_status=502,
            affected_count=None,
            error_code=(
                "database_update_failed"
                if status == "failed"
                else "database_update_pending"
            ),
            retryable=True,
            extra={
                "promoted_count": 0,
                "source_status": "complete" if source_id else "not_required",
                "database_status": status,
                "aggregate_status": "partial",
            },
        )

    @staticmethod
    def _log(stage, error):
        logger.warning("Workshop source %s failed (%s)", stage, type(error).__name__)
