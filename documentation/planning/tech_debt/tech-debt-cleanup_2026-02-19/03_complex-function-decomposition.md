# Phase 3: Complex Function Decomposition

**Status**: 🔧 IN PROGRESS
**Started**: 2026-02-19
**Date**: 2026-02-19
**Codebase**: Shuffify (1081 tests, 32,574 LOC Python)
**Session**: `tech-debt-cleanup_2026-02-19`

---

## PR Metadata

| Field | Value |
|-------|-------|
| **PR Title** | Refactor: Decompose complex functions for maintainability |
| **Branch Name** | `implement/complex-function-decomposition` |
| **Risk Level** | Medium-High (changes internal structure of critical paths) |
| **Estimated Effort** | 4-5 hours |
| **Files Modified** | `shuffify/services/job_executor_service.py`, `shuffify/spotify/api.py`, `shuffify/routes/workshop.py`, `shuffify/services/raid_sync_service.py` |
| **Test Files (read, not modified)** | `tests/services/test_job_executor_service.py`, `tests/services/test_job_executor_rotate.py`, `tests/services/test_raid_sync_service.py`, `tests/spotify/test_api.py`, `tests/test_workshop.py`, `tests/test_workshop_external.py` |
| **Dependencies** | Phase 2 (service layer deduplication) should be done first |
| **Blocks** | None directly |

---

## Overview

This phase decomposes 7 overly complex functions into smaller, focused private helper methods. **No external behavior changes.** All helper methods are private (underscore-prefixed) and stay in the same class/module as the parent function. All existing tests must continue to pass without modification -- that is the correctness guarantee.

---

## Target 1: `shuffify/services/job_executor_service.py` (886 lines, 3 functions)

### 1A. Decompose `execute()` (lines 47-184)

**Current state**: The `execute()` static method at line 47 handles schedule lookup, validation, execution record creation, Spotify client setup, job dispatch, success recording with activity logging, and failure recording. It is 138 lines with a two-level try/except and multiple responsibilities.

**Decomposition plan**: Extract four private static methods from the body of `execute()`. The orchestrator (`execute()`) retains control flow and exception handling.

#### New method: `_create_execution_record(schedule_id)`

Extract lines 74-80 into a standalone method.

**Before** (inside `execute()`, lines 73-80):
```python
            # Create execution record
            execution = JobExecution(
                schedule_id=schedule_id,
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            db.session.add(execution)
            db.session.commit()
```

**After** (new static method):
```python
    @staticmethod
    def _create_execution_record(schedule_id: int) -> JobExecution:
        """Create a running execution record in the database."""
        execution = JobExecution(
            schedule_id=schedule_id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        db.session.commit()
        return execution
```

**Call site in `execute()`**: Replace lines 73-80 with:
```python
            execution = JobExecutorService._create_execution_record(schedule_id)
```

#### New method: `_record_success(execution, schedule, result)`

Extract lines 97-155 (the success path: updating execution, updating schedule, activity logging, info log).

**After** (new static method):
```python
    @staticmethod
    def _record_success(
        execution: JobExecution,
        schedule: Schedule,
        result: dict,
    ) -> None:
        """Record a successful job execution."""
        execution.status = "success"
        execution.completed_at = datetime.now(timezone.utc)
        execution.tracks_added = result.get("tracks_added", 0)
        execution.tracks_total = result.get("tracks_total", 0)

        schedule.last_run_at = datetime.now(timezone.utc)
        schedule.last_status = "success"
        schedule.last_error = None

        db.session.commit()

        # Log activity (non-blocking)
        try:
            from shuffify.services.activity_log_service import (
                ActivityLogService,
            )

            ActivityLogService.log(
                user_id=schedule.user_id,
                activity_type=ActivityType.SCHEDULE_RUN,
                description=(
                    f"Scheduled {schedule.job_type} on "
                    f"'{schedule.target_playlist_name}'"
                    f" completed"
                ),
                playlist_id=schedule.target_playlist_id,
                playlist_name=schedule.target_playlist_name,
                metadata={
                    "schedule_id": schedule.id,
                    "job_type": schedule.job_type,
                    "tracks_added": result.get("tracks_added", 0),
                    "tracks_total": result.get("tracks_total", 0),
                    "triggered_by": "scheduler",
                },
            )
        except Exception:
            pass

        logger.info(
            f"Schedule {schedule.id} executed "
            f"successfully: "
            f"added={result.get('tracks_added', 0)}, "
            f"total={result.get('tracks_total', 0)}"
        )
```

**Call site in `execute()`**: Replace lines 96-155 with:
```python
            JobExecutorService._record_success(execution, schedule, result)
```

#### New method: `_record_failure(execution, schedule, error, schedule_id)`

Extract lines 157-184 (the exception handler body).

**After** (new static method):
```python
    @staticmethod
    def _record_failure(
        execution,
        schedule,
        error: Exception,
        schedule_id: int,
    ) -> None:
        """Record a failed job execution."""
        logger.error(
            f"Schedule {schedule_id} execution "
            f"failed: {error}",
            exc_info=True,
        )
        try:
            if execution:
                execution.status = "failed"
                execution.completed_at = datetime.now(timezone.utc)
                execution.error_message = str(error)[:1000]

            if schedule:
                schedule.last_run_at = datetime.now(timezone.utc)
                schedule.last_status = "failed"
                schedule.last_error = str(error)[:1000]

            db.session.commit()
        except Exception as db_err:
            logger.error(
                f"Failed to record execution failure: "
                f"{db_err}"
            )
            db.session.rollback()
```

#### Resulting `execute()` orchestrator

The method shrinks from 138 lines to approximately 35 lines:

```python
    @staticmethod
    def execute(schedule_id: int) -> None:
        """
        Execute a scheduled job.

        This is the main entry point called by the scheduler.
        It handles all error scenarios and records the execution.
        """
        execution = None
        schedule = None

        try:
            schedule = db.session.get(Schedule, schedule_id)
            if not schedule:
                logger.error(
                    f"Schedule {schedule_id} not found, skipping"
                )
                return

            if not schedule.is_enabled:
                logger.info(
                    f"Schedule {schedule_id} is disabled, skipping"
                )
                return

            execution = JobExecutorService._create_execution_record(
                schedule_id
            )

            user = db.session.get(User, schedule.user_id)
            if not user:
                raise JobExecutionError(
                    f"User {schedule.user_id} not found"
                )

            api = JobExecutorService._get_spotify_api(user)

            result = JobExecutorService._execute_job_type(
                schedule, api
            )

            JobExecutorService._record_success(
                execution, schedule, result
            )

        except Exception as e:
            JobExecutorService._record_failure(
                execution, schedule, e, schedule_id
            )
```

---

### 1B. Decompose `_execute_raid()` (lines 341-465)

**Current state**: This 123-line method at line 341 fetches target tracks, creates an auto-snapshot, iterates source playlists to collect new URIs (deduplicating), and batch-adds them. Four distinct responsibilities are tangled together.

**Decomposition plan**: Extract three private static methods. The orchestrator dispatches and handles exceptions.

#### New method: `_fetch_raid_sources(api, source_ids, target_uris)`

Extracts lines 406-425 (the source-fetching loop with deduplication).

**After**:
```python
    @staticmethod
    def _fetch_raid_sources(
        api: SpotifyAPI,
        source_ids: list,
        target_uris: set,
    ) -> List[str]:
        """
        Fetch new tracks from source playlists not already in target.

        Returns:
            List of new track URIs (deduplicated).
        """
        new_uris: List[str] = []
        for source_id in source_ids:
            try:
                source_tracks = api.get_playlist_tracks(source_id)
                for track in source_tracks:
                    uri = track.get("uri")
                    if (
                        uri
                        and uri not in target_uris
                        and uri not in new_uris
                    ):
                        new_uris.append(uri)
            except SpotifyNotFoundError:
                logger.warning(
                    f"Source playlist {source_id} "
                    f"not found, skipping"
                )
                continue
        return new_uris
```

#### New method: `_auto_snapshot_before_raid(schedule, target_tracks)`

Extracts lines 370-403 (the auto-snapshot block before raid).

**After**:
```python
    @staticmethod
    def _auto_snapshot_before_raid(
        schedule: Schedule,
        target_tracks: list,
    ) -> None:
        """Create an auto-snapshot before a scheduled raid if enabled."""
        try:
            pre_raid_uris = [
                t.get("uri")
                for t in target_tracks
                if t.get("uri")
            ]
            if (
                pre_raid_uris
                and PlaylistSnapshotService.is_auto_snapshot_enabled(
                    schedule.user_id
                )
            ):
                PlaylistSnapshotService.create_snapshot(
                    user_id=schedule.user_id,
                    playlist_id=schedule.target_playlist_id,
                    playlist_name=(
                        schedule.target_playlist_name
                        or schedule.target_playlist_id
                    ),
                    track_uris=pre_raid_uris,
                    snapshot_type=SnapshotType.AUTO_PRE_RAID,
                    trigger_description="Before scheduled raid",
                )
        except Exception as snap_err:
            logger.warning(
                "Auto-snapshot before scheduled "
                f"raid failed: {snap_err}"
            )
```

#### New method: `_batch_add_tracks(api, playlist_id, uris)`

Extracts lines 437-442 (batch add loop). This pattern also appears in `_rotate_archive`, `_rotate_refresh`, `_rotate_swap`, and `raid_now`, so centralizing it is valuable.

**After**:
```python
    @staticmethod
    def _batch_add_tracks(
        api: SpotifyAPI,
        playlist_id: str,
        uris: List[str],
        batch_size: int = 100,
    ) -> None:
        """Add tracks to a playlist in batches."""
        for i in range(0, len(uris), batch_size):
            batch = uris[i: i + batch_size]
            api._ensure_valid_token()
            api._sp.playlist_add_items(playlist_id, batch)
```

#### Resulting `_execute_raid()` orchestrator

```python
    @staticmethod
    def _execute_raid(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """Pull new tracks from source playlists into the target."""
        target_id = schedule.target_playlist_id
        source_ids = schedule.source_playlist_ids or []

        if not source_ids:
            logger.info(
                f"Schedule {schedule.id}: no source playlists "
                f"configured, skipping raid"
            )
            target_tracks = api.get_playlist_tracks(target_id)
            return {
                "tracks_added": 0,
                "tracks_total": len(target_tracks),
            }

        try:
            target_tracks = api.get_playlist_tracks(target_id)
            target_uris = {
                t.get("uri")
                for t in target_tracks
                if t.get("uri")
            }

            JobExecutorService._auto_snapshot_before_raid(
                schedule, target_tracks
            )

            new_uris = JobExecutorService._fetch_raid_sources(
                api, source_ids, target_uris
            )

            if not new_uris:
                logger.info(
                    f"Schedule {schedule.id}: no new tracks to add"
                )
                return {
                    "tracks_added": 0,
                    "tracks_total": len(target_tracks),
                }

            JobExecutorService._batch_add_tracks(
                api, target_id, new_uris
            )

            total = len(target_tracks) + len(new_uris)
            logger.info(
                f"Schedule {schedule.id}: added "
                f"{len(new_uris)} tracks to "
                f"{schedule.target_playlist_name} "
                f"(total: {total})"
            )

            return {
                "tracks_added": len(new_uris),
                "tracks_total": total,
            }

        except SpotifyNotFoundError:
            raise JobExecutionError(
                f"Target playlist {target_id} not found. "
                f"It may have been deleted."
            )
        except SpotifyAPIError as e:
            raise JobExecutionError(
                f"Spotify API error during raid: {e}"
            )
```

---

### 1C. Decompose `_execute_rotate()` (lines 580-732)

**Current state**: This 151-line method at line 580 validates rotation config, fetches production tracks, creates auto-snapshot, computes the count, and dispatches to one of three mode-specific methods. The three mode methods (`_rotate_archive`, `_rotate_refresh`, `_rotate_swap`) are already extracted, which is good. The remaining orchestrator still mixes validation, snapshot, and dispatch.

**Decomposition plan**: Extract two private static methods -- validation and auto-snapshot. The mode-specific methods stay as-is.

#### New method: `_validate_rotation_config(schedule)`

Extracts lines 596-626 (import, param extraction, validation, pair lookup).

**After**:
```python
    @staticmethod
    def _validate_rotation_config(schedule: Schedule) -> tuple:
        """
        Extract and validate rotation parameters from schedule.

        Returns:
            Tuple of (rotation_mode, rotation_count, pair).

        Raises:
            JobExecutionError: If mode is invalid or no pair found.
        """
        from shuffify.services.playlist_pair_service import (
            PlaylistPairService,
        )

        params = schedule.algorithm_params or {}
        rotation_mode = params.get(
            "rotation_mode", RotationMode.ARCHIVE_OLDEST
        )
        rotation_count = max(
            1, int(params.get("rotation_count", 5))
        )

        valid_modes = set(RotationMode)
        if rotation_mode not in valid_modes:
            raise JobExecutionError(
                "Invalid rotation_mode: "
                "{}".format(rotation_mode)
            )

        pair = PlaylistPairService.get_pair_for_playlist(
            user_id=schedule.user_id,
            production_playlist_id=schedule.target_playlist_id,
        )
        if not pair:
            raise JobExecutionError(
                "No archive pair found for playlist "
                "{}. Create a pair in the workshop "
                "first.".format(schedule.target_playlist_id)
            )

        return rotation_mode, rotation_count, pair
```

#### New method: `_auto_snapshot_before_rotate(schedule, prod_uris, rotation_mode)`

Extracts lines 645-676 (the auto-snapshot block before rotation).

**After**:
```python
    @staticmethod
    def _auto_snapshot_before_rotate(
        schedule: Schedule,
        prod_uris: list,
        rotation_mode: str,
    ) -> None:
        """Create an auto-snapshot before rotation if enabled."""
        try:
            if (
                prod_uris
                and PlaylistSnapshotService.is_auto_snapshot_enabled(
                    schedule.user_id
                )
            ):
                PlaylistSnapshotService.create_snapshot(
                    user_id=schedule.user_id,
                    playlist_id=schedule.target_playlist_id,
                    playlist_name=(
                        schedule.target_playlist_name
                        or schedule.target_playlist_id
                    ),
                    track_uris=prod_uris,
                    snapshot_type=SnapshotType.AUTO_PRE_ROTATE,
                    trigger_description=(
                        "Before scheduled "
                        "{} rotation".format(rotation_mode)
                    ),
                )
        except Exception as snap_err:
            logger.warning(
                "Auto-snapshot before rotation "
                "failed: %s", snap_err
            )
```

#### Resulting `_execute_rotate()` orchestrator

```python
    @staticmethod
    def _execute_rotate(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """
        Rotate tracks between production and archive playlists.
        """
        target_id = schedule.target_playlist_id
        rotation_mode, rotation_count, pair = (
            JobExecutorService._validate_rotation_config(schedule)
        )
        archive_id = pair.archive_playlist_id

        try:
            prod_tracks = api.get_playlist_tracks(target_id)
            if not prod_tracks:
                return {"tracks_added": 0, "tracks_total": 0}

            prod_uris = [
                t["uri"] for t in prod_tracks if t.get("uri")
            ]

            JobExecutorService._auto_snapshot_before_rotate(
                schedule, prod_uris, rotation_mode
            )

            actual_count = min(rotation_count, len(prod_uris))
            if actual_count == 0:
                return {
                    "tracks_added": 0,
                    "tracks_total": len(prod_uris),
                }

            oldest_uris = prod_uris[:actual_count]

            if rotation_mode == RotationMode.ARCHIVE_OLDEST:
                return JobExecutorService._rotate_archive(
                    api, schedule, target_id, archive_id,
                    oldest_uris, prod_uris, actual_count,
                )
            elif rotation_mode == RotationMode.REFRESH:
                return JobExecutorService._rotate_refresh(
                    api, schedule, target_id, archive_id,
                    prod_uris, actual_count,
                )
            elif rotation_mode == RotationMode.SWAP:
                return JobExecutorService._rotate_swap(
                    api, schedule, target_id, archive_id,
                    oldest_uris, prod_uris, actual_count,
                )
            else:
                raise JobExecutionError(
                    "Unknown rotation mode: "
                    "{}".format(rotation_mode)
                )

        except JobExecutionError:
            raise
        except SpotifyNotFoundError:
            raise JobExecutionError(
                "Playlist not found during rotation. "
                "Target: {}, Archive: {}".format(
                    target_id, archive_id
                )
            )
        except SpotifyAPIError as e:
            raise JobExecutionError(
                "Spotify API error during rotation: {}".format(e)
            )
```

#### Also update `_rotate_archive`, `_rotate_refresh`, `_rotate_swap` to use `_batch_add_tracks`

Replace the inline batch loops in these three methods with calls to the new `_batch_add_tracks` helper. For example, in `_rotate_archive` (lines 740-745), replace:

```python
        api._ensure_valid_token()
        for i in range(0, len(oldest_uris), 100):
            batch = oldest_uris[i:i + 100]
            api._sp.playlist_add_items(
                archive_id, batch
            )
```

With:

```python
        JobExecutorService._batch_add_tracks(
            api, archive_id, oldest_uris
        )
```

Apply the same substitution in `_rotate_refresh` (lines 796-802) and `_rotate_swap` (lines 847-854, 859-865).

---

## Target 2: `shuffify/spotify/api.py` (lines 56-142)

### 2A. Decompose `api_error_handler()` decorator

**Current state**: The `api_error_handler` decorator at line 56 is an 87-line function with 5 exception handlers, each containing conditional retry logic. The branching on HTTP status codes (404, 401, 429, 5xx) is nested inside a for-loop, making it hard to reason about which errors retry and which don't.

**Decomposition plan**: Extract three module-level private helper functions used by the decorator.

#### New function: `_classify_error(exception)`

Returns a string category for the exception.

**After** (placed above `api_error_handler`, around line 55):
```python
def _classify_error(exception: Exception) -> str:
    """
    Classify an exception into an error category.

    Returns one of: 'not_found', 'token_expired', 'rate_limited',
    'server_error', 'network_error', 'client_error', 'unexpected'.
    """
    if isinstance(exception, spotipy.SpotifyException):
        if exception.http_status == 404:
            return "not_found"
        elif exception.http_status == 401:
            return "token_expired"
        elif exception.http_status == 429:
            return "rate_limited"
        elif exception.http_status in (500, 502, 503, 504):
            return "server_error"
        else:
            return "client_error"
    elif isinstance(exception, (ConnectionError, Timeout, RequestException)):
        return "network_error"
    else:
        return "unexpected"
```

#### New function: `_should_retry(error_category)`

Returns whether the error category is retryable.

**After**:
```python
def _should_retry(error_category: str) -> bool:
    """Determine if an error category is retryable."""
    return error_category in ("rate_limited", "server_error", "network_error")
```

#### New function: `_get_retry_delay(exception, error_category, attempt)`

Computes the appropriate delay.

**After**:
```python
def _get_retry_delay(
    exception: Exception, error_category: str, attempt: int
) -> float:
    """
    Calculate retry delay based on error type and attempt number.

    For rate limits, respects the Retry-After header.
    For other retryable errors, uses exponential backoff.
    """
    if error_category == "rate_limited" and isinstance(exception, spotipy.SpotifyException):
        retry_after = exception.headers.get("Retry-After", 60) if exception.headers else 60
        return max(int(retry_after), _calculate_backoff_delay(attempt))
    return _calculate_backoff_delay(attempt)
```

#### New function: `_raise_final_error(exception, error_category, func_name)`

Converts the exception to the appropriate custom exception type for raising.

**After**:
```python
def _raise_final_error(
    exception: Exception, error_category: str, func_name: str
) -> None:
    """
    Raise the appropriate custom exception after retries are exhausted
    or for non-retryable errors.
    """
    if error_category == "not_found":
        raise SpotifyNotFoundError(f"Resource not found: {exception}")
    elif error_category == "token_expired":
        raise SpotifyTokenExpiredError(f"Token expired or invalid: {exception}")
    elif error_category == "rate_limited":
        retry_after = 60
        if isinstance(exception, spotipy.SpotifyException) and exception.headers:
            retry_after = int(exception.headers.get("Retry-After", 60))
        raise SpotifyRateLimitError(
            f"Rate limited after {MAX_RETRIES + 1} attempts: {exception}",
            retry_after=retry_after,
        )
    elif error_category in ("server_error", "network_error"):
        logger.error(
            f"{error_category} in {func_name} after {MAX_RETRIES + 1} attempts: {exception}",
            exc_info=True,
        )
        raise SpotifyAPIError(f"API error after retries: {exception}")
    elif error_category == "client_error":
        logger.error(f"Spotify API error in {func_name}: {exception}")
        raise SpotifyAPIError(f"API error: {exception}")
    else:
        logger.error(f"Unexpected error in {func_name}: {exception}", exc_info=True)
        raise SpotifyAPIError(f"Unexpected error: {exception}")
```

#### Resulting `api_error_handler()` decorator

The decorator becomes a clean retry loop:

```python
def api_error_handler(func: Callable) -> Callable:
    """
    Decorator for handling Spotify API errors with automatic retry.

    Catches spotipy exceptions and converts them to our exception types.
    Implements exponential backoff for rate limits and transient errors.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exception = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                category = _classify_error(e)

                if not _should_retry(category) or attempt >= MAX_RETRIES:
                    _raise_final_error(e, category, func.__name__)

                delay = _get_retry_delay(e, category, attempt)
                logger.warning(
                    f"{category} in {func.__name__}, "
                    f"attempt {attempt + 1}/{MAX_RETRIES + 1}. "
                    f"Retrying in {delay}s"
                )
                time.sleep(delay)

        # Should not reach here, but handle it just in case
        if last_exception:
            raise SpotifyAPIError(
                f"Failed after {MAX_RETRIES + 1} attempts: {last_exception}"
            )

    return wrapper
```

**CRITICAL**: The `spotipy` import must be available at module level (it already is at line 17). The `_classify_error` function needs `spotipy.SpotifyException` and the `requests.exceptions` classes (already imported at line 15).

---

## Target 3: `shuffify/routes/workshop.py` (lines 365-481 and 135-249)

### 3A. Decompose `workshop_load_external_playlist()` (lines 365-481)

**Current state**: This 113-line route function at line 365 handles two distinct modes: URL-based playlist loading (lines 384-449) and query-based playlist search (lines 452-477). These two code paths share only the auth check and Pydantic validation at the top.

**Decomposition plan**: Extract two private module-level functions (not class methods, since routes are module-level).

#### New function: `_load_playlist_by_url(client, ext_request)`

Extracts lines 384-449.

**After** (placed above the route definition, around line 363):
```python
def _load_playlist_by_url(client, ext_request):
    """
    Load tracks from a specific playlist by URL/URI/ID.

    Returns a JSON response tuple.
    """
    playlist_id = parse_spotify_playlist_url(ext_request.url)
    if not playlist_id:
        return json_error(
            "Could not parse a playlist ID from the "
            "provided URL. Please use a Spotify "
            "playlist URL, URI, or ID.",
            400,
        )

    try:
        playlist_service = PlaylistService(client)
        playlist = playlist_service.get_playlist(
            playlist_id, include_features=False
        )

        if "external_playlist_history" not in session:
            session["external_playlist_history"] = []

        history = session["external_playlist_history"]
        entry = {
            "id": playlist.id,
            "name": playlist.name,
            "owner_id": playlist.owner_id,
            "track_count": len(playlist),
        }
        history = [
            h for h in history if h["id"] != playlist.id
        ]
        history.insert(0, entry)
        session["external_playlist_history"] = history[:10]
        session.modified = True

        logger.info(
            f"Loaded external playlist "
            f"'{playlist.name}' "
            f"({len(playlist)} tracks)"
        )

        return jsonify({
            "success": True,
            "mode": "tracks",
            "playlist": {
                "id": playlist.id,
                "name": playlist.name,
                "owner_id": playlist.owner_id,
                "description": playlist.description,
                "track_count": len(playlist),
            },
            "tracks": playlist.tracks,
        })

    except PlaylistError as e:
        logger.error(
            f"Failed to load external playlist: {e}"
        )
        return json_error(
            "Could not load playlist. "
            "It may be private or deleted.",
            404,
        )
```

#### New function: `_search_playlists_by_query(client, ext_request)`

Extracts lines 452-477.

**After**:
```python
def _search_playlists_by_query(client, ext_request):
    """
    Search for playlists by query string.

    Returns a JSON response tuple.
    """
    try:
        results = client.search_playlists(
            ext_request.query, limit=10
        )

        logger.info(
            f"External playlist search for "
            f"'{ext_request.query}' "
            f"returned {len(results)} results"
        )

        return jsonify({
            "success": True,
            "mode": "search",
            "playlists": results,
        })

    except Exception as e:
        logger.error(
            f"Playlist search failed: {e}",
            exc_info=True,
        )
        return json_error(
            "Search failed. Please try again.", 500
        )
```

#### Resulting `workshop_load_external_playlist()` route

```python
@main.route(
    "/workshop/load-external-playlist", methods=["POST"]
)
def workshop_load_external_playlist():
    """Load tracks from an external playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    try:
        ext_request = ExternalPlaylistRequest(**data)
    except Exception as e:
        return json_error(str(e), 400)

    if ext_request.url:
        return _load_playlist_by_url(client, ext_request)

    if ext_request.query:
        return _search_playlists_by_query(client, ext_request)

    return json_error(
        "Either 'url' or 'query' must be provided.", 400
    )
```

This reduces the route from 113 lines to 22 lines.

---

### 3B. Decompose `workshop_commit()` (lines 135-249)

**Current state**: This 111-line route at line 135 handles validation, auto-snapshot, Spotify update, state recording, and activity logging. Four distinct responsibilities.

**Decomposition plan**: Extract two private module-level functions.

#### New function: `_auto_snapshot_before_commit(playlist_id, playlist_name, current_uris)`

Extracts lines 165-191 (the auto-snapshot block).

**After** (placed above the route definition):
```python
def _auto_snapshot_before_commit(
    playlist_id, playlist_name, current_uris
):
    """Create an auto-snapshot before a workshop commit if enabled."""
    if is_db_available():
        db_user = get_db_user()
        if (
            db_user
            and PlaylistSnapshotService.is_auto_snapshot_enabled(
                db_user.id
            )
        ):
            try:
                PlaylistSnapshotService.create_snapshot(
                    user_id=db_user.id,
                    playlist_id=playlist_id,
                    playlist_name=playlist_name,
                    track_uris=current_uris,
                    snapshot_type=SnapshotType.AUTO_PRE_COMMIT,
                    trigger_description="Before workshop commit",
                )
            except Exception as e:
                logger.warning(
                    "Auto-snapshot before commit "
                    f"failed: {e}"
                )
```

#### New function: `_log_workshop_commit_activity(playlist_id, playlist_name, track_count)`

Extracts lines 217-244 (the activity logging block).

**After**:
```python
def _log_workshop_commit_activity(
    playlist_id, playlist_name, track_count
):
    """Log a workshop commit activity (non-blocking)."""
    try:
        user_data = session.get("user_data", {})
        spotify_id = user_data.get("id")
        if spotify_id:
            db_user = UserService.get_by_spotify_id(spotify_id)
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=ActivityType.WORKSHOP_COMMIT,
                    description=(
                        f"Committed workshop changes "
                        f"to '{playlist_name}'"
                    ),
                    playlist_id=playlist_id,
                    playlist_name=playlist_name,
                    metadata={"track_count": track_count},
                )
    except Exception:
        pass
```

#### Resulting `workshop_commit()` route

```python
@main.route(
    "/workshop/<playlist_id>/commit", methods=["POST"]
)
def workshop_commit(playlist_id):
    """Save the workshop's staged track order to Spotify."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    try:
        commit_request = WorkshopCommitRequest(**data)
    except ValidationError as e:
        return json_error(
            f"Invalid request: {e.error_count()} "
            f"validation error(s).",
            400,
        )

    playlist_service = PlaylistService(client)
    playlist = playlist_service.get_playlist(
        playlist_id, include_features=False
    )
    current_uris = [track["uri"] for track in playlist.tracks]

    _auto_snapshot_before_commit(
        playlist_id, playlist.name, current_uris
    )

    StateService.ensure_playlist_initialized(
        session, playlist_id, current_uris
    )

    if not ShuffleService.shuffle_changed_order(
        current_uris, commit_request.track_uris
    ):
        return json_success(
            "No changes to save -- track order is unchanged."
        )

    playlist_service.update_playlist_tracks(
        playlist_id, commit_request.track_uris
    )

    updated_state = StateService.record_new_state(
        session, playlist_id, commit_request.track_uris
    )

    logger.info(
        f"Workshop commit for playlist {playlist_id}: "
        f"{len(commit_request.track_uris)} tracks saved"
    )

    _log_workshop_commit_activity(
        playlist_id, playlist.name, len(commit_request.track_uris)
    )

    return json_success(
        "Playlist saved to Spotify!",
        playlist_state=updated_state.to_dict(),
    )
```

This reduces the route from 111 lines to approximately 55 lines.

---

## Target 4: `shuffify/services/raid_sync_service.py` (lines 232-343)

### 4A. Decompose `raid_now()` (lines 232-343)

**Current state**: This 110-line method at line 232 has two distinct execution paths: (1) when a schedule exists, delegate to `JobExecutorService.execute_now()` (lines 276-288), and (2) when no schedule exists, execute an inline raid (lines 289-343). The inline path duplicates logic from `_execute_raid()` in job_executor_service.py.

**Decomposition plan**: Extract two private static methods.

#### New method: `_execute_raid_via_scheduler(schedule, user)`

Extracts lines 277-288.

**After**:
```python
    @staticmethod
    def _execute_raid_via_scheduler(schedule, user):
        """Execute raid through existing schedule's job executor."""
        from shuffify.services.job_executor_service import (
            JobExecutorService,
            JobExecutionError,
        )

        try:
            result = JobExecutorService.execute_now(
                schedule.id, user.id
            )
            db.session.refresh(schedule)
            return {
                "tracks_added": 0,
                "tracks_total": 0,
                "status": result.get("status", "success"),
            }
        except JobExecutionError as e:
            raise RaidSyncError(str(e))
```

#### New method: `_execute_raid_inline(user, target_playlist_id, source_playlist_ids)`

Extracts lines 290-343.

**After** (reuses `_fetch_raid_sources` and `_batch_add_tracks` from JobExecutorService to eliminate duplication):
```python
    @staticmethod
    def _execute_raid_inline(
        user, target_playlist_id, source_playlist_ids
    ):
        """Execute raid without a schedule (inline)."""
        from shuffify.services.job_executor_service import (
            JobExecutorService,
        )

        try:
            api = JobExecutorService._get_spotify_api(user)
            target_tracks = api.get_playlist_tracks(
                target_playlist_id
            )
            target_uris = {
                t.get("uri")
                for t in target_tracks
                if t.get("uri")
            }

            new_uris = JobExecutorService._fetch_raid_sources(
                api, source_playlist_ids, target_uris
            )

            if new_uris:
                JobExecutorService._batch_add_tracks(
                    api, target_playlist_id, new_uris
                )

            return {
                "tracks_added": len(new_uris),
                "tracks_total": (
                    len(target_tracks) + len(new_uris)
                ),
                "status": "success",
            }
        except RaidSyncError:
            raise
        except Exception as e:
            raise RaidSyncError(
                f"Raid execution failed: {e}"
            )
```

#### Resulting `raid_now()` orchestrator

```python
    @staticmethod
    def raid_now(
        spotify_id, target_playlist_id,
        source_playlist_ids=None,
    ):
        """
        Trigger an immediate one-off raid.

        If source_playlist_ids is None, uses all configured sources.
        """
        from shuffify.services.upstream_source_service import (
            UpstreamSourceService,
        )

        user = User.query.filter_by(
            spotify_id=spotify_id
        ).first()
        if not user:
            raise RaidSyncError("User not found")

        if source_playlist_ids is None:
            sources = UpstreamSourceService.list_sources(
                spotify_id, target_playlist_id
            )
            source_playlist_ids = [
                s.source_playlist_id for s in sources
            ]

        if not source_playlist_ids:
            raise RaidSyncError(
                "No sources configured. "
                "Watch a playlist first."
            )

        schedule = RaidSyncService._find_raid_schedule(
            user.id, target_playlist_id
        )

        if schedule:
            return RaidSyncService._execute_raid_via_scheduler(
                schedule, user
            )
        else:
            return RaidSyncService._execute_raid_inline(
                user, target_playlist_id, source_playlist_ids
            )
```

This reduces `raid_now()` from 110 lines to approximately 38 lines.

---

## Step-by-Step Implementation Instructions

### Step 1: Create the feature branch

```bash
git checkout -b implement/complex-function-decomposition
```

### Step 2: Decompose `job_executor_service.py` (largest file, most changes)

1. Open `/Users/chris/Projects/shuffify/shuffify/services/job_executor_service.py`.
2. Add the `_batch_add_tracks()` static method anywhere inside `JobExecutorService` (after line 304, before `_execute_job_type`, is a logical location).
3. Add `_create_execution_record()` static method (between `execute_now` and `_get_spotify_api`).
4. Add `_record_success()` static method.
5. Add `_record_failure()` static method.
6. Rewrite `execute()` to use these three helpers (keep the schedule lookup and validation at the top).
7. Add `_auto_snapshot_before_raid()` static method.
8. Add `_fetch_raid_sources()` static method.
9. Rewrite `_execute_raid()` to use these helpers plus `_batch_add_tracks`.
10. Add `_validate_rotation_config()` static method.
11. Add `_auto_snapshot_before_rotate()` static method.
12. Rewrite `_execute_rotate()` to use these helpers.
13. Update `_rotate_archive()`, `_rotate_refresh()`, `_rotate_swap()` to use `_batch_add_tracks()` instead of inline loops.
14. Run `flake8 shuffify/services/job_executor_service.py` -- fix any line length or import issues.
15. Run `pytest tests/services/test_job_executor_service.py tests/services/test_job_executor_rotate.py -v` -- all tests must pass.

### Step 3: Decompose `api.py`

1. Open `/Users/chris/Projects/shuffify/shuffify/spotify/api.py`.
2. Add `_classify_error()`, `_should_retry()`, `_get_retry_delay()`, and `_raise_final_error()` as module-level functions below `_calculate_backoff_delay()` (after line 53).
3. Rewrite the `api_error_handler()` decorator to use these four helpers.
4. Run `flake8 shuffify/spotify/api.py`.
5. Run `pytest tests/spotify/test_api.py tests/spotify/test_api_search.py -v` -- all tests must pass.

### Step 4: Decompose `workshop.py`

1. Open `/Users/chris/Projects/shuffify/shuffify/routes/workshop.py`.
2. Add `_load_playlist_by_url()` and `_search_playlists_by_query()` as module-level functions above the `workshop_load_external_playlist` route definition. NOTE: These functions need access to `session`, `jsonify`, `json_error`, `logger`, `PlaylistService`, `PlaylistError`, and `parse_spotify_playlist_url` -- all are already imported at the top of the module.
3. Rewrite `workshop_load_external_playlist()` to dispatch to these helpers.
4. Add `_auto_snapshot_before_commit()` and `_log_workshop_commit_activity()` as module-level functions above the `workshop_commit` route definition.
5. Rewrite `workshop_commit()` to use these helpers.
6. Run `flake8 shuffify/routes/workshop.py`.
7. Run `pytest tests/test_workshop.py tests/test_workshop_external.py -v` -- all tests must pass.

### Step 5: Decompose `raid_sync_service.py`

1. Open `/Users/chris/Projects/shuffify/shuffify/services/raid_sync_service.py`.
2. Add `_execute_raid_via_scheduler()` and `_execute_raid_inline()` as static methods inside `RaidSyncService` (after `raid_now`, before `_find_raid_schedule`).
3. Rewrite `raid_now()` to dispatch to these helpers.
4. Run `flake8 shuffify/services/raid_sync_service.py`.
5. Run `pytest tests/services/test_raid_sync_service.py -v` -- all tests must pass.

### Step 6: Full verification

```bash
flake8 shuffify/ && pytest tests/ -v && echo "Ready to push!"
```

### Step 7: Commit and push

```bash
git add shuffify/services/job_executor_service.py shuffify/spotify/api.py shuffify/routes/workshop.py shuffify/services/raid_sync_service.py
git commit -m "Refactor: Decompose complex functions for maintainability"
git push -u origin implement/complex-function-decomposition
```

### Step 8: Create PR

Use the PR title and body specified in the metadata section.

---

## Verification Checklist

| Check | Command | Expected Result |
|-------|---------|-----------------|
| Lint passes | `flake8 shuffify/` | 0 errors |
| All tests pass | `pytest tests/ -v` | 1081 passing |
| job_executor tests | `pytest tests/services/test_job_executor_service.py tests/services/test_job_executor_rotate.py -v` | All pass |
| api tests | `pytest tests/spotify/test_api.py tests/spotify/test_api_search.py -v` | All pass |
| workshop tests | `pytest tests/test_workshop.py tests/test_workshop_external.py -v` | All pass |
| raid_sync tests | `pytest tests/services/test_raid_sync_service.py -v` | All pass |
| No new files created | `git status` | Only 4 modified files |
| No public API changes | Visual review | All new methods are private (`_` prefixed) |
| No import changes | Visual review | No new external imports added |

---

## What NOT To Do

1. **DO NOT create new files.** All helpers go in the same module/class as the parent function. The entire point is decomposition without architectural changes.

2. **DO NOT change any method signatures of existing public methods.** `execute()`, `execute_now()`, `_execute_raid()`, `_execute_shuffle()`, `_execute_rotate()`, `raid_now()`, `workshop_commit()`, `workshop_load_external_playlist()`, and `api_error_handler()` must all retain their current parameters and return types.

3. **DO NOT change the behavior of any function.** This is a pure structural refactor. The before and after behavior must be identical. If a test fails, you introduced a behavior change -- revert and try again.

4. **DO NOT modify test files.** All 1081 existing tests must pass without changes. If tests fail, the decomposition is wrong.

5. **DO NOT move the `from shuffify.services.playlist_pair_service import PlaylistPairService` import in `_execute_rotate()` to the top of the file.** It is a deferred import to avoid circular imports. The new `_validate_rotation_config()` method must contain this same deferred import pattern.

6. **DO NOT move the `from shuffify.services.activity_log_service import ActivityLogService` import to the top of the file.** Same reason -- deferred to avoid circular imports. Keep it inside `_record_success()`.

7. **DO NOT change error message strings.** Tests match on error messages (e.g., `match="no stored refresh token"`, `match="No archive pair found"`, `match="Invalid rotation_mode"`). If you change the text, tests will fail.

8. **DO NOT extract the three rotation mode methods (`_rotate_archive`, `_rotate_refresh`, `_rotate_swap`) further.** They are already well-sized (25-45 lines each). The only change to them is replacing inline batch loops with `_batch_add_tracks()`.

9. **DO NOT add type annotations to the new workshop helper functions that reference internal types not already imported.** The workshop module uses `PlaylistService`, `PlaylistError`, etc. -- stick to what is already imported.

10. **DO NOT refactor `_execute_shuffle()`.** While it is 110 lines, the existing structure is reasonably clear (fetch, snapshot, transform, shuffle, update). Its auto-snapshot pattern is similar to raid/rotate, but Phase 2 (service deduplication) should address the shared snapshot pattern first.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/services/job_executor_service.py` - Primary target: 3 complex functions to decompose, 8 new helper methods
- `/Users/chris/Projects/shuffify/shuffify/spotify/api.py` - Secondary target: `api_error_handler` decomposition into 4 helper functions
- `/Users/chris/Projects/shuffify/shuffify/routes/workshop.py` - Two route functions to decompose into 4 helper functions
- `/Users/chris/Projects/shuffify/shuffify/services/raid_sync_service.py` - `raid_now()` split into 2 dispatch methods
- `/Users/chris/Projects/shuffify/tests/services/test_job_executor_rotate.py` - Most comprehensive test file; validates rotation decomposition correctness
