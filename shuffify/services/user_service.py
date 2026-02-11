"""
User service for managing user records in the database.

Handles user creation, retrieval, and the upsert-on-login pattern.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from shuffify.models.db import db, User

logger = logging.getLogger(__name__)


class UserServiceError(Exception):
    """Base exception for user service operations."""

    pass


class UserNotFoundError(UserServiceError):
    """Raised when a user cannot be found."""

    pass


class UserService:
    """Service for managing User records."""

    @staticmethod
    def upsert_from_spotify(user_data: Dict[str, Any]) -> User:
        """
        Create or update a User from Spotify profile data.

        Called on every successful OAuth login. If the user already exists
        (matched by spotify_id), update their profile fields and timestamp.
        If the user does not exist, create a new record.

        Args:
            user_data: The Spotify user profile dictionary. Expected keys:
                - 'id' (str): Spotify user ID (REQUIRED)
                - 'display_name' (str): Display name
                - 'email' (str): Email address
                - 'images' (list): List of image dicts with 'url' key

        Returns:
            The User instance (created or updated).

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

        try:
            user = User.query.filter_by(
                spotify_id=spotify_id
            ).first()

            if user:
                # Update existing user
                user.display_name = user_data.get("display_name")
                user.email = user_data.get("email")
                user.profile_image_url = profile_image_url
                user.updated_at = datetime.now(timezone.utc)
                logger.info(
                    f"Updated existing user: {spotify_id} "
                    f"({user_data.get('display_name', 'Unknown')})"
                )
            else:
                # Create new user
                user = User(
                    spotify_id=spotify_id,
                    display_name=user_data.get("display_name"),
                    email=user_data.get("email"),
                    profile_image_url=profile_image_url,
                )
                db.session.add(user)
                logger.info(
                    f"Created new user: {spotify_id} "
                    f"({user_data.get('display_name', 'Unknown')})"
                )

            db.session.commit()
            return user

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to upsert user {spotify_id}: {e}",
                exc_info=True,
            )
            raise UserServiceError(
                f"Failed to save user record: {e}"
            )

    @staticmethod
    def get_by_spotify_id(spotify_id: str) -> Optional[User]:
        """
        Look up a user by their Spotify ID.

        Args:
            spotify_id: The Spotify user ID.

        Returns:
            User instance if found, None otherwise.
        """
        if not spotify_id:
            return None
        return User.query.filter_by(spotify_id=spotify_id).first()

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
