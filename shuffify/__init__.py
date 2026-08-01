import os
import atexit
import logging
import secrets
from typing import Optional
from flask import Flask, g
from flask_session import Session
import redis
from flask_limiter import Limiter
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from config import config, validate_required_env_vars

logger = logging.getLogger(__name__)

# Global Redis client for caching (initialized in create_app)
_redis_client: Optional[redis.Redis] = None
_migrate: Optional[Migrate] = None
_limiter: Optional[Limiter] = None


def _create_redis_client(redis_url: str) -> redis.Redis:
    """
    Create a Redis client from URL.

    Args:
        redis_url: Redis connection URL.

    Returns:
        Redis client instance.

    Raises:
        redis.ConnectionError: If connection fails.
    """
    return redis.from_url(redis_url, decode_responses=False)


def get_redis_client() -> Optional[redis.Redis]:
    """
    Get the global Redis client for caching.

    Returns:
        Redis client if configured, None otherwise.
    """
    return _redis_client


def get_limiter() -> Optional[Limiter]:
    """
    Get the global Flask-Limiter instance.

    Returns:
        Limiter instance if initialized, None otherwise.
    """
    return _limiter


def get_spotify_cache():
    """
    Get a SpotifyCache instance for Spotify API caching.

    Returns:
        SpotifyCache instance if Redis is configured, None otherwise.

    Example:
        from shuffify import get_spotify_cache
        from shuffify.spotify import SpotifyAPI

        cache = get_spotify_cache()
        api = SpotifyAPI(token_info, auth_manager, cache=cache)
    """
    if _redis_client is None:
        return None

    from shuffify.spotify.cache import SpotifyCache
    from flask import current_app

    # Get TTL settings from config if available
    try:
        config = current_app.config
        return SpotifyCache(
            _redis_client,
            key_prefix=config.get("CACHE_KEY_PREFIX", "shuffify:cache:"),
            default_ttl=config.get("CACHE_DEFAULT_TTL", 300),
            playlist_ttl=config.get("CACHE_PLAYLIST_TTL", 60),
            user_ttl=config.get("CACHE_USER_TTL", 600),
            audio_features_ttl=config.get("CACHE_AUDIO_FEATURES_TTL", 86400),
        )
    except RuntimeError:
        # Not in Flask context - use defaults
        return SpotifyCache(_redis_client)


def is_db_available() -> bool:
    """
    Check if the SQLAlchemy database is initialized and available.

    Returns:
        True if database is available, False otherwise.
    """
    try:
        from shuffify.models.db import db
        from flask import current_app

        # Verify we're in app context and db is initialized
        if not current_app:
            return False
        # Quick test query
        db.session.execute(db.text("SELECT 1"))
        return True
    except Exception:
        return False


# =============================================================================
# App Factory Helpers
# =============================================================================


def _init_redis(app):
    """Configure Redis for session storage and caching.

    Falls back to filesystem sessions (and disables Redis-backed caching)
    when REDIS_URL is unset or unreachable. The fallback path is the
    documented production deployment mode for installations without a
    provisioned Redis — sessions live on the container filesystem, the
    Spotify API cache is disabled, and Flask-Limiter uses in-memory
    storage. See CLAUDE.md "Production Infrastructure" for the tradeoff.
    """
    redis_url = app.config.get("REDIS_URL")
    if redis_url:
        try:
            client = _create_redis_client(redis_url)
            client.ping()
            app.config["SESSION_REDIS"] = client
            logger.info(
                "Redis session storage configured: %s",
                redis_url.split("@")[-1],
            )
            logger.info("Redis caching enabled")
            return client
        except redis.ConnectionError as e:
            logger.warning(
                "Redis connection failed: %s. Falling back to filesystem sessions.",
                e,
            )
    else:
        logger.warning("REDIS_URL not configured. Using filesystem sessions.")

    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = "./.flask_session/"
    os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)
    return None


def _init_limiter(app, redis_client):
    """Initialize Flask-Limiter for rate limiting.

    Returns Limiter instance if successful, None otherwise.
    """
    try:
        from flask_limiter.util import get_remote_address

        if redis_client is not None:
            storage_uri = app.config.get("REDIS_URL")
            logger.info("Rate limiter using Redis storage")
        else:
            storage_uri = "memory://"
            logger.warning(
                "Rate limiter using in-memory storage. "
                "Rate limits will not persist across restarts."
            )

        limiter = Limiter(
            app=app,
            key_func=get_remote_address,
            storage_uri=storage_uri,
            default_limits=[],
            strategy="fixed-window",
        )
        logger.info("Flask-Limiter initialized")
        return limiter
    except Exception as e:
        logger.warning(
            "Flask-Limiter initialization failed: %s. "
            "Rate limiting will be unavailable.",
            e,
        )
        return None


def _init_token_encryption(app):
    """Initialize Fernet token encryption service."""
    from shuffify.services.token_service import TokenService

    try:
        TokenService.initialize(
            app.config["SECRET_KEY"],
            token_encryption_key=app.config.get("TOKEN_ENCRYPTION_KEY"),
            fallback_keys=app.config.get("TOKEN_ENCRYPTION_KEY_FALLBACKS") or (),
        )
        logger.info("Token encryption service initialized")
    except Exception as e:
        if app.config.get("TOKEN_ENCRYPTION_KEY"):
            # An explicitly configured key that cannot be used is an
            # operator error — fail the boot rather than run with
            # token encryption silently disabled.
            raise
        logger.warning(
            "Token encryption init failed: %s. "
            "Scheduled operations will be unavailable.",
            e,
        )


# Break-glass override for the production schema guard. When set, the
# entrypoint tolerates a failed upgrade and the app factory downgrades the
# schema-drift check from a startup failure to an ERROR log. It exists so a
# misfiring guard can be cleared from the platform console in one restart,
# rather than blocking every deploy until a code change ships.
SCHEMA_DRIFT_OVERRIDE_VAR = "SHUFFIFY_ALLOW_SCHEMA_DRIFT"

# Internal handshake, set by the entrypoint while it applies migrations --
# not an operator knob. `flask db upgrade` imports run.py and builds the whole
# application, so without this the schema check would fire inside the
# migration step and refuse to let it run: the check would be asserting the
# very invariant that step exists to establish.
MIGRATION_STEP_VAR = "SHUFFIFY_MIGRATION_STEP"

_TRUTHY = ("1", "true", "yes", "on")


def _env_is_true(name):
    return os.getenv(name, "").strip().lower() in _TRUTHY


class SchemaOutOfDateError(RuntimeError):
    """The database schema is behind the Alembic migration chain.

    Raised during app construction in production. It propagates out of
    ``create_app()`` deliberately: serving requests against a schema the
    code does not expect corrupts data and returns errors that look like
    application bugs, which is strictly worse than refusing to start.
    """


def _schema_revision_state(migrations_dir):
    """Return ``(current_revision, head_revisions)`` for the database schema.

    ``current_revision`` is the revision stamped in ``alembic_version``, or
    None when that table does not exist (a database that has never been
    migrated). ``head_revisions`` is the set of tip revisions in
    ``migrations/versions`` -- a set rather than a single value so a branched
    chain reports honestly instead of raising.
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from shuffify.models.db import db

    heads = set(ScriptDirectory(migrations_dir).get_heads())
    with db.engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    return current, heads


def _verify_schema_at_head(migrations_dir):
    """Fail fast when the production schema is not at the migration head.

    Production applies migrations in the container entrypoint, before Gunicorn
    starts, so by the time the app factory runs "schema is at head" is an
    invariant to check rather than work to do. The check is satisfied entirely
    from the image -- the migration chain ships with the code -- so it cannot
    block a boot on infrastructure that has not been provisioned.

    The migration step itself is exempt: it builds the app in order to apply
    the migrations, and would otherwise be refused for the schema state it is
    about to correct.

    Raises:
        SchemaOutOfDateError: If the schema is behind head and the override
            is not set.
    """
    if _env_is_true(MIGRATION_STEP_VAR):
        logger.info("Schema check skipped -- this process is the migration step")
        return

    current, heads = _schema_revision_state(migrations_dir)

    if current is not None and current in heads:
        logger.info("Database schema verified at Alembic head (%s)", current)
        return

    message = (
        "Database schema is not at Alembic head: current=%s, head=%s. "
        "Migrations are applied by the container entrypoint before Gunicorn "
        "starts, so this means the entrypoint was bypassed or its upgrade "
        "failed. Apply them with 'flask db upgrade', or set %s=true to start "
        "anyway and serve against the current schema."
        % (
            current or "none (database never migrated)",
            ",".join(sorted(heads)) or "none",
            SCHEMA_DRIFT_OVERRIDE_VAR,
        )
    )

    if _env_is_true(SCHEMA_DRIFT_OVERRIDE_VAR):
        logger.error(
            "%s [%s is set -- starting anyway]", message, SCHEMA_DRIFT_OVERRIDE_VAR
        )
        return

    raise SchemaOutOfDateError(message)


def _upgrade_schema():
    """Apply pending Alembic migrations in-process.

    The development path. Production migrates in the container entrypoint;
    running the upgrade from the app factory there would put schema mutation
    inside every process that serves requests.
    """
    from flask_migrate import upgrade

    upgrade()
    logger.info("Alembic migrations applied")


def _init_database(app):
    """Initialize SQLAlchemy and reconcile the database schema.

    Schema handling is declared per environment by two config attributes:

    - ``TESTING`` builds tables straight from the models; there is no
      migration chain to apply against in-memory SQLite.
    - ``MIGRATE_ON_STARTUP=False`` (production) means something outside the
      app already ran ``flask db upgrade`` -- the container entrypoint -- so
      the factory only verifies the result and refuses to serve a stale one.
    - ``MIGRATE_ON_STARTUP=True`` applies migrations in-process.

    Note that ``DevConfig`` currently sets ``TESTING=True``, so development
    takes the ``create_all()`` branch and never reaches the migration chain
    (#325). Flipping that flag is what routes development through
    ``_upgrade_schema``.
    """
    global _migrate
    try:
        from shuffify.models.db import db

        db.init_app(app)
        _migrate = Migrate(app, db)

        with app.app_context():
            if app.config.get("TESTING"):
                # Tests use in-memory SQLite -- create tables directly
                db.create_all()
            else:
                migrations_dir = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "migrations"
                )
                if not os.path.isdir(migrations_dir):
                    logger.warning(
                        "No migrations/ directory found. "
                        "Using db.create_all() as fallback. "
                        "Run 'flask db init && flask db migrate' "
                        "to set up Alembic migrations."
                    )
                    db.create_all()
                elif app.config.get("MIGRATE_ON_STARTUP", True):
                    _upgrade_schema()
                else:
                    _verify_schema_at_head(migrations_dir)

        logger.info(
            "SQLAlchemy database initialized: %s",
            app.config.get("SQLALCHEMY_DATABASE_URI", "not set"),
        )
    except SchemaOutOfDateError:
        # Never absorbed into the generic handler below -- a stale schema is
        # the one database condition the app must not serve through.
        raise
    except Exception as e:
        logger.error(
            "Database initialization failed: %s "
            "[type=%s]. Persistence features will "
            "be unavailable.",
            e,
            type(e).__name__,
            exc_info=True,
        )


_SENTRY_PII_DENYLIST = (
    "refresh_token",
    "access_token",
    "encrypted_refresh_token",
    "encrypted_",
    "authorization",
    "cookie",
    "secret_key",
    "client_secret",
    "email",
    "session",
    "token_data",
    "token_info",
)

# Keys redacted on an exact match rather than a substring match. An OAuth
# authorization code lives under the bare key "code"; matching that as a
# substring would also redact status_code, error_code, and similar
# diagnostic values that Sentry exists to show.
_SENTRY_PII_EXACT_KEYS = (
    "code",
    "password",
    "token",
    "secret",
)


def _sentry_key_is_sensitive(key):
    """True when a payload key must have its value redacted.

    Keys only -- the value itself is never examined. See _strip_pii for what
    that leaves uncovered (#514).
    """
    lowered = str(key).lower()
    if lowered in _SENTRY_PII_EXACT_KEYS:
        return True
    return any(token in lowered for token in _SENTRY_PII_DENYLIST)


def _strip_pii(event, hint):
    """Sentry before_send hook: redact known-sensitive keys.

    Walks request headers, request data, extras, contexts, breadcrumbs, and
    exception stack-trace frame variables; replaces any value whose key is
    sensitive with a fixed redaction sentinel. Cookies and Authorization are
    stripped wholesale.

    Frame variables are the last line of defense, not the first: capture is
    disabled at the SDK level in _init_sentry. This walk keeps secrets out of
    the payload even if a stack trace arrives carrying them anyway.

    Boundary -- redaction is **key-based only**. A value is filtered because
    the key naming it is sensitive; the scrubber never inspects free text.
    Anything interpolated into a string rather than stored under a key passes
    through untouched: log message text (``logentry.message``), exception
    strings (``exception.values[].value``), and breadcrumb messages. So
    ``logger.error("failed for %s", token)`` egresses the token even though
    ``logger.error("failed", extra={"token": token})`` would not. Do not
    interpolate credentials into messages or exception text on the assumption
    that this hook covers them. Value/pattern-based scrubbing is tracked in
    issue #514.
    """

    def _redact(obj):
        if isinstance(obj, dict):
            return {
                k: ("[Filtered]" if _sentry_key_is_sensitive(k) else _redact(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_redact(item) for item in obj]
        return obj

    def _redact_stacktrace(stacktrace):
        if not isinstance(stacktrace, dict):
            return
        frames = stacktrace.get("frames")
        if not isinstance(frames, list):
            return
        for frame in frames:
            if isinstance(frame, dict) and "vars" in frame:
                frame["vars"] = _redact(frame["vars"])

    if not isinstance(event, dict):
        return event

    if "request" in event:
        event["request"] = _redact(event["request"])
    if "extra" in event:
        event["extra"] = _redact(event["extra"])
    if "contexts" in event:
        event["contexts"] = _redact(event["contexts"])
    if "breadcrumbs" in event:
        event["breadcrumbs"] = _redact(event["breadcrumbs"])

    exception = event.get("exception")
    if isinstance(exception, dict):
        for value in exception.get("values") or []:
            if isinstance(value, dict):
                _redact_stacktrace(value.get("stacktrace"))
    _redact_stacktrace(event.get("stacktrace"))

    threads = event.get("threads")
    if isinstance(threads, dict):
        for value in threads.get("values") or []:
            if isinstance(value, dict):
                _redact_stacktrace(value.get("stacktrace"))

    return event


def _init_sentry(config_class):
    """Initialize sentry_sdk if SENTRY_DSN is configured.

    Safe no-op when the DSN is empty (dev, tests, or production with
    Sentry disabled). Imports sentry_sdk lazily so the module is
    optional at install time.
    """
    dsn = getattr(config_class, "SENTRY_DSN", "") or ""
    if not dsn:
        logger.info("Sentry disabled (no SENTRY_DSN configured)")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.sqlalchemy import (
            SqlalchemyIntegration,
        )
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError as e:
        logger.warning("sentry-sdk not installed: %s. Skipping Sentry init.", e)
        return False

    release = getattr(config_class, "SENTRY_RELEASE", "") or None

    sentry_sdk.init(
        dsn=dsn,
        environment=getattr(config_class, "SENTRY_ENVIRONMENT", "production"),
        traces_sample_rate=getattr(config_class, "SENTRY_TRACES_SAMPLE_RATE", 0.0),
        profiles_sample_rate=getattr(config_class, "SENTRY_PROFILES_SAMPLE_RATE", 0.0),
        send_default_pii=False,
        # OAuth codes, token dicts, and credentials live in the frame locals
        # of the token-exchange and refresh paths, which log at ERROR with
        # exc_info. Capturing locals would ship them to a third-party store.
        include_local_variables=False,
        release=release,
        integrations=[
            FlaskIntegration(),
            SqlalchemyIntegration(),
            LoggingIntegration(
                level=logging.INFO,
                event_level=logging.WARNING,
            ),
        ],
        before_send=_strip_pii,
    )
    logger.info(
        "Sentry initialized (environment=%s)",
        getattr(config_class, "SENTRY_ENVIRONMENT", "production"),
    )
    return True


def _apply_security_headers(app):
    """Register CSP nonce generation and security headers."""

    @app.before_request
    def _generate_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(32)

    @app.context_processor
    def _inject_csp_nonce():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.after_request
    def set_security_headers(response):
        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Control referrer information leakage
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        nonce = getattr(g, "csp_nonce", "")

        # Content Security Policy — nonce-based, no unsafe-inline
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' https://cdn.tailwindcss.com "
            f"https://cdn.jsdelivr.net 'nonce-{nonce}'; "
            f"style-src 'self' 'nonce-{nonce}'; "
            "img-src 'self' https://i.scdn.co https://*.spotifycdn.com data:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            # The login form GETs /login, which 302s to Spotify's authorize
            # endpoint. form-action is enforced against every hop of a
            # submission chain, so the same-origin action passes and the
            # cross-origin redirect is blocked unless the OAuth host is listed
            # here. Scoped to the authorize host only; this is a targeted
            # allowance for a known destination, not a relaxation of the
            # injection protections in style-src/script-src.
            "form-action 'self' https://accounts.spotify.com"
        )

        # HSTS: only in production (development uses HTTP)
        if not app.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        # No-cache headers for development mode
        if app.debug:
            response.headers["Cache-Control"] = (
                "no-cache, no-store, must-revalidate, public, max-age=0"
            )
            response.headers["Expires"] = 0
            response.headers["Pragma"] = "no-cache"

        return response


# =============================================================================
# App Factory
# =============================================================================


def create_app(config_name=None):
    """Create and configure the Flask application."""
    if config_name is None:
        config_name = os.getenv("APP_CONFIG", "production")

    # Ensure config_name is a string
    if not isinstance(config_name, str):
        config_name = "production"  # Default to production if not a string

    logging.basicConfig(
        level=logging.DEBUG if config_name != "production" else logging.INFO
    )

    logger.info("Creating app with config: %s", config_name)

    # Validate required environment variables
    try:
        validate_required_env_vars(config_name)
        logger.info("Environment validation passed")
    except ValueError as e:
        logger.error("Environment validation failed: %s", str(e))
        if config_name == "production":
            raise  # Fail fast in production
        else:
            logger.warning(
                "Continuing in development mode with missing environment variables"
            )

    # Initialize Sentry before Flask so FlaskIntegration can hook
    # the WSGI app on construction.
    _init_sentry(config[config_name])

    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Trust one level of proxy so request.remote_addr reflects the real client IP.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Log important config values
    logger.info("SPOTIFY_REDIRECT_URI: %s", app.config.get("SPOTIFY_REDIRECT_URI"))
    logger.info("CONFIG_NAME: %s", app.config.get("CONFIG_NAME", config_name))

    # Initialize extensions
    CSRFProtect(app)
    global _redis_client, _limiter
    _redis_client = _init_redis(app)
    Session(app)
    _limiter = _init_limiter(app, _redis_client)
    _init_token_encryption(app)
    _init_database(app)

    # Register blueprints
    from shuffify.routes import main as main_blueprint

    app.register_blueprint(main_blueprint)

    # Apply rate limits to auth endpoints.
    # Traditional account lockout does not apply: this is an
    # OAuth-only app — users never enter credentials here.
    # Per-IP rate limiting on /login and /callback is the
    # appropriate brute-force protection for OAuth flows.
    if _limiter is not None:
        try:
            app.view_functions["main.login"] = _limiter.limit("10/minute")(
                app.view_functions["main.login"]
            )
            app.view_functions["main.callback"] = _limiter.limit("10/minute")(
                app.view_functions["main.callback"]
            )
            # Resource-intensive endpoints
            app.view_functions["main.shuffle"] = _limiter.limit("5/minute")(
                app.view_functions["main.shuffle"]
            )
            app.view_functions["main.workshop_commit"] = _limiter.limit("10/minute")(
                app.view_functions["main.workshop_commit"]
            )
            app.view_functions["main.run_schedule_now"] = _limiter.limit("5/minute")(
                app.view_functions["main.run_schedule_now"]
            )
            logger.info(
                "Rate limits applied: /login=10/min, "
                "/callback=10/min, /shuffle=5/min, "
                "/workshop/commit=10/min, "
                "/schedules/*/run=5/min"
            )
        except Exception as e:
            logger.warning("Failed to apply rate limits: %s", e)

    # Register global error handlers
    from shuffify.error_handlers import register_error_handlers

    register_error_handlers(app)

    # Register operational CLI commands
    from shuffify.cli import register_cli

    register_cli(app)

    # Initialize APScheduler (after all extensions)
    if app.config.get("SCHEDULER_ENABLED", True):
        from shuffify.scheduler import init_scheduler

        scheduler = init_scheduler(app)
        if scheduler:
            app.extensions["scheduler"] = scheduler

    # Register scheduler shutdown on app teardown
    @atexit.register
    def shutdown():
        from shuffify.scheduler import shutdown_scheduler

        shutdown_scheduler()

    _apply_security_headers(app)

    return app
