"""Reviewed, exact-ID routine activation and desired-state changes."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from shuffify.enums import ActivityType, JobType, RotationMode
from shuffify.models.db import RoutineActivationReceipt, Schedule, db
from shuffify.schemas.playlist_setup_requests import is_playlist_id
from shuffify.services import signed_digest
from shuffify.services.activity_log_service import ActivityLogService
from shuffify.services.api_response import busy_error
from shuffify.services.live_review_bindings import fingerprint
from shuffify.services.playlist_lock import playlist_configuration_lock, playlist_lock
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistNotFoundError, PlaylistService
from shuffify.services.playlist_setup_service import (
    PlaylistSetupService,
    configuration_revision,
    read_state,
)
from shuffify.services.schedule_utils import normalize_timing, schedule_revision, serialize_timing
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.spotify.exceptions import SpotifyError

INTENT_PURPOSE = "shuffify.routine.activation.v1"
INTENT_AGE_S = 24 * 60 * 60
RECEIPT_RETENTION = timedelta(days=7)
LOCK_WAIT_S = 2.0
DEFINITION_FIELDS = frozenset(
    ("job_type", "target_playlist_id", "target_playlist_name", "source_playlist_ids",
     "algorithm_name", "algorithm_params", "schedule_type", "schedule_value", "timezone")
)


def _error(code, status, message, field=None, **extra):
    payload = {"success": False, "error_code": code, "message": message, **extra}
    if field is not None:
        payload["field_errors"] = [{"path": field, "message": message}]
    return payload, status


def _target_context(api, user, target_id):
    try:
        context, state = PlaylistSetupService.get_context_and_state(user, target_id, api)
        return context, state, None
    except PlaylistNotFoundError:
        return None, None, _error("playlist_not_found", 404, "That playlist doesn't exist or was deleted.")
    except PlaylistAccessError:
        return None, None, _error("access_denied", 403, "You can only set up your own or collaborative playlists.")
    except SpotifyError:
        return None, None, _error(
            "prerequisites_unavailable", 503, "Playlist settings could not be checked. Try again."
        )


def _receipt(user_id, key):
    cutoff = datetime.now(timezone.utc) - RECEIPT_RETENTION
    return RoutineActivationReceipt.query.filter_by(user_id=user_id, request_key=key).filter(
        RoutineActivationReceipt.accepted_at >= cutoff
    ).first()


def _payload_hash(user_id, target_id, request):
    return fingerprint({
        "purpose": INTENT_PURPOSE, "user_id": user_id, "target_id": target_id,
        "request_key": request["request_key"], "definition": request["definition"],
        "dependency_revision": request["dependency_revision"],
        "schedule_id": request.get("schedule_id"),
        "expected_revision": request.get("expected_revision"),
        "desired_enabled": request.get("desired_enabled"),
        "create_another": request.get("create_another", False),
    })


def _intent_payload(user_id, target_id, request):
    return {
        "user_id": user_id, "target_id": target_id,
        "request_key": request["request_key"],
        "payload_hash": _payload_hash(user_id, target_id, request),
        "dependency_revision": request["dependency_revision"],
        "schedule_id": request.get("schedule_id"),
        "expected_revision": request.get("expected_revision"),
        "desired_enabled": request.get("desired_enabled"),
        "create_another": request.get("create_another", False),
    }


def _positive_int(value, path, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise DefinitionError(path, f"Enter a whole number of at least {minimum}.")
    return value


class DefinitionError(ValueError):
    def __init__(self, path, message):
        super().__init__(message)
        self.path = path


def _validate_options(job_type, algorithm, params):
    if not isinstance(params, dict):
        raise DefinitionError("definition.algorithm_params", "Routine options must be an object.")
    if params.get("force"):
        raise DefinitionError("definition.algorithm_params.force", "Scheduled Drip needs its saved enabled setting.")
    if job_type in (JobType.SHUFFLE, JobType.RAID_AND_SHUFFLE):
        if algorithm not in ShuffleRegistry.get_available_algorithms():
            raise DefinitionError("definition.algorithm_name", "Choose an available shuffle algorithm.")
        permitted = ShuffleRegistry.get_algorithm(algorithm)().parameters
        for key, value in params.items():
            path = f"definition.algorithm_params.{key}"
            rule = permitted.get(key)
            if rule is None:
                raise DefinitionError(path, "This shuffle option is unavailable for the selected algorithm.")
            if rule["type"] == "integer":
                _positive_int(value, path, minimum=rule.get("min", 0))
                if "max" in rule and value > rule["max"]:
                    raise DefinitionError(path, f"Enter a value no greater than {rule['max']}.")
            elif rule["type"] == "float":
                if type(value) not in (int, float) or not rule.get("min", float("-inf")) <= value <= rule.get(
                    "max", float("inf")
                ):
                    raise DefinitionError(path, "Enter a percentage in the allowed range.")
            elif rule["type"] == "string" and value not in rule.get("options", ()):
                raise DefinitionError(path, "Choose an available option.")
    elif algorithm is not None:
        raise DefinitionError("definition.algorithm_name", "This routine does not use a shuffle algorithm.")
    if job_type == JobType.ROTATE:
        unknown = set(params) - {"rotation_mode", "rotation_count", "target_size", "protect_count"}
        if unknown:
            raise DefinitionError(f"definition.algorithm_params.{sorted(unknown)[0]}", "Unknown rotation option.")
        if params.get("rotation_mode", RotationMode.SWAP) not in tuple(RotationMode):
            raise DefinitionError("definition.algorithm_params.rotation_mode", "Choose an available rotation mode.")
        _positive_int(params.get("target_size"), "definition.algorithm_params.target_size", minimum=1)
        for key, minimum in (("rotation_count", 1), ("protect_count", 0)):
            if key in params:
                _positive_int(params[key], f"definition.algorithm_params.{key}", minimum=minimum)
    if job_type in (JobType.DRIP, JobType.RAID_AND_DRIP) and "drip_count" in params:
        _positive_int(params["drip_count"], "definition.algorithm_params.drip_count", minimum=1)
    if job_type in (JobType.DRIP, JobType.RAID_AND_DRIP):
        unknown = set(params) - {"drip_count"}
        if unknown:
            raise DefinitionError(f"definition.algorithm_params.{sorted(unknown)[0]}", "Unknown Drip option.")
    if job_type == JobType.RAID and params:
        raise DefinitionError("definition.algorithm_params", "Collection uses its saved source settings.")


def _editable_asset(api, user, playlist_id, cache):
    """One fresh lean verdict per distinct asset within this request/lock."""
    if playlist_id not in cache:
        try:
            PlaylistService(api).validate_user_can_edit(playlist_id, user.spotify_id, lean=True)
            cache[playlist_id] = True
        except (PlaylistAccessError, SpotifyError) as error:
            cache[playlist_id] = error
    result = cache[playlist_id]
    if isinstance(result, Exception):
        raise result


def _validate_dependencies(api, user, target_id, state, job_type, *, extra_source_ids=(), editable_cache=None):
    cache = editable_cache if editable_cache is not None else {}
    if job_type in (JobType.RAID, JobType.RAID_AND_SHUFFLE, JobType.RAID_AND_DRIP):
        if not state.sources and not extra_source_ids:
            raise DefinitionError("definition.job_type", "Save at least one source before scheduling collection.")
        if not isinstance(extra_source_ids, list) or any(not is_playlist_id(value) for value in extra_source_ids):
            raise DefinitionError("definition.source_playlist_ids", "A legacy routine source ID is invalid.")
        for source in state.sources:
            if source.source_type == "search_query":
                valid = bool(source.search_query and source.search_query.strip())
            else:
                valid = bool(source.source_playlist_id and source.source_playlist_id.strip())
            if not valid:
                raise DefinitionError("definition.job_type", "A saved source is incomplete. Update sources first.")
        if state.link is not None:
            if state.link.raid_playlist_id == target_id:
                raise DefinitionError("definition.job_type", "The collection must be distinct from the target.")
            try:
                _editable_asset(api, user, state.link.raid_playlist_id, cache)
            except PlaylistAccessError as error:
                raise DefinitionError("definition.job_type", "The linked collection cannot be edited.") from error
    if job_type in (JobType.DRIP, JobType.RAID_AND_DRIP):
        link = state.link
        if link is None or link.raid_playlist_id == target_id:
            raise DefinitionError("definition.job_type", "Link a distinct collection before scheduling Drip.")
        if not link.drip_enabled:
            raise DefinitionError("definition.job_type", "Enable Drip on the linked collection first.")
        try:
            _editable_asset(api, user, link.raid_playlist_id, cache)
        except PlaylistAccessError as error:
            raise DefinitionError("definition.job_type", "The linked collection cannot be edited.") from error
    if job_type == JobType.ROTATE:
        pair = state.pair
        if pair is None or pair.archive_playlist_id == target_id:
            raise DefinitionError("definition.job_type", "Pair a distinct archive before scheduling rotation.")
        try:
            _editable_asset(api, user, pair.archive_playlist_id, cache)
        except PlaylistAccessError as error:
            raise DefinitionError("definition.job_type", "The paired archive cannot be edited.") from error


def _stored(schedule):
    return {
        "job_type": schedule.job_type,
        "target_playlist_id": schedule.target_playlist_id,
        "target_playlist_name": schedule.target_playlist_name,
        "source_playlist_ids": schedule.source_playlist_ids or [],
        "algorithm_name": schedule.algorithm_name,
        "algorithm_params": schedule.algorithm_params or {},
        "schedule_type": schedule.schedule_type,
        "schedule_value": schedule.schedule_value,
        "timezone": schedule.timezone,
    }


def _normalize(api, user, target_id, supplied, existing, state, target_name):
    if not isinstance(supplied, dict):
        raise DefinitionError("definition", "Routine definition must be an object.")
    if set(supplied) - {"job_type", "algorithm_name", "algorithm_params", "timing"}:
        raise DefinitionError("definition", "The routine definition has unsupported fields.")
    base = _stored(existing) if existing else {}
    job_type = supplied.get("job_type", base.get("job_type"))
    if job_type not in tuple(JobType):
        raise DefinitionError("definition.job_type", "Choose an available routine operation.")
    algorithm = supplied.get("algorithm_name", base.get("algorithm_name"))
    params = supplied.get("algorithm_params", base.get("algorithm_params", {}))
    if params is None:
        params = {}
    _validate_options(job_type, algorithm, params)
    _validate_dependencies(
        api, user, target_id, state, job_type,
        extra_source_ids=base.get("source_playlist_ids", []), editable_cache={},
    )
    timing = supplied.get("timing")
    if timing is not None:
        if not isinstance(timing, dict):
            raise DefinitionError("definition.timing", "Timing must be an object.")
        fields = {
            "interval": {"kind", "interval", "timezone"},
            "daily": {"kind", "time", "timezone"},
            "weekly": {"kind", "time", "weekday", "timezone"},
            "advanced": {"kind", "expression", "timezone"},
        }.get(timing.get("kind"))
        if fields is None:
            raise DefinitionError("definition.timing.kind", "Choose a timing kind.")
        unknown = set(timing) - fields
        if unknown:
            raise DefinitionError(f"definition.timing.{sorted(unknown)[0]}", "Unknown timing field.")
        try:
            timing_fields = normalize_timing(timing)
        except (TypeError, ValueError) as error:
            raise DefinitionError("definition.timing", str(error)) from error
    elif existing:
        timing_fields = {key: base[key] for key in ("schedule_type", "schedule_value", "timezone")}
    else:
        raise DefinitionError("definition.timing", "Choose when this routine should run.")
    return {
        "job_type": job_type,
        "target_playlist_id": target_id,
        "target_playlist_name": target_name[:255],
        # The executor reads all current saved sources, including search-only
        # rows, plus legacy routine-only IDs. Edits preserve those extras;
        # new routines cannot submit a source subset.
        "source_playlist_ids": base.get("source_playlist_ids", []),
        "algorithm_name": algorithm,
        "algorithm_params": params,
        **timing_fields,
    }


def _validate_submitted(definition, target_id):
    if not isinstance(definition, dict) or set(definition) != DEFINITION_FIELDS:
        raise DefinitionError("definition", "Use the complete normalized definition from review.")
    if definition["target_playlist_id"] != target_id:
        raise DefinitionError("definition.target_playlist_id", "The reviewed target changed.")
    sources = definition["source_playlist_ids"]
    if not isinstance(sources, list) or any(not is_playlist_id(source_id) for source_id in sources):
        raise DefinitionError("definition.source_playlist_ids", "Use the reviewed legacy source IDs.")


def _schedule_payload(schedule):
    from shuffify.scheduler import get_schedule_registration

    registration = get_schedule_registration(schedule)
    if registration["status"] == "registered" and schedule.is_enabled:
        state = "Active"
    elif registration["status"] == "paused" and not schedule.is_enabled:
        state = "Paused"
    else:
        state = "Saved—registration needs attention"
    return {
        "schedule_id": schedule.id, "schedule": schedule.to_dict(),
        "definition": _stored(schedule), "schedule_revision": schedule_revision(schedule),
        "timing": serialize_timing(schedule), "registration": registration, "state": state,
    }


def _accepted_locked(user_id, target_id, receipt, *, replayed):
    """Sync and report the current exact row while its target lock is held."""
    from shuffify.scheduler import sync_schedule_registration

    db.session.expire_all()
    schedule = Schedule.query.filter_by(id=receipt.schedule_id, user_id=user_id).first()
    same_incarnation = schedule is not None and schedule.created_at == receipt.schedule_created_at
    if same_incarnation:
        sync_schedule_registration(schedule)
        db.session.expire_all()
        schedule = Schedule.query.filter_by(id=receipt.schedule_id, user_id=user_id).first()
        same_incarnation = schedule is not None and schedule.created_at == receipt.schedule_created_at
    revision = configuration_revision(read_state(user_id, target_id))
    if not same_incarnation:
        return {
            "success": True, "accepted": True, "replayed": replayed,
            "schedule_id": receipt.schedule_id, "accepted_at": receipt.accepted_at.isoformat(),
            "dependency_revision": revision, "state": "Deleted", "schedule": None,
        }, 200
    return {
        "success": True, "accepted": True, "replayed": replayed,
        "accepted_at": receipt.accepted_at.isoformat(), "dependency_revision": revision,
        **_schedule_payload(schedule),
    }, 200


class RoutineActivationService:
    @staticmethod
    def context(api, user, target_id):
        setup, state, error = _target_context(api, user, target_id)
        if error:
            return error
        capabilities = {}
        editable_cache = {}
        for job_type in JobType:
            try:
                _validate_dependencies(api, user, target_id, state, job_type, editable_cache=editable_cache)
                capabilities[job_type] = {"available": True, "reason": None}
            except DefinitionError as exc:
                capabilities[job_type] = {"available": False, "reason": str(exc)}
            except SpotifyError:
                return _error("prerequisites_unavailable", 503, "Playlist settings could not be checked. Try again.")
        return {
            "success": True, "target": setup["target"], "prerequisites": setup,
            "dependency_revision": configuration_revision(state),
            "routines": [_schedule_payload(row) for row in state.schedules],
            "algorithms": ShuffleRegistry.list_algorithms(), "capabilities": capabilities,
        }, 200

    @staticmethod
    def review(api, user, target_id, request):
        setup, state, error = _target_context(api, user, target_id)
        if error:
            return error
        schedule_id = request.get("schedule_id")
        existing = next((row for row in state.schedules if row.id == schedule_id), None)
        if schedule_id is not None and existing is None:
            return _error("routine_not_found", 404, "This exact routine is unavailable.", "schedule_id")
        if existing:
            if request.get("expected_revision") != schedule_revision(existing):
                return _error("routine_changed", 409, "This routine changed. Review it again.", "expected_revision")
        elif request.get("expected_revision") is not None:
            return _error("invalid_definition", 400, "A new routine has no expected revision.", "expected_revision")
        if not existing and request.get("desired_enabled") is False:
            return _error("invalid_definition", 400, "Activate a new routine as enabled.", "desired_enabled")
        try:
            definition = _normalize(
                api, user, target_id, request["definition"], existing, state, setup["target"]["name"]
            )
        except DefinitionError as exc:
            return _error("invalid_definition", 422, str(exc), exc.path)
        except SpotifyError:
            return _error("prerequisites_unavailable", 503, "Playlist settings could not be checked. Try again.")
        revision = configuration_revision(state)
        reviewed = {**request, "definition": definition, "dependency_revision": revision}
        binding = _intent_payload(user.id, target_id, reviewed)
        saved_playlist_ids = {source.source_playlist_id for source in state.sources if source.source_playlist_id}
        routine_only_ids = [
            playlist_id for playlist_id in definition["source_playlist_ids"]
            if playlist_id not in saved_playlist_ids
        ]
        candidates = (
            [row.id for row in state.schedules if row.job_type == definition["job_type"]]
            if not existing else []
        )
        return {
            "success": True, "definition": definition,
            "resource_scope": {
                "target_id": target_id, "source_ids": [source.id for source in state.sources],
                "routine_only_source_playlist_ids": routine_only_ids,
                "collection_id": state.link.raid_playlist_id if state.link else None,
                "archive_id": state.pair.archive_playlist_id if state.pair else None,
            },
            "dependency_revision": revision,
            "schedule_revision": schedule_revision(existing) if existing else None,
            "candidate_schedule_ids": candidates,
            "warnings": (["Choose an exact routine or Create another routine before activation."]
                         if candidates and not request.get("create_another") else []),
            "intent": signed_digest.issue(INTENT_PURPOSE, binding),
        }, 200

    @staticmethod
    def activate(api, user, target_id, request):
        try:
            _validate_submitted(request["definition"], target_id)
        except DefinitionError as exc:
            return _error("invalid_definition", 422, str(exc), exc.path)
        payload_hash = _payload_hash(user.id, target_id, request)
        with ExitStack() as locks:
            # Existing routine definitions may be read by a running executor.
            # Acquire execution first, then configuration, so those edits
            # cannot race the executor or invert the setup lock order.
            if request.get("schedule_id") is not None:
                if not locks.enter_context(playlist_lock(target_id, timeout_s=LOCK_WAIT_S)):
                    return busy_error()
            if not locks.enter_context(playlist_configuration_lock(target_id, timeout_s=LOCK_WAIT_S)):
                return busy_error()
            return RoutineActivationService._activate_locked(api, user, target_id, request, payload_hash)

    @staticmethod
    def _activate_locked(api, user, target_id, request, payload_hash):
        receipt = _receipt(user.id, request["request_key"])
        if receipt:
            if receipt.payload_hash != payload_hash:
                return _error(
                    "request_key_conflict", 409, "This request key belongs to different changes.", "request_key"
                )
            return _accepted_locked(user.id, target_id, receipt, replayed=True)
        binding = _intent_payload(user.id, target_id, request)
        reason = signed_digest.verify(INTENT_PURPOSE, binding, request["intent"], max_age_s=INTENT_AGE_S)
        if reason:
            code = "intent_expired" if reason == "expired" else "invalid_intent"
            return _error(code, 409, "This activation review expired. Review it again." if reason == "expired" else
                          "These are not the reviewed changes. Review them again.", "intent")
        setup, state, error = _target_context(api, user, target_id)
        if error:
            return error
        schedule_id = request.get("schedule_id")
        existing = next((row for row in state.schedules if row.id == schedule_id), None)
        if schedule_id is not None and existing is None:
            return _error("routine_not_found", 404, "This exact routine is unavailable.", "schedule_id")
        if existing and schedule_revision(existing) != request.get("expected_revision"):
            return _error("routine_changed", 409, "This routine changed. Review it again.", "expected_revision")
        if configuration_revision(state) != request["dependency_revision"]:
            return _error("prerequisites_changed", 409, "Playlist settings changed. Review the routine again.",
                          "dependency_revision")
        if not existing and request.get("desired_enabled") is False:
            return _error("invalid_definition", 400, "Activate a new routine as enabled.", "desired_enabled")
        try:
            definition = request["definition"]
            _validate_options(definition["job_type"], definition["algorithm_name"], definition["algorithm_params"])
            expected_sources = _stored(existing)["source_playlist_ids"] if existing else []
            if definition["source_playlist_ids"] != expected_sources:
                raise DefinitionError("definition.source_playlist_ids", "Routine sources changed. Review it again.")
            _validate_dependencies(
                api, user, target_id, state, definition["job_type"],
                extra_source_ids=expected_sources, editable_cache={},
            )
            if definition["target_playlist_name"] != setup["target"]["name"][:255]:
                raise DefinitionError("definition.target_playlist_name", "The playlist name changed. Review again.")
            if existing:
                # The reviewed complete merged definition is checked against
                # its exact row, not merged against a newer row on replay.
                pass
            else:
                candidates = [row.id for row in state.schedules if row.job_type == definition["job_type"]]
                if candidates and not request.get("create_another", False):
                    return _error("existing_candidates", 409, "Choose an exact routine or Create another routine.",
                                  "create_another", candidate_schedule_ids=candidates)
        except DefinitionError as exc:
            return _error("invalid_definition", 422, str(exc), exc.path)
        except SpotifyError:
            return _error("prerequisites_unavailable", 503, "Playlist settings could not be checked. Try again.")
        try:
            schedule = existing or Schedule(user_id=user.id)
            for name, value in definition.items():
                setattr(schedule, name, value)
            schedule.is_enabled = request.get("desired_enabled") if request.get("desired_enabled") is not None else (
                existing.is_enabled if existing else True
            )
            db.session.add(schedule)
            db.session.flush()
            now = datetime.now(timezone.utc)
            # Expired receipts are ignored on read and removed only as part of
            # an accepted write. This allows a fresh deliberate review/key
            # while an expired original intent can never recreate its row.
            RoutineActivationReceipt.query.filter(
                RoutineActivationReceipt.accepted_at < now - RECEIPT_RETENTION,
            ).delete(synchronize_session=False)
            receipt = RoutineActivationReceipt(
                user_id=user.id, request_key=request["request_key"], payload_hash=payload_hash,
                accepted_at=now, schedule_id=schedule.id, schedule_created_at=schedule.created_at,
            )
            db.session.add(receipt)
            ActivityLogService.log(
                user_id=user.id,
                activity_type=ActivityType.SCHEDULE_UPDATE if existing else ActivityType.SCHEDULE_CREATE,
                description=f"{'Updated' if existing else 'Activated'} routine #{schedule.id}",
                playlist_id=target_id, playlist_name=definition["target_playlist_name"],
                metadata={"schedule_id": schedule.id, "request_key": request["request_key"]}, commit=False,
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            winner = _receipt(user.id, request["request_key"])
            if winner is None:
                return _error("activation_failed", 503, "Activation could not be saved. Try the same request key.")
            if winner.payload_hash != payload_hash:
                return _error(
                    "request_key_conflict", 409, "This request key belongs to different changes.", "request_key"
                )
            return _accepted_locked(user.id, target_id, winner, replayed=True)
        except Exception:
            db.session.rollback()
            return _error("activation_failed", 503, "Activation could not be saved. Try the same request key.")
        return _accepted_locked(user.id, target_id, receipt, replayed=False)

    @staticmethod
    def set_state(api, user, schedule_id, request):
        schedule = Schedule.query.filter_by(id=schedule_id, user_id=user.id).first()
        if schedule is None:
            return _error("routine_not_found", 404, "This exact routine is unavailable.", "schedule_id")
        target_id = schedule.target_playlist_id
        with playlist_configuration_lock(target_id, timeout_s=LOCK_WAIT_S) as acquired:
            if not acquired:
                return busy_error()
            db.session.expire_all()
            schedule = Schedule.query.filter_by(id=schedule_id, user_id=user.id).first()
            if schedule is None:
                return _error("routine_not_found", 404, "This exact routine is unavailable.", "schedule_id")
            if schedule.target_playlist_id != target_id:
                return _error("routine_changed", 409, "This routine changed. Review it again.", "expected_revision")
            if schedule_revision(schedule) != request["expected_revision"]:
                return _error("routine_changed", 409, "This routine changed. Review it again.", "expected_revision")
            if request["desired_enabled"]:
                setup, state, error = _target_context(api, user, target_id)
                if error:
                    return error
                try:
                    _validate_options(schedule.job_type, schedule.algorithm_name, schedule.algorithm_params or {})
                    _validate_dependencies(
                        api, user, target_id, state, schedule.job_type,
                        extra_source_ids=schedule.source_playlist_ids or [], editable_cache={},
                    )
                except DefinitionError as exc:
                    return _error("invalid_definition", 422, str(exc), exc.path)
                except SpotifyError:
                    return _error(
                        "prerequisites_unavailable", 503, "Playlist settings could not be checked. Try again."
                    )
            if schedule.is_enabled != request["desired_enabled"]:
                try:
                    schedule.is_enabled = request["desired_enabled"]
                    ActivityLogService.log(
                        user_id=user.id, activity_type=ActivityType.SCHEDULE_TOGGLE,
                        description=f"{'Resumed' if schedule.is_enabled else 'Paused'} routine #{schedule.id}",
                        playlist_id=target_id, metadata={"schedule_id": schedule.id}, commit=False,
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    return _error("state_failed", 503, "The routine state could not be saved.")
            from shuffify.scheduler import sync_schedule_registration

            sync_schedule_registration(schedule)
            db.session.expire_all()
            schedule = Schedule.query.filter_by(id=schedule_id, user_id=user.id).first()
            return {"success": True, **_schedule_payload(schedule)}, 200
