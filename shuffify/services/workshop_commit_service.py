"""Compare fresh Workshop baselines and report verified target-save outcomes.

The target lock coordinates participating Shuffify writers only. Spotify does
not provide a conditional replacement, so external edits can still race us.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from shuffify.enums import ActivityType, SnapshotType
from shuffify.services.activity_log_service import ActivityLogService
from shuffify.services.playlist_lock import playlist_lock
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistService
from shuffify.services.playlist_snapshot_service import PlaylistSnapshotService
from shuffify.services.state_service import StateService
from shuffify.services.track_lock_service import TrackLockService
from shuffify.spotify.api import playlist_item_count
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
)

logger = logging.getLogger(__name__)
_TRACK_URI = re.compile(r"spotify:track:[A-Za-z0-9]{22}")
_LOCK_WAIT_SECONDS = 2.0
_BATCH_SIZE = 100
_READ_PAGE_SIZE = 50


class IncompletePlaylistRead(Exception):
    """The response cannot establish one complete, consistent track order."""


class UnsupportedPlaylistItems(Exception):
    """The playlist contains items Workshop cannot safely represent."""


@dataclass(frozen=True)
class ObservedPlaylist:
    name: str
    track_uris: tuple[str, ...]
    tracks: tuple[dict, ...]
    loaded_at: str

    def response_fields(self) -> dict:
        return {
            "current_track_uris": list(self.track_uris),
            "current_tracks": list(self.tracks),
            "loaded_at": self.loaded_at,
            "representable": True,
        }


@dataclass(frozen=True)
class WorkshopTargetResult:
    """Public target outcome, independent of source-finalization workflows."""

    target_status: str
    message: str
    http_status: int = 200
    error_code: str | None = None
    requires_refresh: bool = False
    confirmed_batches: int = 0
    total_batches: int = 0
    retryable: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.http_status == 200,
            "message": self.message,
            "target_status": self.target_status,
            "error_code": self.error_code,
            "requires_refresh": self.requires_refresh,
            "confirmed_batches": self.confirmed_batches,
            "total_batches": self.total_batches,
            "retryable": self.retryable,
            **self.extra,
        }


class WorkshopCommitService:
    def __init__(self, api):
        self.api = api
        self.playlists = PlaylistService(api)

    def read_current(self, playlist_id: str, user) -> WorkshopTargetResult:
        """Read without resetting session state or mutating any playlist."""
        try:
            with playlist_lock(playlist_id, timeout_s=_LOCK_WAIT_SECONDS) as acquired:
                if not acquired:
                    return self._busy()
                observed, failure = self._fresh_read(playlist_id, user)
                return failure or WorkshopTargetResult(
                    "not_written",
                    "Current Spotify playlist loaded.",
                    extra=observed.response_fields(),
                )
        except SpotifyForbiddenError as error:
            raise PlaylistAccessError(
                "Spotify refused access to this playlist."
            ) from error
        except (SpotifyAuthError, PlaylistAccessError):
            raise
        except Exception as error:
            self._log_failure("read", playlist_id, error)
            return self._read_failed()

    def commit(
        self,
        playlist_id: str,
        user,
        session,
        baseline_track_uris: list[str],
        track_uris: list[str],
    ) -> WorkshopTargetResult:
        # Callers cannot mutate the revision being compared or saved.
        baseline, desired = tuple(baseline_track_uris), tuple(track_uris)
        try:
            with playlist_lock(playlist_id, timeout_s=_LOCK_WAIT_SECONDS) as acquired:
                if not acquired:
                    return self._busy()
                observed, failure = self._fresh_read(playlist_id, user)
                if failure:
                    return failure

                if observed.track_uris == desired:
                    return self._confirmed(
                        "already_applied",
                        playlist_id,
                        user,
                        session,
                        observed,
                        observed,
                        0,
                    )
                if observed.track_uris != baseline:
                    return WorkshopTargetResult(
                        "not_written",
                        "Spotify changed since this draft was loaded. Your draft is "
                        "still here. Review the current playlist before saving.",
                        409,
                        "stale_draft",
                        True,
                        extra=observed.response_fields(),
                    )

                self._snapshot(user.id, playlist_id, observed)
                return self._write_and_verify(
                    playlist_id, user, session, observed, desired
                )
        except SpotifyForbiddenError as error:
            raise PlaylistAccessError(
                "Spotify refused access to this playlist."
            ) from error
        except (SpotifyAuthError, PlaylistAccessError):
            raise
        except Exception as error:
            # This boundary covers failure to acquire the coordination resource.
            # Every dispatched write is classified inside _write_and_verify.
            self._log_failure("coordination", playlist_id, error)
            return WorkshopTargetResult(
                "not_written",
                "Could not prepare this save. Your draft is still here.",
                502,
                "prewrite_failed",
                retryable=True,
            )

    def _fresh_read(self, playlist_id, user):
        """Return (observed, None), or (None, a not-written result) on failure."""
        try:
            return self._read_current(playlist_id, user.spotify_id), None
        except UnsupportedPlaylistItems:
            return None, self._unsupported()
        except (SpotifyAuthError, PlaylistAccessError, SpotifyForbiddenError):
            raise
        except Exception as error:
            self._log_failure("read", playlist_id, error)
            return None, self._read_failed()

    def read_complete(self, playlist_id: str, user_spotify_id: str) -> ObservedPlaylist:
        """Read and authorize a complete fresh order, without acquiring a lock.

        Source finalizers need this reader without nesting the nonreentrant
        target lock. This method does not promise coordination with writers.
        Exceptions preserve whether observation or authorization failed.
        """
        return self._read_current(playlist_id, user_spotify_id)

    def _read_current(self, playlist_id: str, user_spotify_id: str) -> ObservedPlaylist:
        before = self.playlists.validate_user_can_edit(
            playlist_id,
            user_spotify_id,
            skip_cache=True,
        )
        tracks, total, offset = [], None, 0
        while True:
            page = self.api.get_playlist_items_raw(
                playlist_id,
                limit=_READ_PAGE_SIZE,
                offset=offset,
            )
            if not isinstance(page, dict):
                raise IncompletePlaylistRead()
            items, page_total = page.get("items"), page.get("total")
            if (
                not isinstance(items, list)
                or type(page_total) is not int
                or page_total < 0
                or type(page.get("offset")) is not int
                or page["offset"] != offset
                or (total is not None and page_total != total)
                or len(items) > _READ_PAGE_SIZE
            ):
                raise IncompletePlaylistRead()
            total = page_total
            end = offset + len(items)
            if end > total or (end < total and not items):
                raise IncompletePlaylistRead()
            for entry in items:
                tracks.append(self._represent_track(entry))
            next_page = page.get("next")
            if end == total:
                if next_page:
                    raise IncompletePlaylistRead()
                break
            if not isinstance(next_page, str):
                raise IncompletePlaylistRead()
            next_offsets = parse_qs(urlparse(next_page).query).get("offset")
            if next_offsets != [str(end)]:
                raise IncompletePlaylistRead()
            offset = end

        after = self.api.get_playlist(playlist_id, skip_cache=True)
        if not isinstance(after, dict):
            raise IncompletePlaylistRead()
        # A stable count alone misses order-only changes between pages. Fail
        # closed when Spotify omits the revision needed to bracket this read.
        snapshot_id = before.get("snapshot_id")
        if (
            not isinstance(snapshot_id, str)
            or not snapshot_id
            or snapshot_id != after.get("snapshot_id")
        ):
            raise IncompletePlaylistRead()
        for metadata in (before, after):
            count = playlist_item_count(metadata, default=None)
            if count is not None and count != total:
                raise IncompletePlaylistRead()
        return ObservedPlaylist(
            name=before.get("name", "Playlist"),
            track_uris=tuple(track["uri"] for track in tracks),
            tracks=tuple(tracks),
            loaded_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _represent_track(entry: Any) -> dict:
        if not isinstance(entry, dict) or entry.get("is_local"):
            raise UnsupportedPlaylistItems()
        track = entry.get("item") if "item" in entry else entry.get("track")
        if (
            not isinstance(track, dict)
            or track.get("is_local")
            or track.get("type", "track") != "track"
            or not isinstance(track.get("uri"), str)
            or not _TRACK_URI.fullmatch(track["uri"])
        ):
            raise UnsupportedPlaylistItems()
        album = track.get("album") or {}
        images = album.get("images") or []
        return {
            "id": track.get("id") or track["uri"].rsplit(":", 1)[1],
            "uri": track["uri"],
            "name": track.get("name", "Track"),
            "artists": [
                artist.get("name", "Unknown") for artist in track.get("artists", [])
            ],
            "duration_ms": track.get("duration_ms", 0),
            "added_at": entry.get("added_at"),
            "is_local": False,
            "album_name": album.get("name", ""),
            "album_image_url": images[0].get("url", "") if images else "",
            "track_url": (track.get("external_urls") or {}).get("spotify", ""),
        }

    def _write_and_verify(self, playlist_id, user, session, before, desired):
        total = max(1, (len(desired) + _BATCH_SIZE - 1) // _BATCH_SIZE)
        try:
            self.playlists.update_playlist_tracks(playlist_id, list(desired))
        except SpotifyPartialBatchError as error:
            self._log_failure("write", playlist_id, error)
            if error.completed_batches:
                return WorkshopTargetResult(
                    "partial",
                    "Spotify received part of this save. Check the playlist before trying again.",
                    502,
                    "partial_write",
                    True,
                    error.completed_batches,
                    error.total_batches,
                )
            return self._first_write_failure(error.cause, total)
        except (SpotifyAuthError, SpotifyForbiddenError):
            raise
        except Exception as error:
            self._log_failure("write", playlist_id, error)
            return self._first_write_failure(error, total)

        try:
            observed = self._read_current(playlist_id, user.spotify_id)
        except Exception as error:
            self._log_failure("verification", playlist_id, error)
            return self._unknown(total, total)
        if observed.track_uris != desired:
            return self._unknown(total, total, observed.response_fields())
        return self._confirmed(
            "saved", playlist_id, user, session, before, observed, total
        )

    def _first_write_failure(self, error, total):
        if isinstance(error, (SpotifyAuthError, SpotifyForbiddenError)):
            raise error
        if isinstance(
            error,
            (SpotifyWriteRejectedError, SpotifyRateLimitError, SpotifyNotFoundError),
        ):
            return WorkshopTargetResult(
                "not_written",
                "Spotify did not accept this save. Your draft is still here.",
                502,
                "prewrite_failed",
                total_batches=total,
                retryable=True,
            )
        return self._unknown(0, total)

    @staticmethod
    def _confirmed(status, playlist_id, user, session, before, observed, batches):
        before_uris, saved_uris = list(before.track_uris), list(observed.track_uris)
        extra = {**observed.response_fields(), "saved_track_uris": saved_uris}
        # Bookkeeping cannot turn a verified Spotify save into a retry prompt.
        try:
            state = StateService.ensure_playlist_initialized(
                session, playlist_id, before_uris
            )
            # Undo must restore the actual pre-save order, including edits
            # outside this draft, rather than an older session state.
            pending = [before_uris, saved_uris] if status == "saved" else [saved_uris]
            for uris in pending:
                if StateService.get_current_uris(session, playlist_id) != uris:
                    state = StateService.record_new_state(
                        session, playlist_id, list(uris)
                    )
            extra["playlist_state"] = state.to_dict()
            TrackLockService.safe_reconcile_positions(
                user.id, playlist_id, list(saved_uris)
            )
            if status == "saved":
                ActivityLogService.log(
                    user_id=user.id,
                    activity_type=ActivityType.WORKSHOP_COMMIT,
                    description=f"Committed workshop changes to '{observed.name}'",
                    playlist_id=playlist_id,
                    playlist_name=observed.name,
                    metadata={"track_count": len(observed.track_uris)},
                )
        except Exception as error:
            WorkshopCommitService._log_failure("bookkeeping", playlist_id, error)
        return WorkshopTargetResult(
            status,
            (
                "Playlist saved to Spotify!"
                if status == "saved"
                else "This order is already on Spotify."
            ),
            confirmed_batches=batches,
            total_batches=batches,
            extra=extra,
        )

    @staticmethod
    def _snapshot(user_id, playlist_id, observed):
        try:
            if PlaylistSnapshotService.is_auto_snapshot_enabled(user_id):
                PlaylistSnapshotService.create_snapshot(
                    user_id=user_id,
                    playlist_id=playlist_id,
                    playlist_name=observed.name,
                    track_uris=list(observed.track_uris),
                    snapshot_type=SnapshotType.AUTO_PRE_COMMIT,
                    trigger_description="Before workshop commit",
                )
        except Exception as error:
            WorkshopCommitService._log_failure("snapshot", playlist_id, error)

    @staticmethod
    def _log_failure(stage, playlist_id, error):
        # Exception strings may contain upstream response bodies or credentials.
        logger.warning(
            "Workshop %s failed for %s (%s)", stage, playlist_id, type(error).__name__
        )

    @staticmethod
    def _busy():
        return WorkshopTargetResult(
            "not_written",
            "Another operation is updating this playlist. Your draft is still here.",
            409,
            "operation_in_progress",
            True,
            retryable=True,
        )

    @staticmethod
    def _unsupported():
        return WorkshopTargetResult(
            "not_written",
            "This playlist contains items Workshop cannot safely save. Your draft is still here.",
            409,
            "unsupported_playlist_items",
            True,
            extra={"representable": False},
        )

    @staticmethod
    def _read_failed():
        return WorkshopTargetResult(
            "not_written",
            "Could not read the complete Spotify playlist. Your draft is still here.",
            502,
            "upstream_read_failed",
            True,
            retryable=True,
        )

    @staticmethod
    def _unknown(confirmed, total, extra=None):
        return WorkshopTargetResult(
            "unknown",
            "Spotify may have changed. Check the playlist before trying again.",
            502,
            "write_unverified",
            True,
            confirmed,
            total,
            extra=extra or {},
        )
