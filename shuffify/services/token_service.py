"""
Token encryption service for secure refresh token storage.

Fernet encryption with the dedicated TOKEN_ENCRYPTION_KEY.
"""

import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_NOT_INITIALIZED = "TokenService not initialized. Call initialize() first."


class TokenEncryptionError(Exception):
    """Raised when token encryption or decryption fails."""

    pass


def _fernet_from_key(key: str, source: str) -> Fernet:
    """Build a Fernet from a raw key string with a clear error."""
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as e:
        raise TokenEncryptionError(
            f"{source} is not a valid Fernet key: {e}. Generate one "
            'with: python -c "from cryptography.fernet import '
            'Fernet; print(Fernet.generate_key().decode())"'
        )


class TokenService:
    """Service for encrypting and decrypting Spotify refresh tokens."""

    _fernet: Optional[Fernet] = None

    @classmethod
    def initialize(cls, token_encryption_key: str) -> None:
        """
        Initialize the Fernet cipher.

        Must be called once during app startup (in create_app).

        Args:
            token_encryption_key: Raw Fernet key that encrypts and
                decrypts stored refresh tokens.

        Raises:
            TokenEncryptionError: If the key is missing or malformed.
        """
        if not token_encryption_key:
            raise TokenEncryptionError(
                "TOKEN_ENCRYPTION_KEY is required for token encryption"
            )
        cls._fernet = _fernet_from_key(token_encryption_key, "TOKEN_ENCRYPTION_KEY")
        logger.info("TokenService initialized successfully")

    @classmethod
    def _require_init(cls) -> None:
        if cls._fernet is None:
            raise TokenEncryptionError(_NOT_INITIALIZED)

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
        cls._require_init()

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
            TokenEncryptionError: If decryption fails.
        """
        cls._require_init()

        if not encrypted_token:
            raise TokenEncryptionError("Cannot decrypt empty token")

        try:
            decrypted = cls._fernet.decrypt(encrypted_token.encode("utf-8"))
            return decrypted.decode("utf-8")
        except InvalidToken:
            logger.error(
                "Token decryption failed: invalid token or a different key"
            )
            raise TokenEncryptionError(
                "Decryption failed: token is corrupted or was "
                "encrypted with a different key"
            )
        except Exception as e:
            logger.error(f"Token decryption failed: {e}")
            raise TokenEncryptionError(f"Decryption failed: {e}")

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if the TokenService has been initialized."""
        return cls._fernet is not None

    @classmethod
    def reset(cls) -> None:
        """Clear cipher state (test isolation helper)."""
        cls._fernet = None
