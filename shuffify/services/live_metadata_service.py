"""Fresh, invocation-local metadata outcomes; no track or history mutations."""

from copy import deepcopy
from datetime import datetime, timezone

from shuffify.services.live_operation_service import (
    _REJECTED,
    direct_rejected_result,
    operation_result,
)
from shuffify.services.live_review_bindings import REVIEW_CHANGED, privacy_scope
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistService
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyRateLimitError,
)


class LiveMetadataService:
    """Verify explicit visibility intent without entering the track pipeline.

    These observations do not lock metadata against other tabs or Spotify edits.
    A write acknowledgement alone never establishes the requested visibility.
    """

    def __init__(self, api):
        self.api = api
        self.playlists = PlaylistService(api)

    @staticmethod
    def _propagate_control(error):
        from shuffify.services.operations.control import propagate_control

        propagate_control(error)

    @staticmethod
    def _result(resource, *, message, error_code=None, retryable=False, public=None, http_status=502):
        operation = operation_result(
            "privacy", resources=[resource], error_code=error_code, retryable=retryable
        )
        complete = operation["aggregate_status"] == "complete"
        result = {
            "status": "success" if complete else "failed",
            "http_status": 200 if complete else http_status,
            "message": message,
            "operation": operation,
        }
        if type(public) is bool:
            result["public"] = public
        return result

    @staticmethod
    def read_failure(error, *, inspection=False):
        if isinstance(error, SpotifyNotFoundError) or (
            isinstance(error, PlaylistAccessError) and isinstance(error.__cause__, SpotifyNotFoundError)
        ):
            code, status = "playlist_not_found", 404
            message = "Playlist not found."
        elif isinstance(error, (PlaylistAccessError, SpotifyForbiddenError)):
            code, status = "access_denied", 403
            message = "You do not have permission to change this playlist."
        elif isinstance(error, SpotifyAuthError):
            code, status = "authentication_required", 401
            message = "Please log in before changing playlist visibility."
        else:
            code, status = "metadata_unavailable", 502
            message = "Playlist visibility could not be read. No changes were sent."
        if inspection:
            message = "Current playlist visibility could not be verified."
        return direct_rejected_result(
            "privacy", error_code=code, http_status=status, unknown=inspection,
            retryable=not inspection and status in (401, 502), message=message,
        )

    @staticmethod
    def _resource(playlist_id, metadata, desired):
        resource = {
            "resource": playlist_id,
            "resource_name": metadata.get("name"),
            "step": "privacy",
            "mutation_type": "metadata",
            "status": "failed",
            "dispatched": False,
            "affected_count": 0,
            "retryable": False,
            "error_code": None,
            "verification": "metadata",
            "loaded_at": datetime.now(timezone.utc).isoformat(),
        }
        if type(metadata.get("public")) is bool:
            resource["current_metadata"] = {"public": metadata["public"]}
        if type(desired) is bool:
            resource["desired_metadata"] = {"public": desired}
        return resource

    def set_visibility(self, playlist_id, user, *, public, reviewed_scope=None, runtime=None):
        """Send at most one explicit Boolean update and verify fresh metadata.

        A reviewed request is compared after fresh authorization and before
        the write; a match never substitutes for that authorization.
        """
        if type(public) is not bool:
            return direct_rejected_result(
                "privacy", error_code="invalid_public", http_status=400,
                message="The 'public' field must be true or false.",
            )
        if runtime is not None:
            return self._set_visibility_durable(playlist_id, user, public, reviewed_scope, runtime)
        try:
            before = self.playlists.validate_user_can_edit(
                playlist_id, user.spotify_id, skip_cache=True
            )
        except Exception as error:
            self._propagate_control(error)
            return self.read_failure(error)

        resource = self._resource(playlist_id, before, public)
        if reviewed_scope is not None and reviewed_scope != privacy_scope(playlist_id, public):
            resource["error_code"] = "review_changed"
            return self._result(
                resource, error_code="review_changed", http_status=409, message=REVIEW_CHANGED,
            )
        if type(before.get("public")) is not bool:
            resource.update(error_code="metadata_unavailable", retryable=True)
            return self._result(
                resource, error_code="metadata_unavailable", retryable=True,
                message="Playlist visibility could not be read. No changes were sent.",
            )
        resource["baseline_metadata"] = {"public": before["public"]}
        state = "public" if public else "private"
        if before["public"] is public:
            resource["status"] = "not_required"
            return self._result(
                resource, public=public, message=f"Playlist is already {state}.",
            )

        # Baseline metadata must never masquerade as a post-dispatch observation.
        resource.pop("current_metadata", None)
        resource.pop("loaded_at", None)
        resource.update(dispatched=True, status="unknown", affected_count=None)
        try:
            self.api.update_playlist_details(playlist_id, public=public)
        except Exception as error:
            self._propagate_control(error)
            rejected = isinstance(error, _REJECTED)
            code = "write_rejected" if rejected else "write_unverified"
            resource.update(
                status="failed" if rejected else "unknown",
                affected_count=0 if rejected else None,
                error_code=code, retryable=rejected,
            )
            status = 502
            if isinstance(error, SpotifyAuthError):
                status = 401
            elif isinstance(error, SpotifyForbiddenError):
                status = 403
            elif isinstance(error, SpotifyNotFoundError):
                status = 404
            elif isinstance(error, SpotifyRateLimitError):
                status = 429
            return self._result(
                resource, error_code=code, retryable=rejected, http_status=status,
                message=(
                    "Spotify rejected the visibility change. No changes were made."
                    if rejected else
                    "The visibility change could not be verified. Check the playlist before another change."
                ),
            )
        try:
            after = self.playlists.validate_user_can_edit(
                playlist_id, user.spotify_id, skip_cache=True
            )
        except Exception as error:
            self._propagate_control(error)
            after = {}
        actual = after.get("public")
        if type(actual) is bool:
            resource.update(
                current_metadata={"public": actual},
                loaded_at=datetime.now(timezone.utc).isoformat(),
            )
        if type(actual) is not bool or actual is not public:
            resource["error_code"] = "verification_failed"
            return self._result(
                resource, error_code="verification_failed",
                message="The visibility change could not be verified. Check the playlist before another change.",
            )
        resource.update(status="complete", affected_count=1)
        return self._result(
            resource, public=actual, message=f"Playlist set to {state}",
        )

    def _set_visibility_durable(self, playlist_id, user, public, reviewed_scope, runtime):
        """Keep original metadata intent while replaying the journaled update once."""
        resource = None
        try:
            def prepare():
                before = self.playlists.validate_user_can_edit(playlist_id, user.spotify_id, skip_cache=True)
                item = self._resource(playlist_id, before, public)
                if reviewed_scope is not None and reviewed_scope != privacy_scope(playlist_id, public):
                    item["error_code"] = "review_changed"
                    return {"failure": self._result(item, error_code="review_changed", http_status=409,
                                                    message=REVIEW_CHANGED)}
                if type(before.get("public")) is not bool:
                    item.update(error_code="metadata_unavailable", retryable=True)
                    return {"failure": self._result(item, error_code="metadata_unavailable", retryable=True,
                        message="Playlist visibility could not be read. No changes were sent.")}
                return {"before": before, "changed": before["public"] is not public}

            prepared = runtime.prepare("visibility.action.v1", prepare)
            if "failure" in prepared:
                return prepared["failure"]
            before, changed = prepared["before"], prepared["changed"]
            resource = self._resource(playlist_id, before, public)
            resource["baseline_metadata"] = {"public": before["public"]}
            state = "public" if public else "private"
            if not changed:
                expected = {"scope": "metadata", "snapshot_id": before.get("snapshot_id"),
                            "metadata": {key: before.get(key) for key in ("name", "public", "description")}}
                runtime.journal.ensure_details(playlist_id, expected_baseline=expected, public=public)
                runtime.journal.skip_to(runtime.journal.cursor + 1)
                checked = runtime.journal.resume()
                if checked["state"] == "needs_attention":
                    from shuffify.services.operations.journal import NeedsAttention

                    raise NeedsAttention(checked["recovery"]["reason"])
                resource["status"] = "not_required"
                return self._result(resource, public=public, message=f"Playlist is already {state}.")
            resource.pop("current_metadata", None)
            resource.pop("loaded_at", None)
            resource.update(dispatched=True, status="unknown", affected_count=None)

            verified_resource = deepcopy(resource)
            verified_resource.update(current_metadata={"public": public}, status="complete", affected_count=1)
            verified_body = self._result(verified_resource, public=public, message=f"Playlist set to {state}")

            def apply():
                expected = {"scope": "metadata", "snapshot_id": before.get("snapshot_id"),
                            "metadata": {key: before.get(key) for key in ("name", "public", "description")}}
                runtime.journal.ensure_details(playlist_id, expected_baseline=expected, public=public)
                self.api.update_playlist_details(playlist_id, public=public)
                after = self.playlists.validate_user_can_edit(playlist_id, user.spotify_id, skip_cache=True)
                if type(after.get("public")) is not bool or after["public"] is not public:
                    from shuffify.spotify.exceptions import SpotifyWriteUnverifiedError

                    raise SpotifyWriteUnverifiedError("Playlist visibility verification failed")
                runtime.protect_verified("direct.publication.v1", verified_body)
                resource.update(current_metadata={"public": after["public"]},
                                loaded_at=datetime.now(timezone.utc).isoformat(), status="complete", affected_count=1)
                runtime.protect_verified("direct.publication.v1", self._result(
                    resource, public=after["public"], message=f"Playlist set to {state}",
                ))
                return after

            after = runtime.step("visibility.target.v1", apply)
            resource.update(current_metadata={"public": after["public"]},
                            loaded_at=datetime.now(timezone.utc).isoformat(), status="complete", affected_count=1)
            return self._result(resource, public=after["public"], message=f"Playlist set to {state}")
        except Exception as error:
            from shuffify.services.operations.journal import NeedsAttention

            runtime.fail_if_verified(error)
            if isinstance(error, NeedsAttention) and resource is not None:
                observed = runtime.journal.doc.get("observations", {}).get(playlist_id, {}).get("metadata", {})
                if type(observed.get("public")) is bool:
                    resource.update(current_metadata={"public": observed["public"]},
                                    loaded_at=datetime.now(timezone.utc).isoformat())
                else:
                    resource.pop("current_metadata", None)
                    resource.pop("loaded_at", None)
                error.result = self._result(resource, error_code="write_unverified",
                    message="The visibility change could not be verified. Check the playlist before another change.")
            self._propagate_control(error)
            if resource is None:
                return self.read_failure(error)
            rejected = isinstance(error, _REJECTED)
            code = "write_rejected" if rejected else "write_unverified"
            resource.update(status="failed" if rejected else "unknown", affected_count=0 if rejected else None,
                            error_code=code, retryable=rejected)
            status = (401 if isinstance(error, SpotifyAuthError) else
                      403 if isinstance(error, SpotifyForbiddenError) else
                      404 if isinstance(error, SpotifyNotFoundError) else
                      429 if isinstance(error, SpotifyRateLimitError) else 502)
            return self._result(resource, error_code=code, retryable=rejected, http_status=status,
                message="The visibility change could not be verified. Check the playlist before another change.")

    def inspect_visibility(self, playlist_id, user, *, desired_public=None):
        """Observe retained intent without replaying a write or claiming causation."""
        if desired_public is not None and type(desired_public) is not bool:
            result = direct_rejected_result(
                "privacy", error_code="invalid_public", http_status=400, unknown=True,
                message="The retained visibility must be true or false.",
            )
        else:
            try:
                current = self.playlists.validate_user_can_edit(
                    playlist_id, user.spotify_id, skip_cache=True
                )
            except Exception as error:
                self._propagate_control(error)
                result = self.read_failure(error, inspection=True)
            else:
                resource = self._resource(playlist_id, current, desired_public)
                actual = current.get("public")
                matches = type(actual) is bool and type(desired_public) is bool and actual is desired_public
                code = None if matches else (
                    "intent_unavailable" if desired_public is None else "verification_failed"
                )
                resource.update(
                    status="not_required" if matches else "unknown",
                    affected_count=0 if matches else None, error_code=code,
                )
                result = self._result(
                    resource, error_code=code, public=actual if matches else None,
                    message=(
                        "Current visibility matches the retained request."
                        if matches else "Current visibility does not establish the retained request's outcome."
                    ),
                )
        result["inspection_only"] = True
        return result
