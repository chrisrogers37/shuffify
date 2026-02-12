import os
import atexit
import logging
from typing import Optional
from flask import Flask
from flask_session import Session
import redis
from flask_migrate import Migrate
from config import config, validate_required_env_vars

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Global Redis client for caching (initialized in create_app)
_redis_client: Optional[redis.Redis] = None
_migrate: Optional[Migrate] = None


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


def create_app(config_name=None):
    """Create and configure the Flask application."""
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "production")

    # Ensure config_name is a string
    if not isinstance(config_name, str):
        config_name = "production"  # Default to production if not a string

    logger.info("Creating app with config: %s", config_name)

    # Validate required environment variables
    try:
        validate_required_env_vars()
        logger.info("Environment validation passed")
    except ValueError as e:
        logger.error("Environment validation failed: %s", str(e))
        if config_name == "production":
            raise  # Fail fast in production
        else:
            logger.warning(
                "Continuing in development mode with missing environment variables"
            )

    app = Flask(__name__)

    # Load config
    app.config.from_object(config[config_name])

    # Log important config values
    logger.info("SPOTIFY_REDIRECT_URI: %s", app.config.get("SPOTIFY_REDIRECT_URI"))
    logger.info("CONFIG_NAME: %s", app.config.get("CONFIG_NAME", config_name))

    # Configure Redis for session storage and caching
    global _redis_client
    redis_url = app.config.get("REDIS_URL")
    if redis_url:
        try:
            redis_client = _create_redis_client(redis_url)
            # Test the connection
            redis_client.ping()
            app.config["SESSION_REDIS"] = redis_client
            _redis_client = redis_client
            logger.info(
                "Redis session storage configured: %s", redis_url.split("@")[-1]
            )
            logger.info("Redis caching enabled")
        except redis.ConnectionError as e:
            logger.warning(
                "Redis connection failed: %s. Falling back to filesystem sessions.", e
            )
            app.config["SESSION_TYPE"] = "filesystem"
            app.config["SESSION_FILE_DIR"] = "./.flask_session/"
            os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)
            _redis_client = None
    else:
        logger.warning("REDIS_URL not configured. Using filesystem sessions.")
        app.config["SESSION_TYPE"] = "filesystem"
        app.config["SESSION_FILE_DIR"] = "./.flask_session/"
        os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)
        _redis_client = None

    # Initialize Flask-Session
    Session(app)

    # Initialize token encryption service
    from shuffify.services.token_service import TokenService

    try:
        TokenService.initialize(app.config["SECRET_KEY"])
        logger.info("Token encryption service initialized")
    except Exception as e:
        logger.warning(
            "Token encryption init failed: %s. "
            "Scheduled operations will be unavailable.",
            e,
        )

    # Initialize SQLAlchemy database
    try:
        from shuffify.models.db import db

        db.init_app(app)

        global _migrate
        _migrate = Migrate(app, db)

        with app.app_context():
            if app.config.get("TESTING"):
                # Tests use in-memory SQLite -- create tables directly
                db.create_all()
            else:
                # Development and production: use Alembic migrations.
                # In development without migrations dir, fall back to
                # db.create_all() for convenience.
                migrations_dir = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)),
                    'migrations'
                )
                if os.path.isdir(migrations_dir):
                    from flask_migrate import upgrade
                    upgrade()
                else:
                    logger.warning(
                        "No migrations/ directory found. "
                        "Using db.create_all() as fallback. "
                        "Run 'flask db init && flask db migrate' "
                        "to set up Alembic migrations."
                    )
                    db.create_all()

        logger.info(
            "SQLAlchemy database initialized: %s",
            app.config.get("SQLALCHEMY_DATABASE_URI", "not set"),
        )
    except Exception as e:
        logger.warning(
            "Database initialization failed: %s. "
            "Persistence features will be unavailable.",
            e,
        )

    # Register blueprints
    from shuffify.routes import main as main_blueprint

    app.register_blueprint(main_blueprint)

    # Register global error handlers
    from shuffify.error_handlers import register_error_handlers

    register_error_handlers(app)

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

    # Add a `no-cache` header to responses in development mode. This prevents
    # the browser from caching assets and not showing changes.
    if app.debug:

        @app.after_request
        def after_request(response):
            response.headers["Cache-Control"] = (
                "no-cache, no-store, must-revalidate, public, max-age=0"
            )
            response.headers["Expires"] = 0
            response.headers["Pragma"] = "no-cache"
            return response

    return app
