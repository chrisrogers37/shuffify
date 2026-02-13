"""
User settings service for managing user preferences.

Handles get-or-create, update, and convenience methods
for reading specific preference values.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from shuffify.models.db import db, UserSettings
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
        try:
            settings = UserSettings.query.filter_by(
                user_id=user_id
            ).first()

            if settings:
                return settings

            settings = UserSettings(user_id=user_id)
            db.session.add(settings)
            db.session.commit()

            logger.info(
                "Created default settings for user %d",
                user_id,
            )
            return settings

        except Exception as e:
            db.session.rollback()
            logger.error(
                "Failed to get/create settings for user "
                "%d: %s",
                user_id,
                e,
                exc_info=True,
            )
            raise UserSettingsError(
                f"Failed to get or create settings: {e}"
            )

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
            db.session.commit()

            logger.info(
                "Updated settings for user %d: %s",
                user_id,
                list(kwargs.keys()),
            )
            return settings

        except UserSettingsError:
            db.session.rollback()
            raise
        except Exception as e:
            db.session.rollback()
            logger.error(
                "Failed to update settings for user "
                "%d: %s",
                user_id,
                e,
                exc_info=True,
            )
            raise UserSettingsError(
                f"Failed to update settings: {e}"
            )

    @staticmethod
    def get_default_algorithm(
        user_id: int,
    ) -> Optional[str]:
        """
        Get the user's default shuffle algorithm name.

        Args:
            user_id: The internal database user ID.

        Returns:
            Algorithm class name string, or None if not set.
        """
        settings = UserSettings.query.filter_by(
            user_id=user_id
        ).first()

        if not settings:
            return None

        return settings.default_algorithm
