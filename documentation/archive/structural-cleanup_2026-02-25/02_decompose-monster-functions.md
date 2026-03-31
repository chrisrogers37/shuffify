# Phase 02: Decompose Monster Functions (error handlers, create_app)

`📋 PENDING`

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `refactor: Decompose register_error_handlers and create_app into focused helpers` |
| **Risk Level** | Low |
| **Estimated Effort** | Low (1-2 hours) |
| **Dependencies** | None |
| **Blocks** | Phase 03 (Extract Core Route Helpers) |

### Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/error_handlers.py` | Major refactor — extract 35 nested functions to module-level |
| `shuffify/__init__.py` | Major refactor — extract 5 helper functions from `create_app` |
| No test file changes required | Public APIs unchanged |

---

## Part A: Decompose `register_error_handlers()` in `shuffify/error_handlers.py`

### Current State

File: `shuffify/error_handlers.py` (431 lines)

`register_error_handlers(app)` (lines 60-431) is a single 371-line function containing 35 nested handler function definitions. Each handler is defined with `@app.errorhandler(...)` as a closure inside the outer function. The nested closures are unnecessary — the handler functions do not capture any variables from `register_error_handlers`'s scope (they only use `logger` and `json_error_response` that are already module-level).

### Target State

Every handler becomes a **module-level function**. `register_error_handlers(app)` shrinks to a ~40-line registration table that calls `app.errorhandler(exc)(fn)` for each one.

### Step-by-Step Instructions

**Step 1: Move all 35 handler functions to module level.**

Take each nested `def handle_*` function and de-indent it to module level, removing the `@app.errorhandler(...)` decorator. The function signature, docstring, body, and return value stay identical.

Before (nested inside `register_error_handlers`):

```python
def register_error_handlers(app):
    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        """Handle Pydantic validation errors."""
        errors_list = []
        for err in error.errors():
            field = ".".join(str(loc) for loc in err["loc"])
            msg = err["msg"]
            errors_list.append(f"{field}: {msg}")
        message = "; ".join(errors_list) if errors_list else "Validation failed"
        logger.warning(f"Validation error: {message}")
        return json_error_response(message, 400)
```

After (module-level):

```python
def handle_validation_error(error: ValidationError):
    """Handle Pydantic validation errors."""
    errors_list = []
    for err in error.errors():
        field = ".".join(str(loc) for loc in err["loc"])
        msg = err["msg"]
        errors_list.append(f"{field}: {msg}")
    message = "; ".join(errors_list) if errors_list else "Validation failed"
    logger.warning(f"Validation error: {message}")
    return json_error_response(message, 400)
```

Repeat for all 35 handlers. Names stay identical.

Note: `handle_not_found` and `handle_internal_error` reference `request` from Flask. The import on line 9 already covers this at module level.

**Step 2: Rewrite `register_error_handlers(app)` as a short registration function.**

```python
def register_error_handlers(app):
    """Register global error handlers with the Flask app."""
    handlers = [
        (ValidationError, handle_validation_error),
        (AuthenticationError, handle_authentication_error),
        (TokenValidationError, handle_token_validation_error),
        (PlaylistNotFoundError, handle_playlist_not_found),
        (NoHistoryError, handle_no_history_error),
        (InvalidAlgorithmError, handle_invalid_algorithm),
        (ParameterValidationError, handle_parameter_validation_error),
        (AlreadyAtOriginalError, handle_already_at_original),
        (PlaylistError, handle_playlist_error),
        (PlaylistUpdateError, handle_playlist_update_error),
        (ShuffleExecutionError, handle_shuffle_execution_error),
        (ShuffleError, handle_shuffle_error),
        (StateError, handle_state_error),
        (UserServiceError, handle_user_service_error),
        (UserNotFoundError, handle_user_not_found),
        (WorkshopSessionNotFoundError, handle_workshop_session_not_found),
        (WorkshopSessionLimitError, handle_workshop_session_limit),
        (WorkshopSessionError, handle_workshop_session_error),
        (UpstreamSourceNotFoundError, handle_upstream_source_not_found),
        (UpstreamSourceError, handle_upstream_source_error),
        (ScheduleNotFoundError, handle_schedule_not_found),
        (ScheduleLimitError, handle_schedule_limit),
        (ScheduleError, handle_schedule_error),
        (JobExecutionError, handle_job_execution_error),
        (SpotifyTokenExpiredError, handle_spotify_token_expired),
        (SpotifyRateLimitError, handle_spotify_rate_limit),
        (SpotifyNotFoundError, handle_spotify_not_found),
        (SpotifyAuthError, handle_spotify_auth_error),
        (SpotifyAPIError, handle_spotify_api_error),
        (SpotifyError, handle_spotify_error),
        (400, handle_bad_request),
        (401, handle_unauthorized),
        (404, handle_not_found),
        (500, handle_internal_error),
        (429, handle_rate_limit_exceeded),
    ]

    for exc_or_code, handler in handlers:
        app.errorhandler(exc_or_code)(handler)

    logger.info("Global error handlers registered")
```

Note: `app.errorhandler(X)(fn)` is the imperative equivalent of `@app.errorhandler(X)` — standard Flask pattern. Preserve the ordering (subclass before parent: e.g., `SpotifyTokenExpiredError` before `SpotifyError`).

---

## Part B: Decompose `create_app()` in `shuffify/__init__.py`

### Current State

File: `shuffify/__init__.py` (337 lines)

`create_app(config_name=None)` (lines 114-337) is a 223-line function with 8 initialization stages inline.

### Target State

Extract 5 stages into private helper functions. `create_app` becomes a ~60-line orchestrator.

### Step-by-Step Instructions

**Step 1: Extract `_init_redis(app)` — Redis + session configuration (lines 148-174).**

Returns the Redis client (or `None`). Sets `app.config["SESSION_REDIS"]` or falls back to filesystem.

```python
def _init_redis(app):
    """Configure Redis for session storage and caching.
    Returns Redis client if available, None otherwise.
    """
    redis_url = app.config.get("REDIS_URL")
    if redis_url:
        try:
            client = _create_redis_client(redis_url)
            client.ping()
            app.config["SESSION_REDIS"] = client
            logger.info("Redis session storage configured: %s", redis_url.split("@")[-1])
            logger.info("Redis caching enabled")
            return client
        except redis.ConnectionError as e:
            logger.warning("Redis connection failed: %s. Falling back to filesystem sessions.", e)
    else:
        logger.warning("REDIS_URL not configured. Using filesystem sessions.")

    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = "./.flask_session/"
    os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)
    return None
```

**Step 2: Extract `_init_limiter(app, redis_client)` — Flask-Limiter (lines 180-208).**

**Step 3: Extract `_init_token_encryption(app)` — Fernet token encryption (lines 211-221).**

**Step 4: Extract `_init_database(app)` — SQLAlchemy + Alembic migrations (lines 224-265).**

**Step 5: Extract `_apply_security_headers(app)` — `@app.after_request` block (lines 309-335).**

Note: The `set_security_headers` inner function legitimately captures `app` from the enclosing scope, so this nested closure is correct and necessary.

**Step 6: Rewrite `create_app()` as a short orchestrator (~60 lines).**

```python
def create_app(config_name=None):
    """Create and configure the Flask application."""
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "production")
    if not isinstance(config_name, str):
        config_name = "production"

    # ... env validation (unchanged) ...

    app = Flask(__name__)
    app.config.from_object(config[config_name])

    global _redis_client, _limiter
    _redis_client = _init_redis(app)
    Session(app)
    _limiter = _init_limiter(app, _redis_client)
    _init_token_encryption(app)
    _init_database(app)

    from shuffify.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    # Apply rate limits (unchanged inline — too small to extract)
    # Register error handlers (unchanged)
    # Init scheduler (unchanged)

    _apply_security_headers(app)
    return app
```

---

## Verification Checklist

```bash
# 1. Lint
./venv/bin/python -m flake8 shuffify/error_handlers.py shuffify/__init__.py

# 2. Directly affected tests
./venv/bin/python -m pytest tests/test_error_handlers.py tests/test_app_factory.py tests/routes/test_error_page_rendering.py -v

# 3. Full test suite
./venv/bin/python -m pytest tests/ -v

# 4. Smoke test — app starts without errors
python run.py
# Verify log output shows all init stages
# Visit http://localhost:8000/health
```

---

## What NOT To Do

1. **Do NOT rename any handler functions.** Names must stay identical.
2. **Do NOT change any handler's logic, status code, or response message.** Pure structural refactor.
3. **Do NOT change the exception-to-handler registration order.** Flask resolves by specificity.
4. **Do NOT use a decorator-based registration pattern.** The explicit handlers list is simpler.
5. **Do NOT split `error_handlers.py` into multiple files.** ~430 lines with flat functions is fine.
6. **Do NOT change the `create_app` function signature.** Must remain `create_app(config_name=None)`.
7. **Do NOT change the module-level globals pattern** (`_redis_client`, `_limiter`, `_migrate`).
8. **Do NOT modify any test files.** Public APIs are unchanged.
