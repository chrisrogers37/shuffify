"""
User service for managing user records in the database.

Handles user creation, retrieval, and the upsert-on-login pattern.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from shuffify.models.db import db, User
from shuffify.services.base import safe_commit

logger = logging.getLogger(__name__)


class UserServiceError(Exception):
    """Base exception for user service operations."""

    pass


class UserNotFoundError(UserServiceError):
    """Raised when a user cannot be found."""

    pass


@dataclass
class UpsertResult:
    """Result of a user upsert operation."""

    user: User
    is_new: bool


class UserService:
    """Service for managing User records."""

    @staticmethod
    def upsert_from_spotify(
        user_data: Dict[str, Any],
    ) -> UpsertResult:
        """
        Create or update a User from Spotify profile data.

        Called on every successful OAuth login. If the user already
        exists (matched by spotify_id), update their profile fields,
        increment login_count, and set last_login_at. If the user
        does not exist, create a new record with login_count=1.

        Args:
            user_data: The Spotify user profile dictionary.
                Expected keys:
                - 'id' (str): Spotify user ID (REQUIRED)
                - 'display_name' (str): Display name
                - 'email' (str): Email address
                - 'images' (list): Image dicts with 'url' key
                - 'country' (str): ISO 3166-1 alpha-2 code
                - 'product' (str): Spotify subscription level
                - 'uri' (str): Spotify URI for the user

        Returns:
            UpsertResult with the User instance and whether
            it was a new user.

        Raises:
            UserServiceError: If the upsert fails.
        """
        spotify_id = user_data.get("id")
        if not spotify_id:
            raise UserServiceError(
                "Spotify user data missing 'id' field"
            )

        # Extract profile image URL (first image if available)
        images = user_data.get("images", [])
        profile_image_url = (
            images[0].get("url") if images else None
        )

        now = datetime.now(timezone.utc)

        user = User.query.filter_by(
            spotify_id=spotify_id
        ).first()

        if user:
            # Update existing user
            user.display_name = user_data.get(
                "display_name"
            )
            user.email = user_data.get("email")
            user.profile_image_url = profile_image_url
            user.country = user_data.get("country")
            user.spotify_product = user_data.get(
                "product"
            )
            user.spotify_uri = user_data.get("uri")
            user.last_login_at = now
            user.login_count = (
                user.login_count or 0
            ) + 1
            user.updated_at = now
            is_new = False
            logger.info(
                "Updated existing user: %s (%s)"
                " — login #%d",
                spotify_id,
                user_data.get("display_name", "Unknown"),
                user.login_count,
            )
        else:
            # Create new user
            user = User(
                spotify_id=spotify_id,
                display_name=user_data.get(
                    "display_name"
                ),
                email=user_data.get("email"),
                profile_image_url=profile_image_url,
                country=user_data.get("country"),
                spotify_product=user_data.get("product"),
                spotify_uri=user_data.get("uri"),
                last_login_at=now,
                login_count=1,
            )
            db.session.add(user)
            is_new = True
            logger.info(
                "Created new user: %s (%s)",
                spotify_id,
                user_data.get("display_name", "Unknown"),
            )

        safe_commit(
            f"upsert user {spotify_id}",
            UserServiceError,
        )

        # Auto-create default settings for new users
        if is_new:
            try:
                from shuffify.services.user_settings_service import (
                    UserSettingsService,
                )

                UserSettingsService.get_or_create(
                    user.id
                )
            except Exception as settings_err:
                # Settings creation failure should NOT
                # block login
                logger.warning(
                    "Failed to create default settings "
                    "for user %s: %s",
                    spotify_id,
                    settings_err,
                )

        return UpsertResult(user=user, is_new=is_new)

    @staticmethod
    def get_by_spotify_id(
        spotify_id: str,
    ) -> Optional[User]:
        """
        Look up a user by their Spotify ID.

        Args:
            spotify_id: The Spotify user ID.

        Returns:
            User instance if found, None otherwise.
        """
        if not spotify_id:
            return None
        return User.query.filter_by(
            spotify_id=spotify_id
        ).first()

    @staticmethod
    def get_by_id(user_id: int) -> Optional[User]:
        """
        Look up a user by their internal database ID.

        Args:
            user_id: The internal auto-increment ID.

        Returns:
            User instance if found, None otherwise.
        """
        return db.session.get(User, user_id)
