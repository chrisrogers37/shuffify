"""
User service for managing user records in the database.

Handles user creation, retrieval, and the upsert-on-login pattern.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

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


@dataclass
class ReencryptionResult:
    """Outcome of a bulk refresh-token re-encryption pass.

    In dry-run mode ``reencrypted`` means "would be re-encrypted".
    """

    already_current: int = 0
    reencrypted: int = 0
    failed_spotify_ids: List[str] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.failed_spotify_ids)

    @property
    def total(self) -> int:
        return (
            self.already_current
            + self.reencrypted
            + self.failed
        )


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
            # Always update fields Spotify still returns
            user.display_name = user_data.get(
                "display_name"
            )
            user.profile_image_url = profile_image_url
            user.spotify_uri = user_data.get("uri")

            # Preserve existing DB values when API fields
            # are absent (Spotify Feb 2026 removed email,
            # country, product from GET /v1/me)
            if "email" in user_data:
                user.email = user_data["email"]
            if "country" in user_data:
                user.country = user_data["country"]
            if "product" in user_data:
                user.spotify_product = user_data[
                    "product"
                ]
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

    @staticmethod
    def store_refresh_token(
        user: "User", refresh_token: str
    ) -> None:
        """
        Encrypt and store a refresh token for a user.

        Args:
            user: The User model instance.
            refresh_token: The plaintext refresh token.
        """
        from shuffify.services.token_service import (
            TokenService,
        )

        if not TokenService.is_initialized():
            return

        user.encrypted_refresh_token = (
            TokenService.encrypt_token(refresh_token)
        )
        safe_commit(
            f"store refresh token for user "
            f"{user.spotify_id}",
            UserServiceError,
        )

    @staticmethod
    def reencrypt_all_refresh_tokens(
        dry_run: bool = False,
    ) -> ReencryptionResult:
        """
        Re-encrypt every stored refresh token under the
        primary key.

        Backs the `flask rotate-token-encryption` command.
        Tokens already on the primary key are left
        untouched, so the operation is safely re-runnable.
        Tokens no configured key can decrypt are reported
        and never modified — a failed row keeps its
        ciphertext so a later key configuration can still
        recover it. Rotation happens inside the crypto
        layer; plaintext tokens are never materialized
        here.

        Args:
            dry_run: When True, report what would change
                without writing anything.
        """
        from shuffify.services.token_service import (
            TokenService,
            TokenEncryptionError,
        )

        users = User.query.filter(
            User.encrypted_refresh_token.isnot(None)
        ).all()

        result = ReencryptionResult()

        for user in users:
            if TokenService.is_encrypted_with_primary(
                user.encrypted_refresh_token
            ):
                result.already_current += 1
                continue

            try:
                rotated = TokenService.rotate_token(
                    user.encrypted_refresh_token
                )
            except TokenEncryptionError:
                logger.warning(
                    "Cannot re-encrypt refresh token for "
                    "user %s: no configured key decrypts "
                    "it",
                    user.spotify_id,
                )
                result.failed_spotify_ids.append(
                    user.spotify_id
                )
                continue

            if not dry_run:
                user.encrypted_refresh_token = rotated
            result.reencrypted += 1

        if not dry_run and result.reencrypted:
            safe_commit(
                f"re-encrypt {result.reencrypted} "
                f"refresh token(s) under the primary key",
                UserServiceError,
            )

        return result
