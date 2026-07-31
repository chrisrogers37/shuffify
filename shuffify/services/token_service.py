"""
Token encryption service for secure refresh token storage.

Fernet encryption behind a MultiFernet chain: TOKEN_ENCRYPTION_KEY is
the primary (encrypting) key when set, with a SECRET_KEY-derived
legacy key as the fallback primary otherwise; TOKEN_ENCRYPTION_KEY_FALLBACKS
entries and the legacy key are accepted for decryption only. Key
semantics, rotation procedure, and the migration path are documented
in documentation/guides/credential-rotation.md.
"""

import base64
import logging
from functools import lru_cache
from typing import Optional, Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# Salt for the legacy SECRET_KEY derivation. Kept verbatim so tokens
# encrypted under the legacy scheme remain decryptable through the
# fallback chain.
_SALT = b"shuffify-refresh-token-encryption-v1"

_NOT_INITIALIZED = "TokenService not initialized. Call initialize() first."


class TokenEncryptionError(Exception):
    """Raised when token encryption or decryption fails."""

    pass


@lru_cache(maxsize=8)
def _derive_legacy_key(secret_key: str) -> bytes:
    """Derive the legacy Fernet key from SECRET_KEY via PBKDF2.

    Cached: 480k PBKDF2 iterations cost ~0.1s per call, and the
    derivation is a pure function of the secret.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret_key.encode("utf-8")))


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

    _multi_fernet: Optional[MultiFernet] = None
    _primary: Optional[Fernet] = None

    @classmethod
    def initialize(
        cls,
        secret_key: str,
        token_encryption_key: Optional[str] = None,
        fallback_keys: Sequence[str] = (),
    ) -> None:
        """
        Initialize the Fernet cipher chain.

        Must be called once during app startup (in create_app).

        Args:
            secret_key: The Flask app's SECRET_KEY string. Always
                required — it feeds the legacy-derived key that keeps
                pre-migration tokens decryptable.
            token_encryption_key: Dedicated raw Fernet key used to
                encrypt. When None, encryption falls back to the
                SECRET_KEY-derived key (legacy behavior).
            fallback_keys: Retired Fernet keys accepted for
                decryption only, so TOKEN_ENCRYPTION_KEY itself can
                rotate without invalidating stored tokens.

        Raises:
            TokenEncryptionError: If any key is missing or malformed.
        """
        if not secret_key:
            raise TokenEncryptionError("SECRET_KEY is required for token encryption")

        try:
            legacy = Fernet(_derive_legacy_key(secret_key))
        except Exception as e:
            logger.error(f"Failed to initialize TokenService: {e}")
            raise TokenEncryptionError(f"Failed to derive encryption key: {e}")

        if token_encryption_key:
            primary = _fernet_from_key(token_encryption_key, "TOKEN_ENCRYPTION_KEY")
        else:
            logger.warning(
                "TOKEN_ENCRYPTION_KEY is not set; deriving the token "
                "encryption key from SECRET_KEY. Rotating SECRET_KEY "
                "will invalidate all stored refresh tokens. See "
                "documentation/guides/credential-rotation.md."
            )
            primary = legacy

        chain = [primary]
        chain += [
            _fernet_from_key(key, "TOKEN_ENCRYPTION_KEY_FALLBACKS entry")
            for key in fallback_keys
        ]
        if primary is not legacy:
            chain.append(legacy)

        cls._primary = primary
        cls._multi_fernet = MultiFernet(chain)
        logger.info("TokenService initialized successfully")

    @classmethod
    def _require_init(cls) -> None:
        if cls._multi_fernet is None:
            raise TokenEncryptionError(_NOT_INITIALIZED)

    @classmethod
    def encrypt_token(cls, plaintext_token: str) -> str:
        """
        Encrypt a refresh token for database storage.

        Always encrypts with the primary key, so every write moves a
        token onto the current key regardless of what it was
        previously encrypted with.

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
            encrypted = cls._multi_fernet.encrypt(plaintext_token.encode("utf-8"))
            return encrypted.decode("utf-8")
        except Exception as e:
            logger.error(f"Token encryption failed: {e}")
            raise TokenEncryptionError(f"Encryption failed: {e}")

    @classmethod
    def decrypt_token(cls, encrypted_token: str) -> str:
        """
        Decrypt a refresh token retrieved from the database, trying
        every key in the configured chain.

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
            decrypted = cls._multi_fernet.decrypt(encrypted_token.encode("utf-8"))
            return decrypted.decode("utf-8")
        except InvalidToken:
            logger.error(
                "Token decryption failed: invalid token or no "
                "configured key can decrypt it"
            )
            raise TokenEncryptionError(
                "Decryption failed: token is corrupted or was "
                "encrypted with a key that is no longer configured"
            )
        except Exception as e:
            logger.error(f"Token decryption failed: {e}")
            raise TokenEncryptionError(f"Decryption failed: {e}")

    @classmethod
    def rotate_token(cls, encrypted_token: str) -> str:
        """
        Re-encrypt a stored token under the primary key without
        exposing the plaintext to the caller.

        Args:
            encrypted_token: Ciphertext under any configured key.

        Returns:
            Ciphertext under the primary key.

        Raises:
            TokenEncryptionError: If no configured key can decrypt
                the token.
        """
        cls._require_init()

        if not encrypted_token:
            raise TokenEncryptionError("Cannot rotate empty token")

        try:
            rotated = cls._multi_fernet.rotate(encrypted_token.encode("utf-8"))
            return rotated.decode("utf-8")
        except InvalidToken:
            raise TokenEncryptionError(
                "Rotation failed: token is corrupted or was "
                "encrypted with a key that is no longer configured"
            )
        except Exception as e:
            logger.error(f"Token rotation failed: {e}")
            raise TokenEncryptionError(f"Rotation failed: {e}")

    @classmethod
    def is_encrypted_with_primary(cls, encrypted_token: str) -> bool:
        """
        Check whether a stored token is encrypted under the primary
        key (as opposed to a fallback or the legacy derived key).

        Used by the re-encryption command to tell migrated tokens
        from ones still pending.

        Raises:
            TokenEncryptionError: If the service is not initialized.
        """
        cls._require_init()

        try:
            cls._primary.decrypt(encrypted_token.encode("utf-8"))
            return True
        except Exception:
            return False

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if the TokenService has been initialized."""
        return cls._multi_fernet is not None

    @classmethod
    def reset(cls) -> None:
        """Clear cipher state (test isolation helper)."""
        cls._multi_fernet = None
        cls._primary = None
