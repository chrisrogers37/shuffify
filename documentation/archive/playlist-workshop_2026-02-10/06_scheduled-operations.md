# Phase 6: Scheduled Operations

**PR Title:** `feat: Add APScheduler for automated playlist raid and shuffle operations`

**PR:** #48 (merged)

**Risk Level:** High -- introduces background threading, encrypted token storage, and autonomous Spotify API access without user interaction. A bug in the scheduler or token management can cause silent failures, stale playlists, or exhausted rate limits.

**Estimated Effort:** 5-7 days for a mid-level engineer, 8-10 days for a junior engineer.

**Files Created:**
- `shuffify/scheduler.py` -- APScheduler initialization and lifecycle management
- `shuffify/services/token_service.py` -- Fernet encryption/decryption for refresh tokens
- `shuffify/services/scheduler_service.py` -- CRUD operations for Schedule model
- `shuffify/services/job_executor_service.py` -- Business logic that executes scheduled jobs
- `shuffify/schemas/schedule_requests.py` -- Pydantic validation for schedule create/edit
- `shuffify/templates/schedules.html` -- Schedule management UI page
- `tests/services/test_token_service.py` -- Token encryption tests
- `tests/services/test_scheduler_service.py` -- Schedule CRUD tests
- `tests/services/test_job_executor_service.py` -- Job execution tests

**Files Modified:**
- `shuffify/models/db.py` -- Add `Schedule` and `JobExecution` models; add `encrypted_refresh_token` column to `User` model
- `shuffify/__init__.py` -- Initialize APScheduler in app factory
- `shuffify/routes.py` -- Add schedule management routes section
- `shuffify/services/__init__.py` -- Export new services and exceptions
- `shuffify/schemas/__init__.py` -- Export new schedule schemas
- `shuffify/error_handlers.py` -- Add error handlers for schedule exceptions
- `shuffify/templates/dashboard.html` -- Add "Schedules" link in user header bar
- `config.py` -- Add scheduler configuration settings
- `requirements/base.txt` -- Add `APScheduler>=3.10`
- `run.py` -- Guard scheduler startup in development mode (Werkzeug reloader)
- `CHANGELOG.md` -- Add entry under `[Unreleased]`

**Files Deleted:** None

---

## Context

Phases 1-4 give users interactive tools for playlist editing and source management. Phase 5 introduces SQLite persistence with User, WorkshopSession, and UpstreamSource models. However, all operations remain manual -- users must log in and click buttons.

Phase 6 adds automation: users configure scheduled jobs that run in the background, pulling tracks from upstream sources ("raid"), shuffling playlists, or both. This is particularly valuable for users who maintain curated playlists that should regularly incorporate new tracks from source playlists.

The key challenge is that background jobs need Spotify API access without a user session. This requires securely storing refresh tokens in the database and using them to obtain fresh access tokens at execution time.

---

## Dependencies

**Phase 5 must be merged first.** This phase depends on:
1. **Flask-SQLAlchemy** initialized in the app factory (`shuffify/__init__.py`)
2. **Flask-Migrate** for schema migrations
3. **User model** (`shuffify/models/db.py`) -- Phase 6 adds `encrypted_refresh_token` column
4. **UpstreamSource model** (`shuffify/models/db.py`) -- used by raid jobs to know which sources to pull from
5. **UserService** (`shuffify/services/user_service.py`) -- for looking up users by Spotify ID

**New Python dependencies:**
- `APScheduler>=3.10` -- Background job scheduling with SQLAlchemy job store
- `cryptography>=43.0.1` -- Already in `requirements/base.txt` (for Fernet encryption)

**No new CDN dependencies** for the frontend.

---

## Detailed Implementation Plan

### Step 1: Add Scheduler Configuration to `config.py`

**File:** `/Users/chris/Projects/shuffify/config.py`

**Where:** Add scheduler settings to the base `Config` class (after line 41, after the caching configuration block). Also add environment-specific overrides in `DevConfig` and `ProdConfig`.

**Add to `Config` class (after line 41):**

```python
    # Scheduler configuration
    SCHEDULER_ENABLED = True
    SCHEDULER_MAX_SCHEDULES_PER_USER = 5
    SCHEDULER_JOB_STORE_URL = os.getenv(
        'SCHEDULER_JOB_STORE_URL',
        'sqlite:///shuffify_jobs.db'
    )
    # Fernet encryption key derived from SECRET_KEY
    # Fernet requires a 32-byte URL-safe base64-encoded key.
    # We derive it from SECRET_KEY using PBKDF2.
    FERNET_KEY = None  # Computed at app startup in __init__.py
```

**Add to `DevConfig` class (after line 79):**

```python
    SCHEDULER_ENABLED = os.getenv('SCHEDULER_ENABLED', 'true').lower() == 'true'
```

**Add to `ProdConfig` class (after line 67):**

```python
    SCHEDULER_ENABLED = os.getenv('SCHEDULER_ENABLED', 'true').lower() == 'true'
```

### Step 2: Create the Token Service

**File:** `/Users/chris/Projects/shuffify/shuffify/services/token_service.py`

This service handles Fernet encryption and decryption of refresh tokens. It derives a Fernet key from the app's `SECRET_KEY` using PBKDF2 so that we do not require a separate encryption key.

```python
"""
Token encryption service for secure refresh token storage.

Uses Fernet symmetric encryption with a key derived from the app's
SECRET_KEY via PBKDF2. This ensures refresh tokens are encrypted
at rest in the SQLite database.
"""

import base64
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# Fixed salt for key derivation. Changing this invalidates all stored tokens.
# This is acceptable because the SECRET_KEY itself provides the entropy.
_SALT = b"shuffify-refresh-token-encryption-v1"


class TokenEncryptionError(Exception):
    """Raised when token encryption or decryption fails."""

    pass


class TokenService:
    """Service for encrypting and decrypting Spotify refresh tokens."""

    _fernet: Optional[Fernet] = None

    @classmethod
    def initialize(cls, secret_key: str) -> None:
        """
        Initialize the Fernet cipher from the app's SECRET_KEY.

        Must be called once during app startup (in create_app).

        Args:
            secret_key: The Flask app's SECRET_KEY string.

        Raises:
            TokenEncryptionError: If key derivation fails.
        """
        if not secret_key:
            raise TokenEncryptionError(
                "SECRET_KEY is required for token encryption"
            )

        try:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=_SALT,
                iterations=480_000,
            )
            key = base64.urlsafe_b64encode(
                kdf.derive(secret_key.encode("utf-8"))
            )
            cls._fernet = Fernet(key)
            logger.info("TokenService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize TokenService: {e}")
            raise TokenEncryptionError(
                f"Failed to derive encryption key: {e}"
            )

    @classmethod
    def encrypt_token(cls, plaintext_token: str) -> str:
        """
        Encrypt a refresh token for database storage.

        Args:
            plaintext_token: The plaintext Spotify refresh token.

        Returns:
            Base64-encoded encrypted token string.

        Raises:
            TokenEncryptionError: If encryption fails or service not initialized.
        """
        if cls._fernet is None:
            raise TokenEncryptionError(
                "TokenService not initialized. Call initialize() first."
            )

        if not plaintext_token:
            raise TokenEncryptionError("Cannot encrypt empty token")

        try:
            encrypted = cls._fernet.encrypt(plaintext_token.encode("utf-8"))
            return encrypted.decode("utf-8")
        except Exception as e:
            logger.error(f"Token encryption failed: {e}")
            raise TokenEncryptionError(f"Encryption failed: {e}")

    @classmethod
    def decrypt_token(cls, encrypted_token: str) -> str:
        """
        Decrypt a refresh token retrieved from the database.

        Args:
            encrypted_token: The base64-encoded encrypted token string.

        Returns:
            The plaintext refresh token.

        Raises:
            TokenEncryptionError: If decryption fails, token is corrupted,
                or service not initialized.
        """
        if cls._fernet is None:
            raise TokenEncryptionError(
                "TokenService not initialized. Call initialize() first."
            )

        if not encrypted_token:
            raise TokenEncryptionError("Cannot decrypt empty token")

        try:
            decrypted = cls._fernet.decrypt(encrypted_token.encode("utf-8"))
            return decrypted.decode("utf-8")
        except InvalidToken:
            logger.error("Token decryption failed: invalid token or wrong key")
            raise TokenEncryptionError(
                "Decryption failed: token is corrupted or SECRET_KEY changed"
            )
        except Exception as e:
            logger.error(f"Token decryption failed: {e}")
            raise TokenEncryptionError(f"Decryption failed: {e}")

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if the TokenService has been initialized."""
        return cls._fernet is not None
```

### Step 3: Add Database Models

**File:** `/Users/chris/Projects/shuffify/shuffify/models/db.py`

Phase 5 creates this file with `User`, `WorkshopSession`, and `UpstreamSource` models. Phase 6 adds two changes:

**3a. Add `encrypted_refresh_token` column to `User` model:**

Add after the existing User columns (after the `spotify_display_name` column or whichever is last):

```python
    # Encrypted Spotify refresh token for background job execution.
    # Encrypted with Fernet using a key derived from SECRET_KEY.
    # Set during OAuth callback, updated on token refresh.
    encrypted_refresh_token = db.Column(db.Text, nullable=True)
```

**3b. Add `Schedule` model (after the existing models):**

```python
class Schedule(db.Model):
    """
    A configured scheduled operation for a user.

    Each schedule defines a recurring job that runs automatically:
    - raid: Pull new tracks from upstream sources into a target playlist
    - shuffle: Run a shuffle algorithm on a target playlist
    - raid_and_shuffle: Pull new tracks then shuffle

    Attributes:
        id: Primary key.
        user_id: FK to User who owns this schedule.
        job_type: One of 'raid', 'shuffle', 'raid_and_shuffle'.
        target_playlist_id: Spotify playlist ID to operate on.
        target_playlist_name: Display name (cached, may become stale).
        source_playlist_ids: JSON list of Spotify playlist IDs to raid from.
        algorithm_name: Shuffle algorithm class name (e.g. 'BasicShuffle').
        algorithm_params: JSON dict of algorithm parameters.
        schedule_type: One of 'interval' or 'cron'.
        schedule_value: For interval: 'daily', 'weekly', 'every_12h'.
                        For cron: cron expression string.
        is_enabled: Whether the schedule is active.
        last_run_at: Timestamp of last execution.
        last_status: Result of last execution ('success', 'failed', 'skipped').
        last_error: Error message from last failed execution.
        created_at: When the schedule was created.
        updated_at: When the schedule was last modified.
    """

    __tablename__ = "schedules"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    job_type = db.Column(
        db.String(20), nullable=False
    )  # 'raid', 'shuffle', 'raid_and_shuffle'
    target_playlist_id = db.Column(db.String(64), nullable=False)
    target_playlist_name = db.Column(db.String(255), nullable=True)
    source_playlist_ids = db.Column(
        db.JSON, nullable=True, default=list
    )  # List of playlist IDs
    algorithm_name = db.Column(
        db.String(64), nullable=True
    )  # Required for shuffle/raid_and_shuffle
    algorithm_params = db.Column(
        db.JSON, nullable=True, default=dict
    )  # Algorithm parameters
    schedule_type = db.Column(
        db.String(10), nullable=False, default="interval"
    )  # 'interval' or 'cron'
    schedule_value = db.Column(
        db.String(100), nullable=False, default="daily"
    )  # 'daily', 'weekly', 'every_12h', or cron expr
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    last_status = db.Column(
        db.String(20), nullable=True
    )  # 'success', 'failed', 'skipped'
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=db.func.now()
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, default=db.func.now(), onupdate=db.func.now()
    )

    # Relationships
    user = db.relationship("User", backref=db.backref("schedules", lazy="dynamic"))

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "job_type": self.job_type,
            "target_playlist_id": self.target_playlist_id,
            "target_playlist_name": self.target_playlist_name,
            "source_playlist_ids": self.source_playlist_ids or [],
            "algorithm_name": self.algorithm_name,
            "algorithm_params": self.algorithm_params or {},
            "schedule_type": self.schedule_type,
            "schedule_value": self.schedule_value,
            "is_enabled": self.is_enabled,
            "last_run_at": (
                self.last_run_at.isoformat() if self.last_run_at else None
            ),
            "last_status": self.last_status,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return (
            f"<Schedule {self.id}: {self.job_type} on {self.target_playlist_name} "
            f"({self.schedule_value}, {'enabled' if self.is_enabled else 'disabled'})>"
        )
```

**3c. Add `JobExecution` model (after Schedule):**

```python
class JobExecution(db.Model):
    """
    Record of a single job execution for audit/history.

    Attributes:
        id: Primary key.
        schedule_id: FK to the Schedule that was executed.
        started_at: When execution began.
        completed_at: When execution finished.
        status: 'success', 'failed', 'skipped'.
        tracks_added: Number of new tracks added (for raid jobs).
        tracks_total: Total tracks in playlist after execution.
        error_message: Error details if failed.
    """

    __tablename__ = "job_executions"

    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(
        db.Integer, db.ForeignKey("schedules.id"), nullable=False, index=True
    )
    started_at = db.Column(db.DateTime, nullable=False, default=db.func.now())
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(
        db.String(20), nullable=False, default="running"
    )  # 'running', 'success', 'failed', 'skipped'
    tracks_added = db.Column(db.Integer, nullable=True, default=0)
    tracks_total = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    # Relationships
    schedule = db.relationship(
        "Schedule", backref=db.backref("executions", lazy="dynamic")
    )

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "schedule_id": self.schedule_id,
            "started_at": (
                self.started_at.isoformat() if self.started_at else None
            ),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "status": self.status,
            "tracks_added": self.tracks_added,
            "tracks_total": self.tracks_total,
            "error_message": self.error_message,
        }

    def __repr__(self):
        return (
            f"<JobExecution {self.id}: schedule={self.schedule_id} "
            f"status={self.status}>"
        )
```

### Step 4: Create the Scheduler Module

**File:** `/Users/chris/Projects/shuffify/shuffify/scheduler.py`

This module manages APScheduler lifecycle. It initializes the `BackgroundScheduler` with an `SQLAlchemyJobStore`, registers jobs from the database on startup, and provides functions to add/remove/pause jobs.

```python
"""
APScheduler integration for Shuffify.

Manages the background scheduler lifecycle: initialization, job registration,
and graceful shutdown. Jobs persist in SQLite via SQLAlchemyJobStore.

IMPORTANT:
- Must be initialized AFTER Flask app creation (in create_app).
- In development with Werkzeug reloader, only start in the main process.
- In production with Gunicorn, use --preload to ensure single scheduler.
"""

import logging
import os
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[BackgroundScheduler] = None


def _on_job_executed(event):
    """Listener for successful job execution."""
    logger.info(f"Job {event.job_id} executed successfully")


def _on_job_error(event):
    """Listener for failed job execution."""
    logger.error(
        f"Job {event.job_id} failed with exception: {event.exception}",
        exc_info=event.traceback,
    )


def _on_job_missed(event):
    """Listener for missed job execution (scheduler was down)."""
    logger.warning(f"Job {event.job_id} missed its scheduled run time")


def init_scheduler(app) -> Optional[BackgroundScheduler]:
    """
    Initialize and start the APScheduler BackgroundScheduler.

    Must be called from create_app() after all extensions are initialized.

    Args:
        app: The Flask application instance.

    Returns:
        The BackgroundScheduler instance, or None if disabled/skipped.
    """
    global _scheduler

    if not app.config.get("SCHEDULER_ENABLED", True):
        logger.info("Scheduler disabled by configuration")
        return None

    # In development, Werkzeug reloader spawns two processes.
    # Only start the scheduler in the main process (WERKZEUG_RUN_MAIN).
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        logger.info(
            "Skipping scheduler init in Werkzeug reloader child process"
        )
        return None

    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running, skipping re-init")
        return _scheduler

    try:
        # Use the same SQLite database as the app for job storage.
        # Phase 5 sets DATABASE_URL in config; we reuse it.
        db_url = app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///shuffify.db")

        jobstores = {
            "default": SQLAlchemyJobStore(url=db_url),
        }
        executors = {
            "default": ThreadPoolExecutor(max_workers=3),
        }
        job_defaults = {
            "coalesce": True,  # If multiple runs were missed, only run once
            "max_instances": 1,  # Only one instance of each job at a time
            "misfire_grace_time": 3600,  # Allow 1 hour of misfire grace
        }

        _scheduler = BackgroundScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
        )

        # Add event listeners
        _scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)
        _scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
        _scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)

        _scheduler.start()
        logger.info("APScheduler started successfully")

        # Register existing enabled schedules from database
        with app.app_context():
            _register_existing_jobs(app)

        return _scheduler

    except Exception as e:
        logger.error(f"Failed to initialize scheduler: {e}", exc_info=True)
        _scheduler = None
        return None


def _register_existing_jobs(app):
    """
    Load enabled schedules from the database and register them with APScheduler.

    Called once during startup.

    Args:
        app: The Flask application instance.
    """
    try:
        from shuffify.models.db import Schedule

        enabled_schedules = Schedule.query.filter_by(is_enabled=True).all()
        registered = 0

        for schedule in enabled_schedules:
            try:
                add_job_for_schedule(schedule, app)
                registered += 1
            except Exception as e:
                logger.error(
                    f"Failed to register job for schedule {schedule.id}: {e}"
                )

        logger.info(
            f"Registered {registered}/{len(enabled_schedules)} "
            f"scheduled jobs from database"
        )

    except Exception as e:
        logger.error(f"Failed to load schedules from database: {e}")


def get_scheduler() -> Optional[BackgroundScheduler]:
    """Get the global scheduler instance."""
    return _scheduler


def add_job_for_schedule(schedule, app):
    """
    Register an APScheduler job for a Schedule model instance.

    Args:
        schedule: A Schedule model instance.
        app: The Flask application instance (needed for app context in job).

    Raises:
        RuntimeError: If scheduler is not initialized.
    """
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized")

    job_id = f"schedule_{schedule.id}"

    # Remove existing job if present (for updates)
    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass  # Job didn't exist, that's fine

    # Parse schedule_value into APScheduler trigger
    trigger, trigger_kwargs = _parse_schedule(
        schedule.schedule_type, schedule.schedule_value
    )

    _scheduler.add_job(
        func=_execute_scheduled_job,
        trigger=trigger,
        id=job_id,
        args=[app, schedule.id],
        replace_existing=True,
        **trigger_kwargs,
    )

    logger.info(
        f"Registered job {job_id} with trigger={trigger}, "
        f"kwargs={trigger_kwargs}"
    )


def remove_job_for_schedule(schedule_id: int):
    """
    Remove an APScheduler job for a schedule.

    Args:
        schedule_id: The Schedule model ID.
    """
    if _scheduler is None:
        return

    job_id = f"schedule_{schedule_id}"
    try:
        _scheduler.remove_job(job_id)
        logger.info(f"Removed job {job_id}")
    except Exception:
        logger.debug(f"Job {job_id} not found in scheduler (already removed)")


def _parse_schedule(schedule_type: str, schedule_value: str):
    """
    Convert schedule configuration to APScheduler trigger arguments.

    Args:
        schedule_type: 'interval' or 'cron'.
        schedule_value: The schedule specification.

    Returns:
        Tuple of (trigger_type_string, trigger_kwargs_dict).
    """
    if schedule_type == "interval":
        interval_map = {
            "every_6h": {"hours": 6},
            "every_12h": {"hours": 12},
            "daily": {"days": 1},
            "every_3d": {"days": 3},
            "weekly": {"weeks": 1},
        }
        kwargs = interval_map.get(schedule_value, {"days": 1})
        return "interval", kwargs

    elif schedule_type == "cron":
        # Parse cron expression: "minute hour day month day_of_week"
        parts = schedule_value.split()
        if len(parts) != 5:
            logger.warning(
                f"Invalid cron expression '{schedule_value}', "
                f"defaulting to daily at midnight"
            )
            return "cron", {"hour": 0, "minute": 0}

        return "cron", {
            "minute": parts[0],
            "hour": parts[1],
            "day": parts[2],
            "month": parts[3],
            "day_of_week": parts[4],
        }

    else:
        logger.warning(
            f"Unknown schedule_type '{schedule_type}', defaulting to daily"
        )
        return "interval", {"days": 1}


def _execute_scheduled_job(app, schedule_id: int):
    """
    Wrapper that executes a scheduled job within Flask app context.

    This is the function registered with APScheduler. It pushes an
    app context so that database access and Flask config are available.

    Args:
        app: The Flask application instance.
        schedule_id: The Schedule model ID to execute.
    """
    with app.app_context():
        try:
            from shuffify.services.job_executor_service import (
                JobExecutorService,
            )

            JobExecutorService.execute(schedule_id)
        except Exception as e:
            logger.error(
                f"Scheduled job for schedule {schedule_id} failed: {e}",
                exc_info=True,
            )


def shutdown_scheduler():
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
    _scheduler = None
```

### Step 5: Create the Scheduler Service (CRUD)

**File:** `/Users/chris/Projects/shuffify/shuffify/services/scheduler_service.py`

```python
"""
Scheduler service for CRUD operations on Schedule models.

Handles creating, reading, updating, and deleting scheduled job
configurations. Does NOT execute jobs -- that is handled by
JobExecutorService.
"""

import logging
from typing import List, Optional, Dict, Any

from shuffify.models.db import db, Schedule

logger = logging.getLogger(__name__)


class ScheduleError(Exception):
    """Base exception for schedule operations."""

    pass


class ScheduleNotFoundError(ScheduleError):
    """Raised when a schedule is not found."""

    pass


class ScheduleLimitError(ScheduleError):
    """Raised when user exceeds max schedule limit."""

    pass


class SchedulerService:
    """Service for managing scheduled job configurations."""

    MAX_SCHEDULES_PER_USER = 5

    @staticmethod
    def get_user_schedules(user_id: int) -> List[Schedule]:
        """
        Get all schedules for a user.

        Args:
            user_id: The database user ID.

        Returns:
            List of Schedule model instances.
        """
        schedules = (
            Schedule.query.filter_by(user_id=user_id)
            .order_by(Schedule.created_at.desc())
            .all()
        )
        logger.debug(f"Retrieved {len(schedules)} schedules for user {user_id}")
        return schedules

    @staticmethod
    def get_schedule(schedule_id: int, user_id: int) -> Schedule:
        """
        Get a single schedule, verifying ownership.

        Args:
            schedule_id: The schedule ID.
            user_id: The user ID (for ownership check).

        Returns:
            Schedule model instance.

        Raises:
            ScheduleNotFoundError: If not found or not owned by user.
        """
        schedule = Schedule.query.filter_by(
            id=schedule_id, user_id=user_id
        ).first()

        if not schedule:
            raise ScheduleNotFoundError(
                f"Schedule {schedule_id} not found for user {user_id}"
            )

        return schedule

    @staticmethod
    def create_schedule(
        user_id: int,
        job_type: str,
        target_playlist_id: str,
        target_playlist_name: str,
        schedule_type: str,
        schedule_value: str,
        source_playlist_ids: Optional[List[str]] = None,
        algorithm_name: Optional[str] = None,
        algorithm_params: Optional[Dict[str, Any]] = None,
    ) -> Schedule:
        """
        Create a new scheduled job.

        Args:
            user_id: The database user ID.
            job_type: 'raid', 'shuffle', or 'raid_and_shuffle'.
            target_playlist_id: Spotify playlist ID to operate on.
            target_playlist_name: Display name for the playlist.
            schedule_type: 'interval' or 'cron'.
            schedule_value: Schedule specification string.
            source_playlist_ids: List of source playlist IDs (for raid).
            algorithm_name: Shuffle algorithm name (for shuffle).
            algorithm_params: Algorithm parameter dict (for shuffle).

        Returns:
            The created Schedule model instance.

        Raises:
            ScheduleLimitError: If user has reached max schedules.
            ScheduleError: If creation fails.
        """
        # Check schedule limit
        existing_count = Schedule.query.filter_by(user_id=user_id).count()
        if existing_count >= SchedulerService.MAX_SCHEDULES_PER_USER:
            raise ScheduleLimitError(
                f"Maximum of {SchedulerService.MAX_SCHEDULES_PER_USER} "
                f"schedules per user reached"
            )

        try:
            schedule = Schedule(
                user_id=user_id,
                job_type=job_type,
                target_playlist_id=target_playlist_id,
                target_playlist_name=target_playlist_name,
                source_playlist_ids=source_playlist_ids or [],
                algorithm_name=algorithm_name,
                algorithm_params=algorithm_params or {},
                schedule_type=schedule_type,
                schedule_value=schedule_value,
                is_enabled=True,
            )

            db.session.add(schedule)
            db.session.commit()

            logger.info(
                f"Created schedule {schedule.id} for user {user_id}: "
                f"{job_type} on {target_playlist_name}"
            )
            return schedule

        except ScheduleLimitError:
            raise
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to create schedule: {e}", exc_info=True)
            raise ScheduleError(f"Failed to create schedule: {e}")

    @staticmethod
    def update_schedule(
        schedule_id: int,
        user_id: int,
        **kwargs,
    ) -> Schedule:
        """
        Update an existing schedule.

        Only the provided keyword arguments are updated.

        Args:
            schedule_id: The schedule ID.
            user_id: The user ID (for ownership check).
            **kwargs: Fields to update.

        Returns:
            The updated Schedule model instance.

        Raises:
            ScheduleNotFoundError: If not found.
            ScheduleError: If update fails.
        """
        schedule = SchedulerService.get_schedule(schedule_id, user_id)

        allowed_fields = {
            "job_type",
            "target_playlist_id",
            "target_playlist_name",
            "source_playlist_ids",
            "algorithm_name",
            "algorithm_params",
            "schedule_type",
            "schedule_value",
            "is_enabled",
        }

        try:
            for key, value in kwargs.items():
                if key in allowed_fields:
                    setattr(schedule, key, value)

            db.session.commit()
            logger.info(f"Updated schedule {schedule_id}")
            return schedule

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to update schedule {schedule_id}: {e}",
                exc_info=True,
            )
            raise ScheduleError(f"Failed to update schedule: {e}")

    @staticmethod
    def delete_schedule(schedule_id: int, user_id: int) -> None:
        """
        Delete a schedule and its execution history.

        Args:
            schedule_id: The schedule ID.
            user_id: The user ID (for ownership check).

        Raises:
            ScheduleNotFoundError: If not found.
            ScheduleError: If deletion fails.
        """
        schedule = SchedulerService.get_schedule(schedule_id, user_id)

        try:
            # Delete execution history first (cascade would also work)
            from shuffify.models.db import JobExecution

            JobExecution.query.filter_by(schedule_id=schedule_id).delete()

            db.session.delete(schedule)
            db.session.commit()

            logger.info(f"Deleted schedule {schedule_id}")

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to delete schedule {schedule_id}: {e}",
                exc_info=True,
            )
            raise ScheduleError(f"Failed to delete schedule: {e}")

    @staticmethod
    def toggle_schedule(schedule_id: int, user_id: int) -> Schedule:
        """
        Toggle a schedule's enabled/disabled state.

        Args:
            schedule_id: The schedule ID.
            user_id: The user ID (for ownership check).

        Returns:
            The updated Schedule.

        Raises:
            ScheduleNotFoundError: If not found.
        """
        schedule = SchedulerService.get_schedule(schedule_id, user_id)
        schedule.is_enabled = not schedule.is_enabled

        try:
            db.session.commit()
            logger.info(
                f"Schedule {schedule_id} "
                f"{'enabled' if schedule.is_enabled else 'disabled'}"
            )
            return schedule
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to toggle schedule {schedule_id}: {e}")
            raise ScheduleError(f"Failed to toggle schedule: {e}")

    @staticmethod
    def get_execution_history(
        schedule_id: int, user_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get recent execution history for a schedule.

        Args:
            schedule_id: The schedule ID.
            user_id: The user ID (for ownership check).
            limit: Maximum number of records to return.

        Returns:
            List of execution record dictionaries.
        """
        # Verify ownership
        SchedulerService.get_schedule(schedule_id, user_id)

        from shuffify.models.db import JobExecution

        executions = (
            JobExecution.query.filter_by(schedule_id=schedule_id)
            .order_by(JobExecution.started_at.desc())
            .limit(limit)
            .all()
        )

        return [ex.to_dict() for ex in executions]
```

### Step 6: Create the Job Executor Service

**File:** `/Users/chris/Projects/shuffify/shuffify/services/job_executor_service.py`

This is the core execution engine. It runs within Flask app context (pushed by the scheduler wrapper), retrieves the user's refresh token, creates a Spotify API client, and executes the configured operation.

```python
"""
Job executor service for running scheduled playlist operations.

Handles the actual execution of raid, shuffle, and combined jobs.
Uses encrypted refresh tokens to obtain Spotify API access without
user interaction.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from shuffify.models.db import db, Schedule, JobExecution, User
from shuffify.services.token_service import TokenService, TokenEncryptionError
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyTokenError,
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.shuffle_algorithms.registry import ShuffleRegistry

logger = logging.getLogger(__name__)


class JobExecutionError(Exception):
    """Raised when a scheduled job fails to execute."""

    pass


class JobExecutorService:
    """Service that executes scheduled playlist operations."""

    @staticmethod
    def execute(schedule_id: int) -> None:
        """
        Execute a scheduled job.

        This is the main entry point called by the scheduler.
        It handles all error scenarios and records the execution result.

        Args:
            schedule_id: The Schedule model ID to execute.
        """
        execution = None
        schedule = None

        try:
            # Load the schedule
            schedule = Schedule.query.get(schedule_id)
            if not schedule:
                logger.error(f"Schedule {schedule_id} not found, skipping")
                return

            if not schedule.is_enabled:
                logger.info(f"Schedule {schedule_id} is disabled, skipping")
                return

            # Create execution record
            execution = JobExecution(
                schedule_id=schedule_id,
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            db.session.add(execution)
            db.session.commit()

            # Load user and get API client
            user = User.query.get(schedule.user_id)
            if not user:
                raise JobExecutionError(
                    f"User {schedule.user_id} not found"
                )

            api = JobExecutorService._get_spotify_api(user)

            # Execute based on job type
            result = JobExecutorService._execute_job_type(
                schedule, api
            )

            # Record success
            execution.status = "success"
            execution.completed_at = datetime.now(timezone.utc)
            execution.tracks_added = result.get("tracks_added", 0)
            execution.tracks_total = result.get("tracks_total", 0)

            schedule.last_run_at = datetime.now(timezone.utc)
            schedule.last_status = "success"
            schedule.last_error = None

            db.session.commit()

            logger.info(
                f"Schedule {schedule_id} executed successfully: "
                f"added={result.get('tracks_added', 0)}, "
                f"total={result.get('tracks_total', 0)}"
            )

        except Exception as e:
            logger.error(
                f"Schedule {schedule_id} execution failed: {e}",
                exc_info=True,
            )
            # Record failure
            try:
                if execution:
                    execution.status = "failed"
                    execution.completed_at = datetime.now(timezone.utc)
                    execution.error_message = str(e)[:1000]

                if schedule:
                    schedule.last_run_at = datetime.now(timezone.utc)
                    schedule.last_status = "failed"
                    schedule.last_error = str(e)[:1000]

                db.session.commit()
            except Exception as db_err:
                logger.error(
                    f"Failed to record execution failure: {db_err}"
                )
                db.session.rollback()

    @staticmethod
    def execute_now(schedule_id: int, user_id: int) -> dict:
        """
        Manually trigger a schedule execution (from the UI).

        Unlike execute(), this verifies ownership and returns the result
        directly rather than recording it silently.

        Args:
            schedule_id: The Schedule model ID.
            user_id: The user ID for ownership verification.

        Returns:
            Dict with execution result.

        Raises:
            JobExecutionError: If execution fails.
        """
        from shuffify.services.scheduler_service import (
            SchedulerService,
            ScheduleNotFoundError,
        )

        try:
            schedule = SchedulerService.get_schedule(schedule_id, user_id)
        except ScheduleNotFoundError:
            raise JobExecutionError(
                f"Schedule {schedule_id} not found"
            )

        # Execute synchronously
        JobExecutorService.execute(schedule_id)

        # Reload to get updated status
        db.session.refresh(schedule)

        if schedule.last_status == "failed":
            raise JobExecutionError(
                f"Execution failed: {schedule.last_error}"
            )

        return {
            "status": schedule.last_status,
            "last_run_at": (
                schedule.last_run_at.isoformat()
                if schedule.last_run_at
                else None
            ),
        }

    @staticmethod
    def _get_spotify_api(user: "User") -> SpotifyAPI:
        """
        Create a SpotifyAPI client using the user's stored refresh token.

        Args:
            user: The User model instance.

        Returns:
            An authenticated SpotifyAPI instance.

        Raises:
            JobExecutionError: If token decryption or refresh fails.
        """
        if not user.encrypted_refresh_token:
            raise JobExecutionError(
                f"User {user.spotify_id} has no stored refresh token. "
                f"User must log in to enable scheduled operations."
            )

        try:
            # Decrypt the stored refresh token
            refresh_token = TokenService.decrypt_token(
                user.encrypted_refresh_token
            )
        except TokenEncryptionError as e:
            raise JobExecutionError(
                f"Failed to decrypt refresh token for user "
                f"{user.spotify_id}: {e}"
            )

        try:
            # Create auth manager and use refresh token to get access token
            from flask import current_app

            credentials = SpotifyCredentials.from_flask_config(
                current_app.config
            )
            auth_manager = SpotifyAuthManager(credentials)

            # Create a token_info with an expired access token and valid
            # refresh token. SpotifyAPI will auto-refresh on first call.
            token_info = TokenInfo(
                access_token="expired_placeholder",
                token_type="Bearer",
                expires_at=0,  # Force immediate refresh
                refresh_token=refresh_token,
            )

            api = SpotifyAPI(
                token_info,
                auth_manager,
                auto_refresh=True,
            )

            # Update stored refresh token if it was rotated
            new_token = api.token_info
            if new_token.refresh_token and new_token.refresh_token != refresh_token:
                user.encrypted_refresh_token = TokenService.encrypt_token(
                    new_token.refresh_token
                )
                db.session.commit()
                logger.info(
                    f"Updated rotated refresh token for user {user.spotify_id}"
                )

            return api

        except SpotifyTokenError as e:
            raise JobExecutionError(
                f"Failed to refresh Spotify token for user "
                f"{user.spotify_id}: {e}"
            )

    @staticmethod
    def _execute_job_type(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """
        Execute the appropriate operation based on job type.

        Args:
            schedule: The Schedule model instance.
            api: An authenticated SpotifyAPI instance.

        Returns:
            Dict with 'tracks_added' and 'tracks_total'.

        Raises:
            JobExecutionError: If execution fails.
        """
        if schedule.job_type == "raid":
            return JobExecutorService._execute_raid(schedule, api)
        elif schedule.job_type == "shuffle":
            return JobExecutorService._execute_shuffle(schedule, api)
        elif schedule.job_type == "raid_and_shuffle":
            result = JobExecutorService._execute_raid(schedule, api)
            shuffle_result = JobExecutorService._execute_shuffle(
                schedule, api
            )
            result["tracks_total"] = shuffle_result["tracks_total"]
            return result
        else:
            raise JobExecutionError(
                f"Unknown job type: {schedule.job_type}"
            )

    @staticmethod
    def _execute_raid(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """
        Pull new tracks from source playlists into the target.

        Fetches tracks from all configured source playlists, filters
        out any that already exist in the target, and adds the new ones.

        Args:
            schedule: The Schedule with source_playlist_ids and target.
            api: Authenticated SpotifyAPI.

        Returns:
            Dict with 'tracks_added' and 'tracks_total'.
        """
        target_id = schedule.target_playlist_id
        source_ids = schedule.source_playlist_ids or []

        if not source_ids:
            logger.info(
                f"Schedule {schedule.id}: no source playlists configured, "
                f"skipping raid"
            )
            target_tracks = api.get_playlist_tracks(target_id)
            return {"tracks_added": 0, "tracks_total": len(target_tracks)}

        try:
            # Get current target tracks (URIs for dedup)
            target_tracks = api.get_playlist_tracks(target_id)
            target_uris = {
                t.get("uri") for t in target_tracks if t.get("uri")
            }

            # Collect new tracks from all sources
            new_uris: List[str] = []
            for source_id in source_ids:
                try:
                    source_tracks = api.get_playlist_tracks(source_id)
                    for track in source_tracks:
                        uri = track.get("uri")
                        if uri and uri not in target_uris and uri not in new_uris:
                            new_uris.append(uri)
                except SpotifyNotFoundError:
                    logger.warning(
                        f"Source playlist {source_id} not found, skipping"
                    )
                    continue

            if not new_uris:
                logger.info(
                    f"Schedule {schedule.id}: no new tracks to add"
                )
                return {
                    "tracks_added": 0,
                    "tracks_total": len(target_tracks),
                }

            # Add new tracks to the end of the target playlist
            # Spotify allows adding in batches of 100
            batch_size = 100
            for i in range(0, len(new_uris), batch_size):
                batch = new_uris[i : i + batch_size]
                api._ensure_valid_token()
                api._sp.playlist_add_items(target_id, batch)

            total = len(target_tracks) + len(new_uris)
            logger.info(
                f"Schedule {schedule.id}: added {len(new_uris)} tracks "
                f"to {schedule.target_playlist_name} (total: {total})"
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

    @staticmethod
    def _execute_shuffle(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """
        Run a shuffle algorithm on the target playlist.

        Args:
            schedule: The Schedule with algorithm_name and params.
            api: Authenticated SpotifyAPI.

        Returns:
            Dict with 'tracks_added' (always 0) and 'tracks_total'.
        """
        target_id = schedule.target_playlist_id
        algorithm_name = schedule.algorithm_name

        if not algorithm_name:
            raise JobExecutionError(
                f"Schedule {schedule.id}: no algorithm configured for shuffle"
            )

        try:
            # Get current tracks
            raw_tracks = api.get_playlist_tracks(target_id)
            if not raw_tracks:
                return {"tracks_added": 0, "tracks_total": 0}

            # Normalize tracks for shuffle algorithms
            tracks = []
            for t in raw_tracks:
                if t.get("uri"):
                    tracks.append(
                        {
                            "id": t.get("id", ""),
                            "name": t.get("name", ""),
                            "uri": t["uri"],
                            "artists": [
                                a.get("name", "")
                                for a in t.get("artists", [])
                            ],
                            "album": t.get("album", {}),
                        }
                    )

            if not tracks:
                return {"tracks_added": 0, "tracks_total": 0}

            # Get algorithm and execute
            algorithm_class = ShuffleRegistry.get_algorithm(algorithm_name)
            algorithm = algorithm_class()
            params = schedule.algorithm_params or {}
            shuffled_uris = algorithm.shuffle(tracks, **params)

            # Update the playlist
            api.update_playlist_tracks(target_id, shuffled_uris)

            logger.info(
                f"Schedule {schedule.id}: shuffled "
                f"{schedule.target_playlist_name} with {algorithm_name}"
            )

            return {
                "tracks_added": 0,
                "tracks_total": len(shuffled_uris),
            }

        except SpotifyNotFoundError:
            raise JobExecutionError(
                f"Target playlist {target_id} not found"
            )
        except ValueError as e:
            raise JobExecutionError(
                f"Invalid algorithm '{algorithm_name}': {e}"
            )
        except SpotifyAPIError as e:
            raise JobExecutionError(
                f"Spotify API error during shuffle: {e}"
            )
```

### Step 7: Create Pydantic Schemas for Schedule Requests

**File:** `/Users/chris/Projects/shuffify/shuffify/schemas/schedule_requests.py`

```python
"""
Pydantic validation schemas for schedule API requests.

Validates schedule creation and update payloads.
"""

from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

from shuffify.shuffle_algorithms.registry import ShuffleRegistry


VALID_JOB_TYPES = {"raid", "shuffle", "raid_and_shuffle"}
VALID_SCHEDULE_TYPES = {"interval", "cron"}
VALID_INTERVAL_VALUES = {"every_6h", "every_12h", "daily", "every_3d", "weekly"}


class ScheduleCreateRequest(BaseModel):
    """Schema for creating a new schedule."""

    job_type: str = Field(
        ..., description="Type of job: 'raid', 'shuffle', or 'raid_and_shuffle'"
    )
    target_playlist_id: str = Field(
        ..., min_length=1, description="Spotify playlist ID to operate on"
    )
    target_playlist_name: str = Field(
        ..., min_length=1, max_length=255, description="Display name for the playlist"
    )
    schedule_type: str = Field(
        default="interval", description="'interval' or 'cron'"
    )
    schedule_value: str = Field(
        default="daily", description="Schedule specification"
    )
    source_playlist_ids: Optional[List[str]] = Field(
        default=None, description="Source playlist IDs (required for raid)"
    )
    algorithm_name: Optional[str] = Field(
        default=None, description="Shuffle algorithm name (required for shuffle)"
    )
    algorithm_params: Optional[Dict[str, Any]] = Field(
        default=None, description="Algorithm parameters"
    )

    @field_validator("job_type")
    @classmethod
    def validate_job_type(cls, v: str) -> str:
        """Ensure job_type is valid."""
        v = v.strip().lower()
        if v not in VALID_JOB_TYPES:
            raise ValueError(
                f"Invalid job_type '{v}'. "
                f"Must be one of: {', '.join(sorted(VALID_JOB_TYPES))}"
            )
        return v

    @field_validator("schedule_type")
    @classmethod
    def validate_schedule_type(cls, v: str) -> str:
        """Ensure schedule_type is valid."""
        v = v.strip().lower()
        if v not in VALID_SCHEDULE_TYPES:
            raise ValueError(
                f"Invalid schedule_type '{v}'. "
                f"Must be one of: {', '.join(sorted(VALID_SCHEDULE_TYPES))}"
            )
        return v

    @field_validator("schedule_value")
    @classmethod
    def validate_schedule_value(cls, v: str) -> str:
        """Basic validation of schedule value."""
        v = v.strip()
        if not v:
            raise ValueError("schedule_value cannot be empty")
        return v

    @field_validator("algorithm_name")
    @classmethod
    def validate_algorithm_name(cls, v: Optional[str]) -> Optional[str]:
        """Ensure algorithm name is valid if provided."""
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        valid = set(ShuffleRegistry.get_available_algorithms().keys())
        if v not in valid:
            raise ValueError(
                f"Invalid algorithm '{v}'. "
                f"Valid options: {', '.join(sorted(valid))}"
            )
        return v

    @model_validator(mode="after")
    def validate_job_requirements(self):
        """Cross-field validation: ensure required fields for each job type."""
        if self.job_type in ("raid", "raid_and_shuffle"):
            if not self.source_playlist_ids:
                raise ValueError(
                    f"source_playlist_ids required for job_type '{self.job_type}'"
                )
        if self.job_type in ("shuffle", "raid_and_shuffle"):
            if not self.algorithm_name:
                raise ValueError(
                    f"algorithm_name required for job_type '{self.job_type}'"
                )
        if self.schedule_type == "interval":
            if self.schedule_value not in VALID_INTERVAL_VALUES:
                raise ValueError(
                    f"Invalid interval '{self.schedule_value}'. "
                    f"Must be one of: {', '.join(sorted(VALID_INTERVAL_VALUES))}"
                )
        if self.schedule_type == "cron":
            parts = self.schedule_value.split()
            if len(parts) != 5:
                raise ValueError(
                    "Cron expression must have 5 fields: "
                    "minute hour day month day_of_week"
                )
        return self


class ScheduleUpdateRequest(BaseModel):
    """Schema for updating an existing schedule."""

    job_type: Optional[str] = None
    target_playlist_id: Optional[str] = None
    target_playlist_name: Optional[str] = None
    schedule_type: Optional[str] = None
    schedule_value: Optional[str] = None
    source_playlist_ids: Optional[List[str]] = None
    algorithm_name: Optional[str] = None
    algorithm_params: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None

    @field_validator("job_type")
    @classmethod
    def validate_job_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in VALID_JOB_TYPES:
            raise ValueError(
                f"Invalid job_type '{v}'. "
                f"Must be one of: {', '.join(sorted(VALID_JOB_TYPES))}"
            )
        return v

    @field_validator("schedule_type")
    @classmethod
    def validate_schedule_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in VALID_SCHEDULE_TYPES:
            raise ValueError(f"Invalid schedule_type '{v}'")
        return v

    @field_validator("algorithm_name")
    @classmethod
    def validate_algorithm_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        valid = set(ShuffleRegistry.get_available_algorithms().keys())
        if v not in valid:
            raise ValueError(f"Invalid algorithm '{v}'")
        return v

    class Config:
        extra = "ignore"
```

### Step 8: Update the Schemas Package Init

**File:** `/Users/chris/Projects/shuffify/shuffify/schemas/__init__.py`

**Add these imports (after line 18):**

```python
from .schedule_requests import (
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
)
```

**Add to `__all__` list:**

```python
    "ScheduleCreateRequest",
    "ScheduleUpdateRequest",
```

### Step 9: Add Schedule Routes

**File:** `/Users/chris/Projects/shuffify/shuffify/routes.py`

**9a. Update imports (around lines 22-31):**

Add `SchedulerService` and related imports. After the existing services import block, add:

```python
from shuffify.services.scheduler_service import (
    SchedulerService,
    ScheduleError,
    ScheduleNotFoundError,
    ScheduleLimitError,
)
from shuffify.services.job_executor_service import (
    JobExecutorService,
    JobExecutionError,
)
```

Also update the schemas import (line 31):

```python
from shuffify.schemas import (
    parse_shuffle_request,
    PlaylistQueryParams,
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
)
```

**9b. Add helper function to get the database user.** Place this after the existing `json_success` helper (after line 93):

```python
def get_db_user():
    """
    Get the database User record for the current session user.

    Returns:
        User model instance or None if not found.
    """
    user_data = session.get("user_data")
    if not user_data or "id" not in user_data:
        return None

    from shuffify.services.user_service import UserService

    return UserService.get_by_spotify_id(user_data["id"])
```

**9c. Add schedule routes section at the end of `routes.py` (after the Workshop Routes section that Phase 1 adds):**

```python
# =============================================================================
# Schedule Routes
# =============================================================================


@main.route("/schedules")
def schedules():
    """Render the Schedules management page."""
    if not is_authenticated():
        return redirect(url_for("main.index"))

    try:
        client = AuthService.get_authenticated_client(session["spotify_token"])
        user = AuthService.get_user_data(client)

        db_user = get_db_user()
        if not db_user:
            flash("Please log in again to access schedules.", "error")
            return redirect(url_for("main.index"))

        user_schedules = SchedulerService.get_user_schedules(db_user.id)

        # Get user playlists for the schedule creation form
        playlist_service = PlaylistService(client)
        playlists = playlist_service.get_user_playlists()

        algorithms = ShuffleService.list_algorithms()

        return render_template(
            "schedules.html",
            user=user,
            schedules=[s.to_dict() for s in user_schedules],
            playlists=playlists,
            algorithms=algorithms,
            max_schedules=SchedulerService.MAX_SCHEDULES_PER_USER,
        )

    except (AuthenticationError, PlaylistError) as e:
        logger.error(f"Error loading schedules page: {e}")
        return clear_session_and_show_login(
            "Your session has expired. Please log in again."
        )


@main.route("/schedules/create", methods=["POST"])
def create_schedule():
    """Create a new scheduled operation."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error("User not found. Please log in again.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Validate with Pydantic
    create_request = ScheduleCreateRequest(**data)

    schedule = SchedulerService.create_schedule(
        user_id=db_user.id,
        job_type=create_request.job_type,
        target_playlist_id=create_request.target_playlist_id,
        target_playlist_name=create_request.target_playlist_name,
        schedule_type=create_request.schedule_type,
        schedule_value=create_request.schedule_value,
        source_playlist_ids=create_request.source_playlist_ids,
        algorithm_name=create_request.algorithm_name,
        algorithm_params=create_request.algorithm_params,
    )

    # Register with APScheduler
    try:
        from flask import current_app
        from shuffify.scheduler import add_job_for_schedule

        add_job_for_schedule(schedule, current_app._get_current_object())
    except RuntimeError as e:
        logger.warning(f"Could not register schedule with APScheduler: {e}")

    logger.info(
        f"User {db_user.spotify_id} created schedule {schedule.id}: "
        f"{schedule.job_type} on {schedule.target_playlist_name}"
    )

    return json_success(
        "Schedule created successfully.",
        schedule=schedule.to_dict(),
    )


@main.route("/schedules/<int:schedule_id>", methods=["PUT"])
def update_schedule(schedule_id):
    """Update an existing schedule."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error("User not found. Please log in again.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Validate with Pydantic
    update_request = ScheduleUpdateRequest(**data)
    update_fields = {
        k: v
        for k, v in update_request.model_dump().items()
        if v is not None
    }

    schedule = SchedulerService.update_schedule(
        schedule_id=schedule_id,
        user_id=db_user.id,
        **update_fields,
    )

    # Update APScheduler job
    try:
        from flask import current_app
        from shuffify.scheduler import add_job_for_schedule, remove_job_for_schedule

        if schedule.is_enabled:
            add_job_for_schedule(schedule, current_app._get_current_object())
        else:
            remove_job_for_schedule(schedule_id)
    except RuntimeError as e:
        logger.warning(f"Could not update APScheduler job: {e}")

    logger.info(f"Updated schedule {schedule_id}")

    return json_success(
        "Schedule updated successfully.",
        schedule=schedule.to_dict(),
    )


@main.route("/schedules/<int:schedule_id>", methods=["DELETE"])
def delete_schedule(schedule_id):
    """Delete a schedule."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error("User not found. Please log in again.", 401)

    # Remove from APScheduler first
    from shuffify.scheduler import remove_job_for_schedule

    remove_job_for_schedule(schedule_id)

    # Delete from database
    SchedulerService.delete_schedule(schedule_id, db_user.id)

    logger.info(f"Deleted schedule {schedule_id}")

    return json_success("Schedule deleted successfully.")


@main.route("/schedules/<int:schedule_id>/toggle", methods=["POST"])
def toggle_schedule(schedule_id):
    """Toggle a schedule's enabled/disabled state."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error("User not found. Please log in again.", 401)

    schedule = SchedulerService.toggle_schedule(schedule_id, db_user.id)

    # Update APScheduler
    try:
        from flask import current_app
        from shuffify.scheduler import add_job_for_schedule, remove_job_for_schedule

        if schedule.is_enabled:
            add_job_for_schedule(schedule, current_app._get_current_object())
        else:
            remove_job_for_schedule(schedule_id)
    except RuntimeError as e:
        logger.warning(f"Could not update APScheduler job: {e}")

    status_text = "enabled" if schedule.is_enabled else "disabled"
    return json_success(
        f"Schedule {status_text}.",
        schedule=schedule.to_dict(),
    )


@main.route("/schedules/<int:schedule_id>/run", methods=["POST"])
def run_schedule_now(schedule_id):
    """Manually trigger a schedule execution."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error("User not found. Please log in again.", 401)

    result = JobExecutorService.execute_now(schedule_id, db_user.id)

    return json_success(
        "Schedule executed successfully.",
        result=result,
    )


@main.route("/schedules/<int:schedule_id>/history")
def schedule_history(schedule_id):
    """Get execution history for a schedule."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error("User not found. Please log in again.", 401)

    history = SchedulerService.get_execution_history(
        schedule_id, db_user.id, limit=10
    )

    return jsonify({"success": True, "history": history})
```

### Step 10: Update Error Handlers

**File:** `/Users/chris/Projects/shuffify/shuffify/error_handlers.py`

**Add imports (around lines 12-25):** Add after the existing imports:

```python
from shuffify.services.scheduler_service import (
    ScheduleError,
    ScheduleNotFoundError,
    ScheduleLimitError,
)
from shuffify.services.job_executor_service import JobExecutionError
```

**Add error handlers inside `register_error_handlers()` (before the HTTP Error Codes section):**

```python
    # =========================================================================
    # Schedule Errors
    # =========================================================================

    @app.errorhandler(ScheduleNotFoundError)
    def handle_schedule_not_found(error: ScheduleNotFoundError):
        """Handle schedule not found errors."""
        logger.info(f"Schedule not found: {error}")
        return json_error_response("Schedule not found.", 404)

    @app.errorhandler(ScheduleLimitError)
    def handle_schedule_limit(error: ScheduleLimitError):
        """Handle schedule limit exceeded errors."""
        logger.warning(f"Schedule limit exceeded: {error}")
        return json_error_response(str(error), 400)

    @app.errorhandler(ScheduleError)
    def handle_schedule_error(error: ScheduleError):
        """Handle general schedule errors."""
        logger.error(f"Schedule error: {error}")
        return json_error_response(str(error), 500)

    @app.errorhandler(JobExecutionError)
    def handle_job_execution_error(error: JobExecutionError):
        """Handle job execution failures."""
        logger.error(f"Job execution error: {error}")
        return json_error_response(f"Job execution failed: {error}", 500)
```

### Step 11: Update Services Package Init

**File:** `/Users/chris/Projects/shuffify/shuffify/services/__init__.py`

**Add after the existing State Service imports (after line 57):**

```python
# Token Service
from shuffify.services.token_service import (
    TokenService,
    TokenEncryptionError,
)

# Scheduler Service
from shuffify.services.scheduler_service import (
    SchedulerService,
    ScheduleError,
    ScheduleNotFoundError,
    ScheduleLimitError,
)

# Job Executor Service
from shuffify.services.job_executor_service import (
    JobExecutorService,
    JobExecutionError,
)
```

**Add to `__all__` list:**

```python
    # Token Service
    "TokenService",
    "TokenEncryptionError",
    # Scheduler Service
    "SchedulerService",
    "ScheduleError",
    "ScheduleNotFoundError",
    "ScheduleLimitError",
    # Job Executor Service
    "JobExecutorService",
    "JobExecutionError",
```

### Step 12: Update the App Factory

**File:** `/Users/chris/Projects/shuffify/shuffify/__init__.py`

**12a. After Flask-Session initialization (after line 141, after `Session(app)`):**

Add TokenService initialization:

```python
    # Initialize token encryption service
    from shuffify.services.token_service import TokenService

    try:
        TokenService.initialize(app.config["SECRET_KEY"])
        logger.info("Token encryption service initialized")
    except Exception as e:
        logger.warning(f"Token encryption init failed: {e}")
```

**12b. After blueprint registration (after line 146, after `app.register_blueprint(main_blueprint)`):**

Add scheduler initialization:

```python
    # Initialize APScheduler (after all extensions)
    if app.config.get("SCHEDULER_ENABLED", True):
        from shuffify.scheduler import init_scheduler

        scheduler = init_scheduler(app)
        if scheduler:
            app.extensions["scheduler"] = scheduler
```

**12c. Add atexit handler for graceful shutdown.** At the top of the file (after line 6, after `import redis`), add:

```python
import atexit
```

Then at the end of `create_app()`, before `return app`:

```python
    # Register scheduler shutdown on app teardown
    @atexit.register
    def shutdown():
        from shuffify.scheduler import shutdown_scheduler
        shutdown_scheduler()
```

### Step 13: Update the OAuth Callback to Store Refresh Token

**File:** `/Users/chris/Projects/shuffify/shuffify/routes.py`

In the `/callback` route (around line 214-234), after the line `session["user_data"] = user_data` and before the `logger.info` call, add code to store the encrypted refresh token in the database. Insert the following block:

```python
        # Store encrypted refresh token for scheduled operations
        if token_data.get("refresh_token"):
            try:
                from shuffify.services.token_service import TokenService
                from shuffify.services.user_service import UserService

                db_user = UserService.get_or_create(
                    spotify_id=user_data["id"],
                    display_name=user_data.get("display_name"),
                    email=user_data.get("email"),
                )
                db_user.encrypted_refresh_token = TokenService.encrypt_token(
                    token_data["refresh_token"]
                )

                from shuffify.models.db import db

                db.session.commit()
                logger.debug(
                    f"Stored encrypted refresh token for user {user_data['id']}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to store refresh token: {e}. "
                    f"Scheduled operations may not work."
                )
```

### Step 14: Update `run.py` for Development Mode

**File:** `/Users/chris/Projects/shuffify/run.py`

Change `app.run()` to disable the reloader by default (or check `WERKZEUG_RUN_MAIN`). The scheduler module already guards against the reloader, but we also want to make the development experience clean. Replace lines 8-11:

```python
if __name__ == '__main__':
    app.run(
        host=os.getenv('FLASK_HOST', '0.0.0.0'),
        port=int(os.getenv('FLASK_PORT', 8000)),
        debug=app.debug,
        use_reloader=os.getenv('FLASK_USE_RELOADER', 'true').lower() == 'true',
    )
```

### Step 15: Update `requirements/base.txt`

**File:** `/Users/chris/Projects/shuffify/requirements/base.txt`

Add after the existing entries:

```
APScheduler>=3.10
```

Note: `cryptography>=43.0.1` is already in `requirements/base.txt`.

### Step 16: Create the Schedules Template

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/schedules.html`

```html
{% extends "base.html" %}

{% block title %}Schedules - Shuffify{% endblock %}

{% block content %}
<div class="min-h-screen bg-gradient-to-br from-spotify-green via-spotify-green/90 to-spotify-dark">
    <div class="absolute inset-0" style="background-image: url('/static/images/hero-pattern.svg'); opacity: 0.15; pointer-events: none;"></div>

    <!-- Header -->
    <div class="relative max-w-5xl mx-auto px-4 pt-8">
        <div class="p-6 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20">
            <div class="flex items-center justify-between flex-wrap gap-4">
                <div class="flex items-center">
                    <a href="{{ url_for('main.index') }}"
                       class="mr-4 p-2 rounded-lg bg-white/10 hover:bg-white/20 transition duration-150 border border-white/20"
                       title="Back to Dashboard">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path>
                        </svg>
                    </a>
                    <div>
                        <h1 class="text-2xl font-bold text-white">Scheduled Operations</h1>
                        <p class="text-white/70 text-sm">
                            {{ schedules|length }} / {{ max_schedules }} schedules configured
                        </p>
                    </div>
                </div>
                <button id="new-schedule-btn"
                        onclick="showCreateModal()"
                        class="inline-flex items-center px-4 py-2 rounded-lg bg-white text-spotify-dark font-bold transition duration-150 hover:bg-green-100 shadow-lg {% if schedules|length >= max_schedules %}opacity-40 cursor-not-allowed{% endif %}"
                        {% if schedules|length >= max_schedules %}disabled{% endif %}>
                    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path>
                    </svg>
                    New Schedule
                </button>
            </div>
        </div>
    </div>

    <!-- Schedule List -->
    <div class="relative max-w-5xl mx-auto px-4 py-6">
        {% if not schedules %}
        <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-8 text-center">
            <svg class="w-16 h-16 mx-auto text-white/30 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <p class="text-white/60 text-lg mb-2">No scheduled operations yet.</p>
            <p class="text-white/40 text-sm">Create a schedule to automatically raid playlists or run shuffles on a recurring basis.</p>
        </div>
        {% else %}
        <div class="space-y-4" id="schedule-list">
            {% for schedule in schedules %}
            <div class="schedule-card rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-5"
                 data-schedule-id="{{ schedule.id }}">
                <div class="flex items-start justify-between flex-wrap gap-3">
                    <!-- Schedule Info -->
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-1">
                            <span class="px-2 py-0.5 rounded-full text-xs font-bold uppercase
                                {% if schedule.job_type == 'raid' %}bg-blue-500/80
                                {% elif schedule.job_type == 'shuffle' %}bg-purple-500/80
                                {% else %}bg-orange-500/80{% endif %} text-white">
                                {{ schedule.job_type | replace('_', ' ') }}
                            </span>
                            <span class="px-2 py-0.5 rounded-full text-xs font-semibold
                                {% if schedule.is_enabled %}bg-green-500/60 text-white
                                {% else %}bg-gray-500/60 text-white/70{% endif %}">
                                {{ 'Active' if schedule.is_enabled else 'Paused' }}
                            </span>
                        </div>
                        <h3 class="text-white font-bold text-lg truncate">{{ schedule.target_playlist_name }}</h3>
                        <p class="text-white/60 text-sm">
                            Runs {{ schedule.schedule_value | replace('_', ' ') }}
                            {% if schedule.algorithm_name %}
                            &middot; {{ schedule.algorithm_name }}
                            {% endif %}
                            {% if schedule.source_playlist_ids %}
                            &middot; {{ schedule.source_playlist_ids|length }} source{{ 's' if schedule.source_playlist_ids|length != 1 else '' }}
                            {% endif %}
                        </p>
                        {% if schedule.last_run_at %}
                        <p class="text-white/40 text-xs mt-1">
                            Last run: {{ schedule.last_run_at }}
                            {% if schedule.last_status == 'success' %}
                            <span class="text-green-400">-- Success</span>
                            {% elif schedule.last_status == 'failed' %}
                            <span class="text-red-400">-- Failed</span>
                            {% endif %}
                        </p>
                        {% endif %}
                    </div>

                    <!-- Actions -->
                    <div class="flex items-center space-x-2 flex-shrink-0">
                        <button onclick="runScheduleNow({{ schedule.id }})"
                                class="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white transition duration-150 border border-white/20"
                                title="Run Now">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"></path>
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                        </button>
                        <button onclick="toggleSchedule({{ schedule.id }})"
                                class="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white transition duration-150 border border-white/20"
                                title="{{ 'Pause' if schedule.is_enabled else 'Enable' }}">
                            {% if schedule.is_enabled %}
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            {% else %}
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"></path>
                            </svg>
                            {% endif %}
                        </button>
                        <button onclick="deleteSchedule({{ schedule.id }})"
                                class="p-2 rounded-lg bg-red-500/20 hover:bg-red-500/40 text-red-300 transition duration-150 border border-red-500/30"
                                title="Delete">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
        {% endif %}
    </div>
</div>

<!-- Create Schedule Modal -->
<div id="create-modal" class="fixed inset-0 z-50 hidden">
    <div class="absolute inset-0 bg-black/60 backdrop-blur-sm" onclick="hideCreateModal()"></div>
    <div class="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div class="rounded-2xl shadow-2xl bg-spotify-dark border border-white/20 p-6">
            <h2 class="text-xl font-bold text-white mb-4">New Scheduled Operation</h2>

            <form id="create-schedule-form" class="space-y-4">
                <!-- Job Type -->
                <div>
                    <label class="block text-sm font-medium text-white/90 mb-1">Operation Type</label>
                    <select id="modal-job-type" name="job_type"
                            class="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white"
                            onchange="updateModalFields()">
                        <option value="raid">Raid (pull new tracks from sources)</option>
                        <option value="shuffle">Shuffle (reorder playlist)</option>
                        <option value="raid_and_shuffle">Raid + Shuffle (pull then shuffle)</option>
                    </select>
                </div>

                <!-- Target Playlist -->
                <div>
                    <label class="block text-sm font-medium text-white/90 mb-1">Target Playlist</label>
                    <select id="modal-target-playlist" name="target_playlist"
                            class="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white">
                        {% for pl in playlists %}
                        <option value="{{ pl.id }}" data-name="{{ pl.name }}">{{ pl.name }}</option>
                        {% endfor %}
                    </select>
                </div>

                <!-- Source Playlists (shown for raid types) -->
                <div id="modal-sources-section">
                    <label class="block text-sm font-medium text-white/90 mb-1">Source Playlists</label>
                    <p class="text-white/50 text-xs mb-2">Select playlists to pull new tracks from.</p>
                    <div class="max-h-40 overflow-y-auto space-y-1 bg-white/5 rounded-lg p-2">
                        {% for pl in playlists %}
                        <label class="flex items-center space-x-2 px-2 py-1 rounded hover:bg-white/5 cursor-pointer">
                            <input type="checkbox" name="source_playlist" value="{{ pl.id }}"
                                   class="rounded border-white/30 bg-white/10 text-spotify-green focus:ring-spotify-green">
                            <span class="text-white/80 text-sm truncate">{{ pl.name }}</span>
                        </label>
                        {% endfor %}
                    </div>
                </div>

                <!-- Algorithm (shown for shuffle types) -->
                <div id="modal-algorithm-section" class="hidden">
                    <label class="block text-sm font-medium text-white/90 mb-1">Shuffle Algorithm</label>
                    <select id="modal-algorithm" name="algorithm_name"
                            class="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white">
                        {% for algo in algorithms %}
                        <option value="{{ algo.class_name }}">{{ algo.name }}</option>
                        {% endfor %}
                    </select>
                </div>

                <!-- Schedule Frequency -->
                <div>
                    <label class="block text-sm font-medium text-white/90 mb-1">Frequency</label>
                    <select id="modal-frequency" name="schedule_value"
                            class="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white">
                        <option value="every_6h">Every 6 hours</option>
                        <option value="every_12h">Every 12 hours</option>
                        <option value="daily" selected>Daily</option>
                        <option value="every_3d">Every 3 days</option>
                        <option value="weekly">Weekly</option>
                    </select>
                </div>

                <!-- Buttons -->
                <div class="flex space-x-3 pt-2">
                    <button type="button" onclick="hideCreateModal()"
                            class="flex-1 px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium transition border border-white/20">
                        Cancel
                    </button>
                    <button type="submit"
                            class="flex-1 px-4 py-2 rounded-lg bg-white text-spotify-dark font-bold transition hover:bg-green-100 shadow-lg">
                        Create Schedule
                    </button>
                </div>
            </form>
        </div>
    </div>
</div>

<script>
// =============================================================================
// Modal Management
// =============================================================================

function showCreateModal() {
    document.getElementById('create-modal').classList.remove('hidden');
    updateModalFields();
}

function hideCreateModal() {
    document.getElementById('create-modal').classList.add('hidden');
}

function updateModalFields() {
    const jobType = document.getElementById('modal-job-type').value;
    const sourcesSection = document.getElementById('modal-sources-section');
    const algorithmSection = document.getElementById('modal-algorithm-section');

    // Show/hide sections based on job type
    if (jobType === 'raid' || jobType === 'raid_and_shuffle') {
        sourcesSection.classList.remove('hidden');
    } else {
        sourcesSection.classList.add('hidden');
    }

    if (jobType === 'shuffle' || jobType === 'raid_and_shuffle') {
        algorithmSection.classList.remove('hidden');
    } else {
        algorithmSection.classList.add('hidden');
    }
}

// =============================================================================
// Create Schedule
// =============================================================================

document.getElementById('create-schedule-form').addEventListener('submit', function(e) {
    e.preventDefault();

    const jobType = document.getElementById('modal-job-type').value;
    const targetSelect = document.getElementById('modal-target-playlist');
    const targetId = targetSelect.value;
    const targetName = targetSelect.options[targetSelect.selectedIndex].dataset.name;
    const scheduleValue = document.getElementById('modal-frequency').value;

    const body = {
        job_type: jobType,
        target_playlist_id: targetId,
        target_playlist_name: targetName,
        schedule_type: 'interval',
        schedule_value: scheduleValue,
    };

    // Add source playlists if applicable
    if (jobType === 'raid' || jobType === 'raid_and_shuffle') {
        const checked = document.querySelectorAll('input[name="source_playlist"]:checked');
        body.source_playlist_ids = Array.from(checked).map(cb => cb.value);
        if (body.source_playlist_ids.length === 0) {
            showNotification('Please select at least one source playlist.', 'error');
            return;
        }
    }

    // Add algorithm if applicable
    if (jobType === 'shuffle' || jobType === 'raid_and_shuffle') {
        body.algorithm_name = document.getElementById('modal-algorithm').value;
        body.algorithm_params = {};
    }

    fetch('/schedules/create', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify(body),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(d => { throw new Error(d.message || 'Failed to create schedule.'); });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            showNotification(data.message, 'success');
            hideCreateModal();
            // Reload to show the new schedule
            setTimeout(() => window.location.reload(), 500);
        }
    })
    .catch(error => {
        showNotification(error.message, 'error');
    });
});

// =============================================================================
// Schedule Actions
// =============================================================================

function runScheduleNow(scheduleId) {
    if (!confirm('Run this schedule now?')) return;

    fetch(`/schedules/${scheduleId}/run`, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(d => { throw new Error(d.message || 'Execution failed.'); });
        }
        return response.json();
    })
    .then(data => {
        showNotification(data.message || 'Executed successfully.', 'success');
        setTimeout(() => window.location.reload(), 1000);
    })
    .catch(error => {
        showNotification(error.message, 'error');
    });
}

function toggleSchedule(scheduleId) {
    fetch(`/schedules/${scheduleId}/toggle`, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showNotification(data.message, 'success');
            setTimeout(() => window.location.reload(), 500);
        } else {
            showNotification(data.message || 'Toggle failed.', 'error');
        }
    })
    .catch(error => {
        showNotification('Failed to toggle schedule.', 'error');
    });
}

function deleteSchedule(scheduleId) {
    if (!confirm('Delete this schedule? This cannot be undone.')) return;

    fetch(`/schedules/${scheduleId}`, {
        method: 'DELETE',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showNotification(data.message, 'success');
            const card = document.querySelector(`[data-schedule-id="${scheduleId}"]`);
            if (card) card.remove();
        } else {
            showNotification(data.message || 'Delete failed.', 'error');
        }
    })
    .catch(error => {
        showNotification('Failed to delete schedule.', 'error');
    });
}

// =============================================================================
// Notifications (reused from dashboard pattern)
// =============================================================================

function showNotification(message, type) {
    const notification = document.createElement('div');
    const bgColor = type === 'success' ? 'bg-green-500/90' :
                    type === 'info' ? 'bg-blue-500/90' : 'bg-red-500/90';
    notification.className = `fixed bottom-4 right-4 px-6 py-3 rounded-lg shadow-lg backdrop-blur-md ${bgColor} text-white font-semibold transform transition duration-300 translate-y-16 opacity-0 z-50`;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.classList.remove('translate-y-16', 'opacity-0');
    }, 100);

    setTimeout(() => {
        notification.classList.add('translate-y-16', 'opacity-0');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}
</script>
{% endblock %}
```

### Step 17: Add "Schedules" Link to Dashboard

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/dashboard.html`

**Where:** In the user header bar (lines 25-44), add a "Schedules" link next to the "Refresh" and "Logout" buttons. Insert before the Refresh button (before line 27):

```html
                    <!-- Schedules Link -->
                    <a href="{{ url_for('main.schedules') }}"
                       class="inline-flex items-center px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium transition duration-150 border border-white/20 hover:border-white/30"
                       title="Manage scheduled operations">
                        <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        Schedules
                    </a>
```

### Step 18: Create Database Migration

After implementing the code above, generate a migration:

```bash
flask db migrate -m "Add Schedule and JobExecution models; add encrypted_refresh_token to User"
flask db upgrade
```

---

## Test Plan

### Test File: `/Users/chris/Projects/shuffify/tests/services/test_token_service.py`

```python
"""
Tests for the TokenService (Fernet encryption/decryption).
"""

import pytest
from shuffify.services.token_service import TokenService, TokenEncryptionError


class TestTokenService:
    """Tests for TokenService encrypt/decrypt operations."""

    def setup_method(self):
        """Initialize TokenService before each test."""
        TokenService._fernet = None  # Reset state
        TokenService.initialize("test-secret-key-for-unit-tests")

    def teardown_method(self):
        """Reset TokenService after each test."""
        TokenService._fernet = None

    def test_initialize_success(self):
        """TokenService should initialize with a valid secret key."""
        assert TokenService.is_initialized() is True

    def test_initialize_empty_key_raises(self):
        """Empty secret key should raise TokenEncryptionError."""
        TokenService._fernet = None
        with pytest.raises(TokenEncryptionError, match="SECRET_KEY is required"):
            TokenService.initialize("")

    def test_encrypt_and_decrypt_round_trip(self):
        """Encrypting then decrypting should return the original token."""
        original = "AQDf8h3k_test_refresh_token_value"
        encrypted = TokenService.encrypt_token(original)
        decrypted = TokenService.decrypt_token(encrypted)
        assert decrypted == original

    def test_encrypted_differs_from_plaintext(self):
        """Encrypted output must not equal the plaintext."""
        original = "my_refresh_token"
        encrypted = TokenService.encrypt_token(original)
        assert encrypted != original

    def test_encrypt_empty_token_raises(self):
        """Encrypting an empty string should raise."""
        with pytest.raises(TokenEncryptionError, match="Cannot encrypt empty"):
            TokenService.encrypt_token("")

    def test_decrypt_empty_token_raises(self):
        """Decrypting an empty string should raise."""
        with pytest.raises(TokenEncryptionError, match="Cannot decrypt empty"):
            TokenService.decrypt_token("")

    def test_decrypt_garbage_raises(self):
        """Decrypting invalid ciphertext should raise."""
        with pytest.raises(TokenEncryptionError, match="corrupted"):
            TokenService.decrypt_token("not-a-valid-fernet-token")

    def test_decrypt_with_wrong_key_raises(self):
        """Token encrypted with one key cannot be decrypted with another."""
        original = "secret_refresh_token"
        encrypted = TokenService.encrypt_token(original)

        # Re-initialize with a different key
        TokenService._fernet = None
        TokenService.initialize("different-secret-key")

        with pytest.raises(TokenEncryptionError, match="corrupted"):
            TokenService.decrypt_token(encrypted)

    def test_not_initialized_encrypt_raises(self):
        """Encrypting before initialization should raise."""
        TokenService._fernet = None
        with pytest.raises(TokenEncryptionError, match="not initialized"):
            TokenService.encrypt_token("some_token")

    def test_not_initialized_decrypt_raises(self):
        """Decrypting before initialization should raise."""
        TokenService._fernet = None
        with pytest.raises(TokenEncryptionError, match="not initialized"):
            TokenService.decrypt_token("some_encrypted")

    def test_different_plaintexts_produce_different_ciphertexts(self):
        """Different inputs should produce different outputs."""
        enc1 = TokenService.encrypt_token("token_a")
        enc2 = TokenService.encrypt_token("token_b")
        assert enc1 != enc2

    def test_same_plaintext_produces_different_ciphertexts(self):
        """Fernet uses a random IV, so same input produces different output."""
        enc1 = TokenService.encrypt_token("same_token")
        enc2 = TokenService.encrypt_token("same_token")
        assert enc1 != enc2  # Different ciphertexts
        # But both decrypt to the same value
        assert TokenService.decrypt_token(enc1) == TokenService.decrypt_token(enc2)
```

### Test File: `/Users/chris/Projects/shuffify/tests/services/test_scheduler_service.py`

```python
"""
Tests for SchedulerService CRUD operations.

These tests require a Flask app context with SQLAlchemy configured.
"""

import pytest
from unittest.mock import patch, MagicMock

from shuffify.services.scheduler_service import (
    SchedulerService,
    ScheduleError,
    ScheduleNotFoundError,
    ScheduleLimitError,
)


@pytest.fixture
def db_user(app_context):
    """Create a test user in the database."""
    from shuffify.models.db import db, User

    user = User(
        spotify_id="test_user_123",
        spotify_display_name="Test User",
    )
    db.session.add(user)
    db.session.commit()
    yield user
    # Cleanup
    db.session.rollback()


@pytest.fixture
def sample_schedule(db_user, app_context):
    """Create a sample schedule in the database."""
    schedule = SchedulerService.create_schedule(
        user_id=db_user.id,
        job_type="shuffle",
        target_playlist_id="playlist_abc",
        target_playlist_name="My Playlist",
        schedule_type="interval",
        schedule_value="daily",
        algorithm_name="BasicShuffle",
        algorithm_params={"keep_first": 0},
    )
    return schedule


class TestSchedulerServiceCreate:
    """Tests for creating schedules."""

    def test_create_schedule_success(self, db_user, app_context):
        """Should create a schedule and return it."""
        schedule = SchedulerService.create_schedule(
            user_id=db_user.id,
            job_type="raid",
            target_playlist_id="pl_123",
            target_playlist_name="Test Playlist",
            schedule_type="interval",
            schedule_value="weekly",
            source_playlist_ids=["source_1", "source_2"],
        )
        assert schedule.id is not None
        assert schedule.job_type == "raid"
        assert schedule.is_enabled is True
        assert len(schedule.source_playlist_ids) == 2

    def test_create_schedule_limit_enforced(self, db_user, app_context):
        """Should raise ScheduleLimitError when limit reached."""
        for i in range(SchedulerService.MAX_SCHEDULES_PER_USER):
            SchedulerService.create_schedule(
                user_id=db_user.id,
                job_type="shuffle",
                target_playlist_id=f"pl_{i}",
                target_playlist_name=f"Playlist {i}",
                schedule_type="interval",
                schedule_value="daily",
                algorithm_name="BasicShuffle",
            )

        with pytest.raises(ScheduleLimitError):
            SchedulerService.create_schedule(
                user_id=db_user.id,
                job_type="shuffle",
                target_playlist_id="pl_extra",
                target_playlist_name="Extra Playlist",
                schedule_type="interval",
                schedule_value="daily",
                algorithm_name="BasicShuffle",
            )


class TestSchedulerServiceRead:
    """Tests for reading schedules."""

    def test_get_user_schedules_empty(self, db_user, app_context):
        """Should return empty list for user with no schedules."""
        schedules = SchedulerService.get_user_schedules(db_user.id)
        assert schedules == []

    def test_get_user_schedules_returns_all(
        self, db_user, sample_schedule, app_context
    ):
        """Should return all schedules for the user."""
        schedules = SchedulerService.get_user_schedules(db_user.id)
        assert len(schedules) == 1
        assert schedules[0].id == sample_schedule.id

    def test_get_schedule_by_id(
        self, db_user, sample_schedule, app_context
    ):
        """Should return specific schedule by ID."""
        schedule = SchedulerService.get_schedule(
            sample_schedule.id, db_user.id
        )
        assert schedule.id == sample_schedule.id

    def test_get_schedule_wrong_user_raises(
        self, db_user, sample_schedule, app_context
    ):
        """Should raise ScheduleNotFoundError for wrong user."""
        with pytest.raises(ScheduleNotFoundError):
            SchedulerService.get_schedule(sample_schedule.id, user_id=99999)

    def test_get_schedule_nonexistent_raises(self, db_user, app_context):
        """Should raise ScheduleNotFoundError for nonexistent ID."""
        with pytest.raises(ScheduleNotFoundError):
            SchedulerService.get_schedule(99999, db_user.id)


class TestSchedulerServiceUpdate:
    """Tests for updating schedules."""

    def test_update_schedule_fields(
        self, db_user, sample_schedule, app_context
    ):
        """Should update specified fields."""
        updated = SchedulerService.update_schedule(
            schedule_id=sample_schedule.id,
            user_id=db_user.id,
            schedule_value="weekly",
            is_enabled=False,
        )
        assert updated.schedule_value == "weekly"
        assert updated.is_enabled is False

    def test_update_ignores_non_allowed_fields(
        self, db_user, sample_schedule, app_context
    ):
        """Should ignore fields not in allowed_fields set."""
        original_id = sample_schedule.id
        updated = SchedulerService.update_schedule(
            schedule_id=sample_schedule.id,
            user_id=db_user.id,
            id=99999,  # Should be ignored
        )
        assert updated.id == original_id


class TestSchedulerServiceDelete:
    """Tests for deleting schedules."""

    def test_delete_schedule_removes_from_db(
        self, db_user, sample_schedule, app_context
    ):
        """Should delete the schedule."""
        schedule_id = sample_schedule.id
        SchedulerService.delete_schedule(schedule_id, db_user.id)

        with pytest.raises(ScheduleNotFoundError):
            SchedulerService.get_schedule(schedule_id, db_user.id)

    def test_delete_nonexistent_raises(self, db_user, app_context):
        """Should raise ScheduleNotFoundError."""
        with pytest.raises(ScheduleNotFoundError):
            SchedulerService.delete_schedule(99999, db_user.id)


class TestSchedulerServiceToggle:
    """Tests for toggling schedule state."""

    def test_toggle_disables_enabled(
        self, db_user, sample_schedule, app_context
    ):
        """Should disable an enabled schedule."""
        assert sample_schedule.is_enabled is True
        toggled = SchedulerService.toggle_schedule(
            sample_schedule.id, db_user.id
        )
        assert toggled.is_enabled is False

    def test_toggle_enables_disabled(
        self, db_user, sample_schedule, app_context
    ):
        """Should enable a disabled schedule."""
        SchedulerService.toggle_schedule(sample_schedule.id, db_user.id)
        toggled = SchedulerService.toggle_schedule(
            sample_schedule.id, db_user.id
        )
        assert toggled.is_enabled is True
```

### Test File: `/Users/chris/Projects/shuffify/tests/services/test_job_executor_service.py`

```python
"""
Tests for JobExecutorService.

Tests the job execution logic with mocked Spotify API calls.
"""

import pytest
from unittest.mock import patch, Mock, MagicMock
from datetime import datetime, timezone

from shuffify.services.job_executor_service import (
    JobExecutorService,
    JobExecutionError,
)


@pytest.fixture
def mock_schedule():
    """Create a mock Schedule model instance."""
    schedule = Mock()
    schedule.id = 1
    schedule.user_id = 1
    schedule.job_type = "shuffle"
    schedule.target_playlist_id = "target_pl"
    schedule.target_playlist_name = "My Playlist"
    schedule.source_playlist_ids = ["source_1"]
    schedule.algorithm_name = "BasicShuffle"
    schedule.algorithm_params = {"keep_first": 0}
    schedule.is_enabled = True
    schedule.last_run_at = None
    schedule.last_status = None
    schedule.last_error = None
    return schedule


@pytest.fixture
def mock_user():
    """Create a mock User model instance."""
    user = Mock()
    user.id = 1
    user.spotify_id = "test_user"
    user.encrypted_refresh_token = "encrypted_token_data"
    return user


@pytest.fixture
def mock_api():
    """Create a mock SpotifyAPI instance."""
    api = Mock()
    api.get_playlist_tracks.return_value = [
        {"id": f"track{i}", "name": f"Track {i}", "uri": f"spotify:track:track{i}",
         "artists": [{"name": f"Artist {i}"}], "album": {"name": f"Album {i}"}}
        for i in range(1, 6)
    ]
    api.update_playlist_tracks.return_value = True
    api._ensure_valid_token.return_value = None
    api._sp = Mock()
    api.token_info = Mock()
    api.token_info.refresh_token = "original_refresh"
    return api


class TestExecuteRaid:
    """Tests for the raid execution logic."""

    def test_raid_adds_new_tracks(self, mock_schedule, mock_api):
        """Should add tracks from source not already in target."""
        mock_schedule.job_type = "raid"

        # Target has tracks 1-5
        mock_api.get_playlist_tracks.side_effect = [
            # First call: target tracks
            [{"id": f"track{i}", "uri": f"spotify:track:track{i}"} for i in range(1, 6)],
            # Second call: source tracks (includes some new ones)
            [{"id": f"track{i}", "uri": f"spotify:track:track{i}"} for i in range(4, 9)],
        ]

        result = JobExecutorService._execute_raid(mock_schedule, mock_api)

        # Tracks 6, 7, 8 are new (4 and 5 are duplicates)
        assert result["tracks_added"] == 3
        assert result["tracks_total"] == 8

    def test_raid_no_new_tracks(self, mock_schedule, mock_api):
        """Should report 0 additions when all tracks are duplicates."""
        mock_schedule.job_type = "raid"

        # Same tracks in both target and source
        same_tracks = [
            {"id": f"track{i}", "uri": f"spotify:track:track{i}"}
            for i in range(1, 4)
        ]
        mock_api.get_playlist_tracks.side_effect = [same_tracks, same_tracks]

        result = JobExecutorService._execute_raid(mock_schedule, mock_api)
        assert result["tracks_added"] == 0

    def test_raid_no_sources_skips(self, mock_schedule, mock_api):
        """Should skip when no source playlists configured."""
        mock_schedule.source_playlist_ids = []
        result = JobExecutorService._execute_raid(mock_schedule, mock_api)
        assert result["tracks_added"] == 0


class TestExecuteShuffle:
    """Tests for the shuffle execution logic."""

    def test_shuffle_reorders_playlist(self, mock_schedule, mock_api):
        """Should call update_playlist_tracks with shuffled URIs."""
        result = JobExecutorService._execute_shuffle(mock_schedule, mock_api)

        assert result["tracks_total"] == 5
        mock_api.update_playlist_tracks.assert_called_once()
        # The first arg should be the target playlist ID
        call_args = mock_api.update_playlist_tracks.call_args
        assert call_args[0][0] == "target_pl"
        # Second arg is list of URIs (length matches)
        assert len(call_args[0][1]) == 5

    def test_shuffle_no_algorithm_raises(self, mock_schedule, mock_api):
        """Should raise when algorithm_name is not set."""
        mock_schedule.algorithm_name = None

        with pytest.raises(JobExecutionError, match="no algorithm configured"):
            JobExecutorService._execute_shuffle(mock_schedule, mock_api)

    def test_shuffle_empty_playlist_returns_zero(self, mock_schedule, mock_api):
        """Should handle empty playlists gracefully."""
        mock_api.get_playlist_tracks.return_value = []
        result = JobExecutorService._execute_shuffle(mock_schedule, mock_api)
        assert result["tracks_total"] == 0


class TestGetSpotifyApi:
    """Tests for _get_spotify_api token management."""

    @patch("shuffify.services.job_executor_service.TokenService")
    @patch("shuffify.services.job_executor_service.SpotifyAPI")
    @patch("shuffify.services.job_executor_service.SpotifyAuthManager")
    @patch("shuffify.services.job_executor_service.SpotifyCredentials")
    def test_creates_api_with_decrypted_token(
        self, mock_creds, mock_auth, mock_api_class, mock_token_svc,
        mock_user, app_context
    ):
        """Should decrypt token and create SpotifyAPI instance."""
        mock_token_svc.decrypt_token.return_value = "decrypted_refresh"
        mock_api_instance = Mock()
        mock_api_instance.token_info.refresh_token = "decrypted_refresh"
        mock_api_class.return_value = mock_api_instance

        result = JobExecutorService._get_spotify_api(mock_user)
        mock_token_svc.decrypt_token.assert_called_once_with(
            "encrypted_token_data"
        )
        assert result == mock_api_instance

    def test_no_refresh_token_raises(self, mock_user):
        """Should raise when user has no stored token."""
        mock_user.encrypted_refresh_token = None

        with pytest.raises(JobExecutionError, match="no stored refresh token"):
            JobExecutorService._get_spotify_api(mock_user)
```

---

## Documentation Updates

**CHANGELOG.md** -- Add under `## [Unreleased]` / `### Added`:

```markdown
- **Scheduled Operations** - Automated playlist management via APScheduler
  - Configure recurring raid, shuffle, or combined operations
  - Background scheduler runs jobs without user interaction
  - Encrypted refresh token storage (Fernet) for secure background API access
  - New `/schedules` page for managing scheduled operations
  - Create, edit, toggle, delete, and manually trigger schedules
  - Execution history tracking per schedule
  - Max 5 schedules per user
  - Graceful handling of expired tokens, deleted playlists, rate limits
  - New models: `Schedule`, `JobExecution` (SQLite via Flask-SQLAlchemy)
  - New services: `TokenService`, `SchedulerService`, `JobExecutorService`
  - New routes: `GET /schedules`, `POST /schedules/create`, `PUT /schedules/<id>`,
    `DELETE /schedules/<id>`, `POST /schedules/<id>/toggle`,
    `POST /schedules/<id>/run`, `GET /schedules/<id>/history`
  - Pydantic validation: `ScheduleCreateRequest`, `ScheduleUpdateRequest`
  - Dashboard header now includes "Schedules" navigation link
```

---

## Edge Cases

### 1. User has never logged in since Phase 6 deployment
- `encrypted_refresh_token` will be `NULL` in the User table
- Scheduled jobs for that user will fail with a clear error: "no stored refresh token"
- The error is recorded in `JobExecution.error_message` and shown in the UI
- User must log in once (OAuth callback stores the token)

### 2. SECRET_KEY changes after tokens are stored
- All encrypted refresh tokens become undecryptable
- `TokenService.decrypt_token` raises `TokenEncryptionError` with message about corrupted token
- Users must log in again to re-store tokens
- The PBKDF2 salt is fixed, so only the SECRET_KEY matters

### 3. Spotify revokes the refresh token
- `SpotifyAuthManager.refresh_token()` will raise `SpotifyTokenError`
- Caught by `_get_spotify_api()`, wrapped in `JobExecutionError`
- Recorded as failed execution; user needs to re-authenticate

### 4. Target playlist deleted externally
- `SpotifyNotFoundError` raised during `get_playlist_tracks()`
- Caught and recorded as failed execution with descriptive error
- Schedule remains enabled (user can disable/delete it from the UI)

### 5. Rate limiting during job execution
- `SpotifyAPI` has built-in retry with exponential backoff (`@api_error_handler`)
- After max retries, `SpotifyRateLimitError` raised, caught as `SpotifyAPIError`
- Recorded as failed execution

### 6. Gunicorn with multiple workers
- APScheduler's `SQLAlchemyJobStore` uses database-level locking
- However, if two workers both start the scheduler, jobs may execute twice
- Mitigation: Use `gunicorn --preload` so the scheduler initializes once in the master process before forking
- Alternative: Set `SCHEDULER_ENABLED=false` on all workers except one (add a dedicated scheduler worker)
- Document this clearly in deployment guide

### 7. Werkzeug reloader in development
- `init_scheduler()` checks `WERKZEUG_RUN_MAIN` environment variable
- Only starts the scheduler in the main process, not the reloader child
- Already handled in the implementation

### 8. APScheduler job store table conflicts
- APScheduler creates its own `apscheduler_jobs` table
- This is separate from our `schedules` table (which is our business model)
- APScheduler's table handles job scheduling mechanics; ours handles user configuration
- No conflict since they serve different purposes

### 9. Raid adds too many tracks (approaching Spotify 10,000-track limit)
- The raid logic simply adds whatever is new; it does not check the 10,000-track limit
- If the Spotify API rejects the add, `SpotifyAPIError` is raised and recorded
- Future enhancement: check track count before adding

### 10. Schedule runs while user is actively editing the same playlist in Workshop
- The background job will overwrite tracks via `update_playlist_tracks`
- User's workshop `savedUris` will be stale
- If user then commits from Workshop, their order replaces the scheduled changes
- This is an acceptable tradeoff for a 25-user system -- document it

---

## Verification Checklist

```bash
# 1. Lint check (REQUIRED)
flake8 shuffify/

# 2. All tests pass (REQUIRED)
pytest tests/ -v

# 3. New tests pass specifically
pytest tests/services/test_token_service.py -v
pytest tests/services/test_scheduler_service.py -v
pytest tests/services/test_job_executor_service.py -v

# 4. Code formatting
black --check shuffify/

# 5. Database migration works
flask db migrate -m "Add Schedule, JobExecution, encrypted_refresh_token"
flask db upgrade

# 6. Quick combined check
flake8 shuffify/ && pytest tests/ -v && echo "Ready to push!"
```

Manual checks:
- [ ] Start dev server with `python run.py` -- scheduler initializes without errors
- [ ] Log in via Spotify OAuth -- verify "Stored encrypted refresh token" in logs
- [ ] Navigate to `/schedules` -- page loads with empty state
- [ ] Create a "Shuffle" schedule -- appears in the list
- [ ] Create a "Raid" schedule with source playlists -- appears correctly
- [ ] Click "Run Now" on a schedule -- executes and shows result
- [ ] Toggle a schedule off/on -- status updates correctly
- [ ] Delete a schedule -- removed from list
- [ ] Verify max 5 schedules enforced -- "New Schedule" button disables at limit
- [ ] Check scheduler logs for job registration messages
- [ ] Stop server -- verify "Scheduler shut down" in logs
- [ ] Dashboard shows "Schedules" link in header bar
- [ ] Existing dashboard and workshop features still work (no regression)

---

## What NOT To Do

1. **Do NOT use Celery or any external message broker.** APScheduler's `BackgroundScheduler` is deliberately chosen for the 25-user deployment. Adding Celery would require Redis as a broker (separate from session/cache use), a separate worker process, and dramatically more operational complexity.

2. **Do NOT store plaintext refresh tokens.** Always encrypt with `TokenService.encrypt_token()` before writing to the database. Never log or expose refresh tokens in error messages.

3. **Do NOT start the scheduler before the Flask app is fully configured.** The `init_scheduler()` call must come after SQLAlchemy, Flask-Session, and TokenService are all initialized. Calling it earlier will crash because the database tables do not exist yet.

4. **Do NOT use `app.run(use_reloader=True)` without the `WERKZEUG_RUN_MAIN` guard.** The reloader spawns two processes, and without the guard, two scheduler instances will run, causing duplicate job execution.

5. **Do NOT call `db.session` inside APScheduler callbacks without a Flask app context.** The `_execute_scheduled_job` wrapper pushes an app context with `with app.app_context()`. All database operations must happen inside this context.

6. **Do NOT skip Pydantic validation on schedule creation/update.** The `ScheduleCreateRequest` schema validates cross-field constraints (e.g., `source_playlist_ids` required for raid, `algorithm_name` required for shuffle). Without validation, invalid schedules can be created that will fail silently at runtime.

7. **Do NOT access `api._sp` directly in production code as a pattern.** The `_execute_raid` method uses `api._sp.playlist_add_items()` as a one-off because `SpotifyAPI` does not expose an "add items" method (only "replace all"). If a public method is added in a future phase, switch to it. Do not proliferate private attribute access.

8. **Do NOT allow schedule creation without a stored refresh token.** The UI should warn users if they have not logged in since Phase 6 was deployed. The route handler should check `db_user.encrypted_refresh_token` before allowing schedule creation and return a clear error if it is `None`. Add this check to the `create_schedule` route (Step 9c), after the `get_db_user()` call:

```python
    if not db_user.encrypted_refresh_token:
        return json_error(
            "Your account needs a fresh login to enable scheduled operations. "
            "Please log out and log back in.",
            400,
        )
```

9. **Do NOT let APScheduler's SQLAlchemyJobStore use a different database file than the app.** Both must point to the same SQLite database (via `SQLALCHEMY_DATABASE_URI`). Using a separate `shuffify_jobs.db` file would fragment the data and complicate backups. The `init_scheduler()` implementation in Step 4 already reads from `app.config["SQLALCHEMY_DATABASE_URI"]`.

10. **Do NOT modify the existing `/shuffle/<playlist_id>` or `/undo/<playlist_id>` routes.** The schedule routes are entirely additive. The background job executor writes to Spotify directly through `SpotifyAPI`, not through the existing route handlers.

11. **Do NOT run `flask db migrate` or `flask db upgrade` automatically in code.** Migrations must be run manually by the developer. Document the migration step clearly.

12. **Do NOT assume `schedule.source_playlist_ids` is always a list.** SQLite JSON columns can return `None` if the column was never set. Always default to empty list: `schedule.source_playlist_ids or []`. The model `default=list` handles new rows, but rows created before the column existed (or with explicit `NULL`) need the defensive `or []`.

---

## Additional Implementation Notes

### Gunicorn Deployment Configuration

For production with Gunicorn, add a `gunicorn_config.py` file (or document in existing deployment docs) that the scheduler should only run in one worker:

```python
# gunicorn_config.py
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv('GUNICORN_WORKERS', '2'))
preload_app = True  # CRITICAL: Ensures scheduler starts once in master process

# Alternatively, if preload is not used, set SCHEDULER_ENABLED=false
# and run a dedicated scheduler process.
```

If `preload_app = True` is not feasible (for example, if workers need independent app instances), then set the environment variable `SCHEDULER_ENABLED=false` for all Gunicorn workers and run the scheduler in a separate single-process entry point:

```python
# run_scheduler.py (standalone scheduler process)
from shuffify import create_app
import os
import time

app = create_app(os.getenv('FLASK_ENV', 'production'))

# The scheduler starts inside create_app via init_scheduler()
# This process just keeps running.
if __name__ == '__main__':
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        from shuffify.scheduler import shutdown_scheduler
        shutdown_scheduler()
```

Document both approaches in the deployment guide.

### Database Migration Dependency

The migration generated in Step 18 depends on Phase 5's migration (which creates the `users`, `workshop_sessions`, and `upstream_sources` tables). The Alembic migration chain must be:

1. Phase 5 migration: Creates `users`, `workshop_sessions`, `upstream_sources` tables
2. Phase 6 migration: Creates `schedules`, `job_executions` tables; adds `encrypted_refresh_token` column to `users`

If Phase 5's migration has not been applied, Phase 6's migration will fail because the `users` table does not exist. Always verify with `flask db current` before running `flask db upgrade`.

### Testing the Scheduler Service Tests

The `test_scheduler_service.py` tests in Step "Test Plan" require the Flask app context with a real SQLite database. The existing `app` fixture in `tests/conftest.py` creates a Flask app with `development` config. Phase 5 should add SQLAlchemy initialization and an in-memory SQLite database for testing. If that is not yet done, add this to the `app` fixture in `tests/conftest.py`:

```python
@pytest.fixture
def app():
    """Create a Flask application for testing."""
    import os
    os.environ['SPOTIFY_CLIENT_ID'] = 'test_client_id'
    os.environ['SPOTIFY_CLIENT_SECRET'] = 'test_client_secret'
    os.environ['SPOTIFY_REDIRECT_URI'] = 'http://localhost:5000/callback'
    os.environ['SECRET_KEY'] = 'test-secret-key-for-testing'

    from shuffify import create_app
    app = create_app('development')
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SCHEDULER_ENABLED'] = False  # Disable scheduler in tests

    with app.app_context():
        from shuffify.models.db import db
        db.create_all()

    return app
```

Note: `SCHEDULER_ENABLED = False` prevents APScheduler from starting during tests, which would cause threading issues and flaky tests.

---

### Critical Files for Implementation

- `/Users/chris/Projects/shuffify/shuffify/scheduler.py` - Core APScheduler lifecycle management; new file, the backbone of the entire phase
- `/Users/chris/Projects/shuffify/shuffify/services/job_executor_service.py` - Job execution engine that orchestrates raid/shuffle operations with token refresh; highest complexity
- `/Users/chris/Projects/shuffify/shuffify/services/token_service.py` - Fernet encryption/decryption for refresh tokens; security-critical component
- `/Users/chris/Projects/shuffify/shuffify/__init__.py` - App factory modifications to initialize TokenService and APScheduler in correct order
- `/Users/chris/Projects/shuffify/shuffify/models/db.py` - Schedule and JobExecution model definitions plus User model column addition (Phase 5 file to extend)