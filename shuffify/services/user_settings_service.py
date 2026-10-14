"""
User settings service for managing user preferences.

Handles get-or-create, update, and convenience methods
for reading specific preference values.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from shuffify.models.db import UserSettings, db
from shuffify.schemas.requests import ShuffleRequest
from shuffify.services.base import safe_commit
from shuffify.shuffle_algorithms.registry import ShuffleRegistry

logger = logging.getLogger(__name__)

# Constraints
MAX_SNAPSHOTS_LIMIT = 50
MIN_SNAPSHOTS_LIMIT = 1


class UserSettingsError(Exception):
    """Base exception for user settings operations."""

    pass


class UserSettingsService:
    """Service for managing UserSettings records."""

    @staticmethod
    def get_or_create(user_id: int) -> UserSettings:
        """
        Get existing settings or create defaults for a user.

        Args:
            user_id: The internal database user ID.

        Returns:
            UserSettings instance (existing or newly created).

        Raises:
            UserSettingsError: If the operation fails.
        """
        settings = UserSettings.query.filter_by(
            user_id=user_id
        ).first()

        if settings:
            return settings

        settings = UserSettings(user_id=user_id)
        db.session.add(settings)
        safe_commit(
            f"create default settings for user {user_id}",
            UserSettingsError,
        )
        return settings

    @staticmethod
    def get_default_shuffle_settings(user_id: int) -> dict[str, Any]:
        """Return the saved method and only its valid, explicitly saved options.

        Missing or retired methods remain unselected. Missing and invalid
        options are omitted so the UI can use the algorithm's own defaults.
        Reading stale settings never rewrites the user's stored preferences.
        """
        settings = UserSettingsService.get_or_create(user_id)
        algorithm = next(
            (
                item for item in ShuffleRegistry.list_algorithms()
                if item["class_name"] == settings.default_algorithm
            ),
            None,
        )
        if algorithm is None:
            return {"algorithm": None, "params": {}}

        params = {}
        saved_params = settings.default_algorithm_params
        if isinstance(saved_params, dict):
            for name, metadata in algorithm["parameters"].items():
                if name not in saved_params:
                    continue
                try:
                    validated = ShuffleRequest(
                        algorithm=algorithm["class_name"],
                        **{name: saved_params[name]},
                    ).get_algorithm_params()
                except ValidationError:
                    continue
                if name not in validated:
                    continue
                value = validated[name]
                # Metadata can constrain the visible controls more narrowly
                # than the shared request schema does.
                if "min" in metadata and value < metadata["min"]:
                    continue
                if "max" in metadata and value > metadata["max"]:
                    continue
                if "options" in metadata and value not in metadata["options"]:
                    continue
                params[name] = value

        return {"algorithm": algorithm["class_name"], "params": params}

    @staticmethod
    def update(
        user_id: int, **kwargs: Any
    ) -> UserSettings:
        """
        Update specific settings for a user.

        Only provided keyword arguments are updated. Unknown keys
        are silently ignored to allow forward compatibility.

        Args:
            user_id: The internal database user ID.
            **kwargs: Setting fields to update.

        Returns:
            Updated UserSettings instance.

        Raises:
            UserSettingsError: If validation or update fails.
        """
        settings = UserSettingsService.get_or_create(user_id)

        # Define the set of updatable fields
        updatable_fields = {
            "default_algorithm",
            "default_algorithm_params",
            "theme",
            "notifications_enabled",
            "auto_snapshot_enabled",
            "max_snapshots_per_playlist",
            "dashboard_show_recent_activity",
            "extra",
        }

        try:
            for key, value in kwargs.items():
                if key not in updatable_fields:
                    continue

                # Validate specific fields
                if (
                    key == "default_algorithm"
                    and value is not None
                ):
                    valid = set(
                        ShuffleRegistry.get_available_algorithms().keys()
                    )
                    if value not in valid:
                        raise UserSettingsError(
                            f"Invalid algorithm '{value}'. "
                            f"Valid: "
                            f"{', '.join(sorted(valid))}"
                        )

                if key == "theme":
                    if value not in UserSettings.VALID_THEMES:
                        raise UserSettingsError(
                            f"Invalid theme '{value}'. "
                            f"Valid: "
                            f"{', '.join(sorted(UserSettings.VALID_THEMES))}"
                        )

                if key == "max_snapshots_per_playlist":
                    if not isinstance(value, int):
                        raise UserSettingsError(
                            "max_snapshots_per_playlist "
                            "must be an integer"
                        )
                    if (
                        value < MIN_SNAPSHOTS_LIMIT
                        or value > MAX_SNAPSHOTS_LIMIT
                    ):
                        raise UserSettingsError(
                            "max_snapshots_per_playlist "
                            f"must be between "
                            f"{MIN_SNAPSHOTS_LIMIT} and "
                            f"{MAX_SNAPSHOTS_LIMIT}"
                        )

                setattr(settings, key, value)

            settings.updated_at = datetime.now(timezone.utc)
            safe_commit(
                f"update settings for user {user_id}: "
                f"{list(kwargs.keys())}",
                UserSettingsError,
            )
            return settings

        except UserSettingsError:
            db.session.rollback()
            raise
