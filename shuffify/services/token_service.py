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
            TokenEncryptionError: If encryption fails.
        """
        if cls._fernet is None:
            raise TokenEncryptionError(
                "TokenService not initialized. Call initialize() first."
            )

        if not plaintext_token:
            raise TokenEncryptionError("Cannot encrypt empty token")

        try:
            encrypted = cls._fernet.encrypt(
                plaintext_token.encode("utf-8")
            )
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
            TokenEncryptionError: If decryption fails.
        """
        if cls._fernet is None:
            raise TokenEncryptionError(
                "TokenService not initialized. Call initialize() first."
            )

        if not encrypted_token:
            raise TokenEncryptionError("Cannot decrypt empty token")

        try:
            decrypted = cls._fernet.decrypt(
                encrypted_token.encode("utf-8")
            )
            return decrypted.decode("utf-8")
        except InvalidToken:
            logger.error(
                "Token decryption failed: invalid token or wrong key"
            )
            raise TokenEncryptionError(
                "Decryption failed: token is corrupted "
                "or SECRET_KEY changed"
            )
        except Exception as e:
            logger.error(f"Token decryption failed: {e}")
            raise TokenEncryptionError(f"Decryption failed: {e}")

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if the TokenService has been initialized."""
        return cls._fernet is not None
