import atexit
import logging
import os
import re
import secrets
from typing import Optional
from urllib.parse import unquote, urlsplit

import redis
from flask import Flask, Request, current_app, g, request
from flask_limiter import Limiter
from flask_migrate import Migrate
from flask_session import Session
from flask_wtf.csrf import CSRFProtect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
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

    from flask import current_app

    from shuffify.spotify.cache import SpotifyCache

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
        from flask import current_app

        from shuffify.models.db import db

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
    when REDIS_URL is unset or unreachable. Sessions then live on the
    container filesystem, the Spotify API cache is disabled, and
    Flask-Limiter uses in-memory storage.
    """
    redis_url = app.config.get("REDIS_URL")
    if redis_url:
        try:
            client = _create_redis_client(redis_url)
            client.ping()
            app.config["SESSION_REDIS"] = client
            logger.info(
                "Redis session storage configured: %s",
                _loggable_redis_url(redis_url),
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

# Internal handshake, set by whatever process applies migrations. Not an
# operator knob. `flask db upgrade` imports run.py and builds the whole
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

    With ``MIGRATE_ON_STARTUP=False``, migrations are expected to be applied by
    a separate step before Gunicorn starts, so by the time the app factory runs
    "schema is at head" is an invariant to check rather than work to do. The
    check is satisfied entirely from the image -- the migration chain ships
    with the code -- so it cannot block a boot on infrastructure that has not
    been provisioned.

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
        "The app does not apply migrations at startup; apply them "
        "with 'flask db upgrade', or set %s=true to start anyway and serve "
        "against the current schema."
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

    The development path. Production must not migrate here -- running the
    upgrade from the app factory would put schema mutation inside every
    process that serves requests.
    """
    from flask_migrate import upgrade

    upgrade()
    logger.info("Alembic migrations applied")


# Startup log lines name the database and Redis they connected to. Ordinary log
# handlers bypass the Sentry scrubber, so the password is masked here. Each URL
# is parsed the way the library that connects with it parses it: the two
# disagree about where an unencoded "@" ends the password, so one parser cannot
# safely mask both. A URL that cannot be parsed is not echoed at all.
_UNPARSEABLE_URL = "<unparseable URL>"


def _is_secret_query_key(key):
    """A query parameter that can carry a credential to the connecting library.

    psycopg2 hands query parameters to connect() unchanged -- including ``dsn``,
    a whole connection string -- and redis-py passes unknown ones on as
    connection arguments (``ssl_password``, say). Anything naming a password is
    masked, whatever its spelling.
    """
    key = key.lower()
    return "pass" in key or key == "dsn"


def _loggable_db_url(url):
    """The database URL with its password masked, as SQLAlchemy reads it."""
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):
        return _UNPARSEABLE_URL
    if parsed.password is not None and str(url).count("@") > 1:
        # An unencoded "@" in the password. SQLAlchemy ends the password at the
        # first one, so the rest lands in the host, database or query.
        return _UNPARSEABLE_URL
    query = {k: "***" if _is_secret_query_key(k) else v for k, v in parsed.query.items()}
    return parsed.set(query=query).render_as_string(hide_password=True)


def _loggable_redis_url(url):
    """The Redis URL with its credentials masked, as redis-py (urllib) reads it."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return _UNPARSEABLE_URL
    if not parts.scheme:
        return _UNPARSEABLE_URL
    # urllib ends the credentials at the last "@".
    netloc = "***@" + parts.netloc.rpartition("@")[2] if "@" in parts.netloc else parts.netloc
    rendered = f"{parts.scheme}://{netloc}{parts.path}"
    if parts.query:
        pairs = parts.query.split("&")
        masked = [
            pair.partition("=")[0] + "=***" if _is_secret_query_key(unquote(pair.partition("=")[0])) else pair
            for pair in pairs
        ]
        rendered += "?" + "&".join(masked)
    return rendered


def _init_database(app):
    """Initialize SQLAlchemy and reconcile the database schema.

    Schema handling is declared per environment by two config attributes:

    - ``TESTING`` builds tables straight from the models; there is no
      migration chain to apply against in-memory SQLite.
    - ``MIGRATE_ON_STARTUP=False`` (production) means something outside the
      app is expected to have run ``flask db upgrade`` already, so the factory
      only verifies the result and refuses to serve a stale one.
    - ``MIGRATE_ON_STARTUP=True`` applies migrations in-process.
    - In a migration-step process (``SHUFFIFY_MIGRATION_STEP``) the factory
      does none of these: the step's own commands build, upgrade and stamp.

    ``DevConfig`` sets ``TESTING=True``, so development takes the
    ``create_all()`` branch and never reaches the migration chain. Turning
    that flag off routes development through ``_upgrade_schema``.
    """
    global _migrate
    try:
        from shuffify.models.db import db

        db.init_app(app)
        _migrate = Migrate(app, db)

        with app.app_context():
            if _env_is_true(MIGRATION_STEP_VAR):
                logger.info(
                    "Schema check skipped -- this process is the migration step"
                )
            elif app.config.get("TESTING"):
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
            _loggable_db_url(app.config["SQLALCHEMY_DATABASE_URI"]),
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
    that leaves uncovered.
    """
    lowered = str(key).lower()
    if lowered in _SENTRY_PII_EXACT_KEYS:
        return True
    return any(token in lowered for token in _SENTRY_PII_DENYLIST)


_SENTRY_REDACTED = "[Filtered]"

# Config attributes whose values are secrets in their own right. Registered at
# init so free-text redaction can match them exactly -- no false positives, but
# only covers what the process knows at startup. Per-user OAuth material is not
# knowable here and is matched by shape instead.
_SENTRY_SECRET_CONFIG_ATTRS = (
    "SPOTIFY_CLIENT_SECRET",
    "SECRET_KEY",
    "TOKEN_ENCRYPTION_KEY",
    "TOKEN_ENCRYPTION_KEY_FALLBACKS",
)

# Config attributes holding a URL whose password component is a secret. The
# URL itself is diagnostic and stays readable; only the password is registered.
# _init_database logs a failed connect at ERROR with exc_info, which is exactly
# the path that puts a DSN into an exception string.
_SENTRY_SECRET_URL_CONFIG_ATTRS = (
    "SQLALCHEMY_DATABASE_URI",
    "DATABASE_URL",
    "REDIS_URL",
)

# A secret shorter than this is not safe to substring-match: a three-character
# SECRET_KEY would blank an unrelated fragment of every message containing it.
_SENTRY_MIN_SECRET_LEN = 8

_sentry_known_secrets: tuple = ()

# "Bearer <token>" -- the value sits under no key; the auth scheme names it.
_SENTRY_BEARER_RE = re.compile(r"\b(Bearer\s+)([A-Za-z0-9._~+/=-]{8,})", re.IGNORECASE)


def _sentry_sensitive_key_pattern():
    """Derive a key sub-pattern from the two lists _sentry_key_is_sensitive reads.

    Only a *sensitive* key may open a match. Letting any key open one lets a
    harmless pair swallow a sensitive pair inside its value span: in
    "rejected: client_secret=abc" the key "rejected" matches, its value runs to
    the end, the pair is judged harmless, and the credential is consumed
    without ever being examined.

    Derived rather than hand-written so the denylists stay the single source of
    truth. Exact keys keep their boundaries -- "code" must not match inside
    "status_code", which is the distinction _SENTRY_PII_EXACT_KEYS exists for.

    The group is atomic. Without it the pattern is quadratic: inside one
    unbroken word-character run every denylist occurrence opens a candidate
    key, and when the separator then fails the trailing ``*`` backtracks across
    the rest of the run re-failing the lookahead at each step. Exception
    strings are built from uncapped third-party response bodies, and Sentry
    serialises to 100_000 characters before before_send sees the text, so that
    is reachable input, not a theoretical one. Committing to the first split is
    safe here because every successful key match ends at the same place: the
    run boundary the trailing lookahead requires.
    """
    substring = "|".join(re.escape(token) for token in _SENTRY_PII_DENYLIST)
    exact = "|".join(re.escape(key) for key in _SENTRY_PII_EXACT_KEYS)
    return (
        r"(?<![A-Za-z0-9_-])"
        rf"(?>[A-Za-z0-9_-]*(?:{substring})[A-Za-z0-9_-]*|(?:{exact}))"
        r"(?![A-Za-z0-9_-])"
    )


# key=value, key: value, 'key': 'value' -- the same key policy the dict walk
# uses, projected onto text. Deriving the key from the shared denylists rather
# than carrying a second one means widening a list improves both surfaces.
_SENTRY_LABELLED_RE = re.compile(
    rf"""(?P<key>{_sentry_sensitive_key_pattern()})
        (?P<pre>['"]?\s*[=:]\s*)
        (?P<quote>['"]?)
        (?P<value>[^\s'",;&}}\]]+)""",
    re.VERBOSE | re.IGNORECASE,
)

# Backreferences by name: keep the key, separator and opening quote, drop the
# value. A template rather than a callback -- the pattern admits only sensitive
# keys, so a per-match Python call would have nothing left to decide.
_SENTRY_LABELLED_SUB = r"\g<key>\g<pre>\g<quote>" + _SENTRY_REDACTED

# Spotify OAuth material is recognisable by prefix and length: access tokens
# carry BQ, refresh tokens and authorization codes AQ, and Fernet ciphertext
# (the at-rest form of a refresh token) gAAAAA. The 38-character tail keeps the
# pattern clear of 22-character Spotify object IDs, which are exactly the
# diagnostic content Sentry exists to show.
_SENTRY_OAUTH_TOKEN_RE = re.compile(r"\b(?:BQ|AQ)[A-Za-z0-9_-]{38,}")
_SENTRY_FERNET_RE = re.compile(r"\bgAAAAA[A-Za-z0-9_=-]{20,}")


def _register_sentry_secret_values(config_class):
    """Snapshot the secrets this process holds, for exact-match redaction.

    Replaces the registry rather than extending it, so re-initialising against
    a different config cannot leave a stale value behind. Returns the registry
    for inspection.
    """
    global _sentry_known_secrets

    values = []
    for attr in _SENTRY_SECRET_CONFIG_ATTRS:
        raw = getattr(config_class, attr, None)
        candidates = raw if isinstance(raw, (list, tuple, set)) else [raw]
        for candidate in candidates:
            if isinstance(candidate, bytes):
                candidate = candidate.decode("utf-8", "replace")
            if isinstance(candidate, str) and len(candidate) >= _SENTRY_MIN_SECRET_LEN:
                values.append(candidate)

    for attr in _SENTRY_SECRET_URL_CONFIG_ATTRS:
        url = getattr(config_class, attr, None)
        if not isinstance(url, str) or "@" not in url:
            continue
        try:
            password = urlsplit(url).password
        except ValueError:
            continue
        if password and len(password) >= _SENTRY_MIN_SECRET_LEN:
            values.append(password)

    # Longest first, so a secret containing another is redacted whole.
    _sentry_known_secrets = tuple(sorted(set(values), key=len, reverse=True))
    return _sentry_known_secrets


def _scrub_text(text):
    """Redact secret-shaped spans from free text.

    Only the matched span is replaced, never the whole string, so the
    surrounding diagnostic survives. Four locators run: exact known secrets,
    Bearer values, values under a sensitive label, and Spotify OAuth material
    matched by prefix and length.

    One adjacency is load-bearing: Bearer must precede the label locator. On
    "Authorization: Bearer <token>" the label locator's value stops at
    whitespace, so running it first redacts the word "Bearer" and leaves the
    token on the wire.
    """
    if not isinstance(text, str) or not text:
        return text

    for secret in _sentry_known_secrets:
        text = text.replace(secret, _SENTRY_REDACTED)

    text = _SENTRY_BEARER_RE.sub(lambda m: m.group(1) + _SENTRY_REDACTED, text)

    # Only the value is dropped. The closing quote sits outside the match --
    # the value class excludes it -- so the opening one is all that is re-emitted.
    text = _SENTRY_LABELLED_RE.sub(_SENTRY_LABELLED_SUB, text)
    text = _SENTRY_OAUTH_TOKEN_RE.sub(_SENTRY_REDACTED, text)
    return _SENTRY_FERNET_RE.sub(_SENTRY_REDACTED, text)


def _strip_pii(event, hint):
    """Sentry before_send hook: redact sensitive keys and secret-shaped values.

    Walks request headers, request data, extras, contexts, breadcrumbs, and
    exception stack-trace frame variables; replaces any value whose key is
    sensitive with a fixed redaction sentinel. Cookies and Authorization are
    stripped wholesale.

    Frame variables are the last line of defense, not the first: capture is
    disabled at the SDK level in _init_sentry. This walk keeps secrets out of
    the payload even if a stack trace arrives carrying them anyway.

    Two policies run in the same walk. A value under a sensitive *key* is
    replaced wholesale. Every string *leaf* additionally goes through
    _scrub_text, which redacts secret-shaped spans in place -- so a credential
    interpolated into a string, which no key names, is caught too:
    ``logger.error("failed for %s", token)`` does not egress the token.
    That closes log message text (``logentry``), exception strings
    (``exception.values[].value``), breadcrumb messages and everything else
    the walk reaches for free.

    The two policies differ in kind. Key matching is exact: nothing under a
    sensitive key escapes. Value matching is best-effort -- it recognises the
    secrets this process holds (including the password inside a configured
    connection string), Bearer values, values under a sensitive label, and
    Spotify OAuth material by shape. It is a backstop for the credential
    someone interpolates into a message, not a licence to do so; prefer
    ``extra={"token": token}``, which the exact pass covers.

    Known bound: this governs the Sentry egress only. The same interpolated
    secret still reaches stderr and the platform log store through the ordinary
    logging handlers, which this hook is not in the path of.
    """

    def _redact(obj):
        if isinstance(obj, dict):
            return {
                k: (_SENTRY_REDACTED if _sentry_key_is_sensitive(k) else _redact(v))
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple)):
            return [_redact(item) for item in obj]
        # String leaves get the value-level pass. Scrubbing here rather than at
        # an enumerated list of fields is what makes the free-text cover fail
        # closed: a payload field nobody listed is still walked.
        if isinstance(obj, str):
            return _scrub_text(obj)
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
    # Free-text nodes the walk above is not applied to. logentry carries
    # message, formatted and params, and _redact handles all three shapes --
    # params is record.args, a list or a mapping.
    if "logentry" in event:
        event["logentry"] = _redact(event["logentry"])
    if isinstance(event.get("message"), str):
        event["message"] = _scrub_text(event["message"])

    exception = event.get("exception")
    if isinstance(exception, dict):
        for value in exception.get("values") or []:
            if isinstance(value, dict):
                if isinstance(value.get("value"), str):
                    value["value"] = _scrub_text(value["value"])
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
    # Registered before the DSN guard so the scrubber is armed regardless of
    # whether this particular process ends up sending events.
    _register_sentry_secret_values(config_class)

    dsn = getattr(config_class, "SENTRY_DSN", "") or ""
    if not dsn:
        logger.info("Sentry disabled (no SENTRY_DSN configured)")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.sqlalchemy import (
            SqlalchemyIntegration,
        )
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
    from shuffify.spotify.endpoints import ACCOUNTS_BASE_URL

    @app.before_request
    def _generate_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(32)

    @app.context_processor
    def _inject_csp_nonce():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.after_request
    def set_security_headers(response):
        # Views marked @no_store (fresh reviews and checks) are never reused,
        # including their CSRF, oversize, preflight and unexpected failures.
        if getattr(app.view_functions.get(request.endpoint), "no_store", False):
            response.headers["Cache-Control"] = "no-store"
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
            # The one external script, SortableJS, is permitted by its exact
            # file path rather than by host, and is also pinned by SRI at its
            # script tag -- bump the version in both places together. Tailwind
            # is compiled into static/css at build time, so no CDN serves it.
            f"script-src 'self' https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js 'nonce-{nonce}'; "
            f"style-src 'self' 'nonce-{nonce}'; "
            # Spotify serves cover art from many scdn.co subdomains, not just
            # i: an auto-generated four-up mosaic comes from mosaic.scdn.co,
            # editorial art from thisis-images, daily-mix, newjams-images,
            # lineup-images, charts-images and seeded-session-images. The set
            # is open-ended, so scdn.co is wildcarded, as spotifycdn.com is.
            "img-src 'self' https://*.scdn.co https://*.spotifycdn.com data:; "
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
            f"form-action 'self' {ACCOUNTS_BASE_URL}"
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

_FORM_MIMETYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data"})


class BoundedRequest(Request):
    """Flask's request, with a form body held to MAX_FORM_MEMORY_SIZE.

    The app-wide CSRF check parses the form of every POST before any auth
    check. Werkzeug leaves every body to MAX_CONTENT_LENGTH, which has to fit
    the Workshop's JSON, so a form body is held to the smaller form limit
    here: the app's forms are a few fields, and it takes no uploads.
    """

    @property
    def max_content_length(self) -> int | None:
        limit = super().max_content_length
        form_limit = current_app.config["MAX_FORM_MEMORY_SIZE"] if current_app else None
        if form_limit is not None and self.mimetype in _FORM_MIMETYPES:
            return form_limit if limit is None else min(limit, form_limit)
        return limit

    @max_content_length.setter
    def max_content_length(self, value: int | None) -> None:
        Request.max_content_length.fset(self, value)  # a view can still set its own


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
    app.request_class = BoundedRequest
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
            # Each call creates a playlist in the user's Spotify library.
            app.view_functions["main.playlist_setup_create_playlist"] = _limiter.limit("10/minute")(
                app.view_functions["main.playlist_setup_create_playlist"]
            )
            logger.info(
                "Rate limits applied: /login=10/min, "
                "/callback=10/min, /shuffle=5/min, "
                "/workshop/commit=10/min, "
                "/schedules/*/run=5/min, "
                "/playlist/*/setup/spotify-playlists=10/min"
            )
        except Exception as e:
            logger.warning("Failed to apply rate limits: %s", e)

    # Register global error handlers
    from shuffify.error_handlers import register_error_handlers

    register_error_handlers(app)

    # Templates render raw Spotify playlist objects, which carry the track
    # count under "tracks" or "items"; read it in either shape.
    from shuffify.spotify.api import playlist_item_count

    app.add_template_filter(playlist_item_count, "item_count")

    # Register operational CLI commands
    from shuffify.cli import register_cli

    register_cli(app)

    # Initialize APScheduler (after all extensions)
    if app.config.get("SCHEDULER_ENABLED", True):
        from shuffify.scheduler import init_scheduler, shutdown_scheduler

        scheduler = init_scheduler(app)
        if scheduler:
            app.extensions["scheduler"] = scheduler
        # Bound now, and only where a scheduler can exist: importing the
        # scheduler module from inside an exit hook fails once threading has
        # shut down.
        atexit.register(shutdown_scheduler)

    _apply_security_headers(app)

    return app
