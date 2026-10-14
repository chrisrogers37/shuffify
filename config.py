import os
import secrets
import sys
from urllib.parse import urlsplit

from dotenv import load_dotenv

# Don't load .env when running under pytest — test fixtures control the
# environment.  Without this guard, load_dotenv() injects the DATABASE_URL
# from a developer's .env at module-import time, before any fixture can
# override it, and tests connect to that database.
#
# The local test harness (APP_CONFIG=harness) skips it for the same reason:
# scripts/harness.sh sets every variable the run needs, and a developer's .env
# may hold real credentials.
if "pytest" not in sys.modules and os.getenv("APP_CONFIG") != "harness":
    load_dotenv()


def _resolve_database_url(fallback: str) -> str:
    """
    Resolve DATABASE_URL with PostgreSQL compatibility fixes.

    Bare ``postgres://`` and ``postgresql://`` URLs explicitly select the
    psycopg2 driver installed by Shuffify. SQLAlchemy 2.1 otherwise defaults
    to psycopg3, which is not installed. Explicit driver URLs are preserved.

    Args:
        fallback: Default database URL if DATABASE_URL env var is not set.

    Returns:
        Resolved database URL string.
    """
    url = os.getenv("DATABASE_URL", fallback)
    if url and url.startswith(("postgres://", "postgresql://")):
        url = "postgresql+psycopg2://" + url.split("://", 1)[1]
    return url


def _split_csv(raw: str) -> list:
    """Split a comma-separated env value into a clean list."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def validate_required_env_vars(config_name=None):
    """Validate that all required environment variables are present.

    Args:
        config_name: The configuration profile being used. When 'production',
            SECRET_KEY is required (no weak default allowed).
    """
    if config_name == "harness":
        HarnessConfig.assert_safe()

    required_vars = ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"]
    if config_name == "production":
        required_vars.append("SECRET_KEY")
        # The override exists for the local fake Spotify. In production it
        # would redirect OAuth -- and the client secret sent to the token
        # endpoint -- away from Spotify, so its presence is a boot failure.
        if os.getenv("SPOTIFY_BASE_URL"):
            raise ValueError("SPOTIFY_BASE_URL must not be set in production")
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")


# OAuth client credentials the local test harness runs with. They are not
# registered with Spotify, so no real token can ever be minted from them, and
# HarnessConfig refuses to start with any other credentials.
HARNESS_FAKE_CLIENT_ID = "harness-fake-client-id"
HARNESS_FAKE_CLIENT_SECRET = "harness-fake-client-secret"


class HarnessSafetyError(RuntimeError):
    """The harness config was pointed at something other than this machine.

    Deliberately not a ValueError: create_app() logs and continues past a
    ValueError from env validation for every non-production config, and this
    must stop the process instead.
    """


def _is_loopback_url(url: str) -> bool:
    """True when the URL's host is ``localhost`` or a loopback IP literal."""
    # Imported here: shuffify imports this module, so a top-level import of
    # the package would be circular.
    from shuffify.spotify.endpoints import is_loopback_host

    return bool(url) and is_loopback_host(urlsplit(url).hostname or "")


# libpq connects from C, outside the harness network guard, so the database
# host is checked as libpq will receive it: SQLAlchemy's connect arguments,
# joined the way psycopg2.connect() joins them, then read by libpq's own
# connection-string parser. Checking the URL text instead misses every place
# the parsers disagree -- a "#" before the query, ?dsn=, a key with a stray
# space -- and each of those can name another host.
def _libpq_conninfo(url: str):
    """The connection parameters libpq receives for this URL, or None."""
    import psycopg2.extensions
    from sqlalchemy.engine import make_url

    try:
        sa_url = make_url(url)
        if sa_url.get_backend_name() != "postgresql" or sa_url.get_driver_name() != "psycopg2":
            return None
        cargs, cparams = sa_url.get_dialect()().create_connect_args(sa_url)
        kwargs = dict(cparams)
        dsn = kwargs.pop("dsn", cargs[0] if cargs else None)
        return psycopg2.extensions.parse_dsn(psycopg2.extensions.make_dsn(dsn, **kwargs))
    except Exception:  # noqa: BLE001 - anything unreadable is refused
        return None


def _is_local_database_url(url: str) -> bool:
    """True when every host libpq could connect to for this URL is local."""
    from shuffify.spotify.endpoints import is_loopback_host

    conninfo = _libpq_conninfo(url)
    # A service definition can supply a host of its own.
    if conninfo is None or "service" in conninfo:
        return False
    hosts = conninfo.get("host", "").split(",")
    addrs = [addr for addr in conninfo.get("hostaddr", "").split(",") if addr]
    return all(h.startswith("/") or is_loopback_host(h) for h in hosts) and all(map(is_loopback_host, addrs))


# libpq reads these from the environment, and either one can send a connection
# elsewhere even when the connection string names a local host.
_LIBPQ_REDIRECT_ENV = ("PGHOSTADDR", "PGSERVICE")

# Used only when SPOTIFY_REDIRECT_URI is unset: the development address, as
# Spotify accepts it. Spotify refuses a ``localhost`` redirect URI; HTTP is
# allowed only on a loopback IP literal.
DEFAULT_SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8000/callback"


class Config:
    """Base configuration."""

    SECRET_KEY = os.environ.get("SECRET_KEY")

    # Dedicated Fernet key for refresh-token encryption. When unset,
    # the key is derived from SECRET_KEY (legacy scheme — rotating
    # SECRET_KEY then invalidates all stored tokens). FALLBACKS is a
    # comma-separated list of retired keys accepted for decryption
    # during a key-rotation window.
    TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY")
    TOKEN_ENCRYPTION_KEY_FALLBACKS = _split_csv(
        os.environ.get("TOKEN_ENCRYPTION_KEY_FALLBACKS", "")
    )

    SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "shuffify_session")
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
    # The redirect URI must match the one set in your Spotify Developer Dashboard
    SPOTIFY_REDIRECT_URI = os.getenv(
        "SPOTIFY_REDIRECT_URI", DEFAULT_SPOTIFY_REDIRECT_URI
    )

    # Redis configuration
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Session configuration - Redis-based storage
    SESSION_TYPE = "redis"
    SESSION_PERMANENT = False
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour
    SESSION_KEY_PREFIX = "shuffify:session:"

    # Session security settings for OAuth
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"  # More permissive for OAuth flows
    SESSION_COOKIE_SECURE = True

    # The largest body accepted other than a form, which is held to Flask's
    # MAX_FORM_MEMORY_SIZE (shuffify.BoundedRequest). A body that declares a
    # bigger length gets a 413 before it is read; one sent without a length
    # (chunked) is cut off at the limit. The largest legitimate body is the
    # Workshop's preview shuffle, a full object per track: 10,000 tracks with
    # long titles are 5.3 MiB.
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024

    # Redis caching configuration for Spotify API responses
    CACHE_KEY_PREFIX = "shuffify:cache:"
    CACHE_DEFAULT_TTL = 300  # 5 minutes default TTL
    CACHE_PLAYLIST_TTL = 60  # 1 minute for playlist data (changes frequently)
    CACHE_USER_TTL = 600  # 10 minutes for user profile data
    CACHE_AUDIO_FEATURES_TTL = 86400  # 24 hours for audio features (rarely change)

    # Source resolver — HTTP timeout (seconds) for public Spotify scrapes.
    # Tunable per-environment so production can dial down latency budget
    # without code changes. Retry/backoff constants live in the pathway
    # module since they're stable algorithmic knobs, not ops controls.
    SOURCE_RESOLVER_TIMEOUT = int(os.getenv("SOURCE_RESOLVER_TIMEOUT", "10"))

    # Database configuration
    SQLALCHEMY_DATABASE_URI = _resolve_database_url("sqlite:///shuffify.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Schema management: whether the app applies Alembic migrations itself
    # during startup. False means something outside the app is expected to
    # have run them already, and the app factory only verifies the schema is
    # at head, refusing to serve if it is not.
    MIGRATE_ON_STARTUP = True

    # Scheduler configuration
    SCHEDULER_ENABLED = True
    SCHEDULER_THREAD_POOL_SIZE = int(os.getenv("SCHEDULER_THREAD_POOL_SIZE", "10"))

    # After a job's write fails with an unconfirmed outcome, its rollback
    # re-reads the restored playlist this many times, this many seconds
    # apart, and restores it again if the delayed write lands.
    ROLLBACK_SETTLE_CHECKS = 3
    ROLLBACK_SETTLE_SECONDS = 5

    # Application settings
    DEBUG = False
    PORT = int(os.getenv("PORT", 8000))
    HOST = os.getenv("HOST", "0.0.0.0")

    # Sentry observability (no-op when SENTRY_DSN is empty)
    SENTRY_DSN = os.getenv("SENTRY_DSN", "")
    SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "production")
    SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0"))
    SENTRY_PROFILES_SAMPLE_RATE = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0"))
    SENTRY_RELEASE = os.getenv("GIT_SHA", "")

    @classmethod
    def get_spotify_credentials(cls) -> dict:
        return {
            "client_id": cls.SPOTIFY_CLIENT_ID,
            "client_secret": cls.SPOTIFY_CLIENT_SECRET,
            "redirect_uri": cls.SPOTIFY_REDIRECT_URI,
        }


class ProdConfig(Config):
    """Production configuration."""

    CONFIG_NAME = "production"
    SECRET_KEY = os.environ.get("SECRET_KEY")  # No fallback — validated on startup
    DEBUG = False
    TESTING = False

    # Migrations are not applied at startup; apply them as a separate release
    # step (``flask db upgrade``) before the app starts.
    MIGRATE_ON_STARTUP = False
    SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Production Redis settings - require REDIS_URL to be set
    REDIS_URL = os.getenv("REDIS_URL")  # Must be explicitly set in production

    # Production database - PostgreSQL
    SQLALCHEMY_DATABASE_URI = _resolve_database_url("sqlite:///shuffify.db")

    # PostgreSQL engine options: connection pooling and SSL for managed providers
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 5,
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "connect_args": (
            {"sslmode": "require"}
            if os.getenv("DATABASE_URL", "").startswith("postgres")
            else {}
        ),
    }


class DevConfig(Config):
    """Development configuration."""

    CONFIG_NAME = "development"
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    DEBUG = True
    TESTING = True
    SESSION_COOKIE_SECURE = False

    # Development Redis settings - fallback to localhost
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Development database - PostgreSQL if DATABASE_URL is set, else SQLite
    SQLALCHEMY_DATABASE_URI = _resolve_database_url("sqlite:///shuffify_dev.db")

    SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"


class TestConfig(Config):
    """Test configuration for isolated, in-memory test runs.

    Uses SQLite in-memory so Flask-SQLAlchemy auto-configures StaticPool,
    ensuring all connections share the same database within a test.
    """

    CONFIG_NAME = "testing"
    SECRET_KEY = "test-secret-key"
    # Pinned so tests are hermetic regardless of developer env: never an
    # exported key, Redis or Sentry project.
    TOKEN_ENCRYPTION_KEY = None
    TOKEN_ENCRYPTION_KEY_FALLBACKS = []
    REDIS_URL = "redis://localhost:6379/0"
    SENTRY_DSN = ""
    TESTING = True
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SCHEDULER_ENABLED = False
    # Unit tests that roll back an unconfirmed write don't wait; the settle tests turn it on.
    ROLLBACK_SETTLE_CHECKS = 0


class HarnessConfig(Config):
    """Local end-to-end test harness (scripts/harness.sh).

    Production-shaped where it matters: PostgreSQL, Redis, CSRF on, the
    scheduler running, a new database built from the models by
    ``flask create-schema``, and migrations applied by ``flask db upgrade`` before
    the app starts, with the app factory only verifying the schema is at head
    -- the path ProdConfig expects. Unlike DevConfig it does
    not set TESTING, so it never falls back to ``db.create_all()``.

    Everything it talks to must be on this machine. ``assert_safe`` enforces
    that before the app is built.
    """

    CONFIG_NAME = "harness"
    SECRET_KEY = os.getenv("SECRET_KEY")  # generated per checkout by harness.sh
    DEBUG = False
    TESTING = False
    MIGRATE_ON_STARTUP = False
    # Served over plain http://localhost.
    SESSION_COOKIE_SECURE = False
    # No defaults: harness.sh always sets both, and a missing value must fail
    # assert_safe rather than fall back to something nobody chose.
    REDIS_URL = os.getenv("REDIS_URL", "")
    SQLALCHEMY_DATABASE_URI = _resolve_database_url("")
    SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"

    @classmethod
    def assert_safe(cls) -> None:
        """Refuse to run unless every external dependency is local and fake."""
        problems = []
        if not _is_local_database_url(cls.SQLALCHEMY_DATABASE_URI):
            problems.append("DATABASE_URL must be a PostgreSQL (psycopg2) URL whose every libpq host is on localhost")
        if any(os.environ.get(name) for name in _LIBPQ_REDIRECT_ENV):
            problems.append(f"{'/'.join(_LIBPQ_REDIRECT_ENV)} must not be set (libpq would connect where they say)")
        if not cls.REDIS_URL.startswith("redis://") or not _is_loopback_url(cls.REDIS_URL):
            problems.append("REDIS_URL must be a redis:// URL on localhost")
        if cls.SENTRY_DSN:
            problems.append("SENTRY_DSN must be empty (harness errors must not reach Sentry)")
        if cls.SPOTIFY_CLIENT_ID != HARNESS_FAKE_CLIENT_ID or cls.SPOTIFY_CLIENT_SECRET != HARNESS_FAKE_CLIENT_SECRET:
            problems.append("Spotify client credentials must be the harness fakes, never real ones")
        # The base the Spotify client will actually use, resolved from
        # SPOTIFY_BASE_URL at import.
        from shuffify.spotify import endpoints

        if not _is_loopback_url(endpoints.API_BASE_URL):
            problems.append("SPOTIFY_BASE_URL must point at the local fake Spotify")
        if not cls.SECRET_KEY:
            problems.append("SECRET_KEY must be set")
        # A harness run exists to catch schema drift, so the break-glass
        # override that tolerates it has no place here. Read exactly as the
        # app factory reads it.
        from shuffify import SCHEMA_DRIFT_OVERRIDE_VAR, _env_is_true

        if _env_is_true(SCHEMA_DRIFT_OVERRIDE_VAR):
            problems.append(f"{SCHEMA_DRIFT_OVERRIDE_VAR} must not be set")
        if problems:
            raise HarnessSafetyError("Refusing to start the harness config: " + "; ".join(problems))


# Dictionary for easy config selection
config = {
    "development": DevConfig,
    "production": ProdConfig,
    "testing": TestConfig,
    "harness": HarnessConfig,
    "default": DevConfig,
}
