"""
Tests for the TokenService (Fernet encryption/decryption).
"""

import pytest
from cryptography.fernet import Fernet

from shuffify.services.token_service import (
    TokenEncryptionError,
    TokenService,
)


class TestTokenService:
    """Tests for TokenService encrypt/decrypt operations."""

    def setup_method(self):
        """Initialize TokenService before each test."""
        TokenService.reset()
        TokenService.initialize(
            "test-secret-key-for-unit-tests"
        )

    def teardown_method(self):
        """Reset TokenService after each test."""
        TokenService.reset()

    def test_initialize_success(self):
        """TokenService should initialize with a valid key."""
        assert TokenService.is_initialized() is True

    def test_initialize_empty_key_raises(self):
        """Empty secret key should raise."""
        TokenService.reset()
        with pytest.raises(
            TokenEncryptionError,
            match="SECRET_KEY is required",
        ):
            TokenService.initialize("")

    def test_encrypt_and_decrypt_round_trip(self):
        """Encrypt then decrypt returns the original token."""
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
        with pytest.raises(
            TokenEncryptionError,
            match="Cannot encrypt empty",
        ):
            TokenService.encrypt_token("")

    def test_decrypt_empty_token_raises(self):
        """Decrypting an empty string should raise."""
        with pytest.raises(
            TokenEncryptionError,
            match="Cannot decrypt empty",
        ):
            TokenService.decrypt_token("")

    def test_decrypt_garbage_raises(self):
        """Decrypting invalid ciphertext should raise."""
        with pytest.raises(
            TokenEncryptionError, match="corrupted"
        ):
            TokenService.decrypt_token(
                "not-a-valid-fernet-token"
            )

    def test_decrypt_with_wrong_key_raises(self):
        """Token encrypted with one key cannot decrypt
        with another."""
        original = "secret_refresh_token"
        encrypted = TokenService.encrypt_token(original)

        # Re-initialize with a different key
        TokenService.reset()
        TokenService.initialize("different-secret-key")

        with pytest.raises(
            TokenEncryptionError, match="corrupted"
        ):
            TokenService.decrypt_token(encrypted)

    def test_not_initialized_encrypt_raises(self):
        """Encrypting before initialization should raise."""
        TokenService.reset()
        with pytest.raises(
            TokenEncryptionError,
            match="not initialized",
        ):
            TokenService.encrypt_token("some_token")

    def test_not_initialized_decrypt_raises(self):
        """Decrypting before initialization should raise."""
        TokenService.reset()
        with pytest.raises(
            TokenEncryptionError,
            match="not initialized",
        ):
            TokenService.decrypt_token("some_encrypted")

    def test_different_plaintexts_produce_different_ciphertexts(
        self,
    ):
        """Different inputs produce different outputs."""
        enc1 = TokenService.encrypt_token("token_a")
        enc2 = TokenService.encrypt_token("token_b")
        assert enc1 != enc2

    def test_same_plaintext_produces_different_ciphertexts(
        self,
    ):
        """Fernet uses a random IV, so same input
        produces different output."""
        enc1 = TokenService.encrypt_token("same_token")
        enc2 = TokenService.encrypt_token("same_token")
        assert enc1 != enc2  # Different ciphertexts
        # But both decrypt to the same value
        assert (
            TokenService.decrypt_token(enc1)
            == TokenService.decrypt_token(enc2)
        )


class TestTokenServiceDedicatedKey:
    """Tests for the dedicated TOKEN_ENCRYPTION_KEY path (SR-017).

    The scenarios here are the verification evidence for the
    key-decoupling migration: legacy tokens stay readable, and
    once tokens are on the dedicated key, SECRET_KEY rotation
    no longer invalidates them.
    """

    SECRET_A = "original-secret-key"
    SECRET_B = "rotated-secret-key"

    def setup_method(self):
        TokenService.reset()
        self.key_a = Fernet.generate_key().decode()
        self.key_b = Fernet.generate_key().decode()

    def teardown_method(self):
        TokenService.reset()

    def test_dedicated_key_round_trip(self):
        """Encrypt/decrypt works with a dedicated key."""
        TokenService.initialize(
            self.SECRET_A,
            token_encryption_key=self.key_a,
        )
        original = "refresh_token_value"
        encrypted = TokenService.encrypt_token(original)
        assert (
            TokenService.decrypt_token(encrypted)
            == original
        )

    def test_legacy_token_decrypts_after_dedicated_key_added(
        self,
    ):
        """Tokens encrypted under SECRET_KEY derivation
        remain readable when TOKEN_ENCRYPTION_KEY is
        introduced."""
        TokenService.initialize(self.SECRET_A)
        legacy_encrypted = TokenService.encrypt_token(
            "legacy_token"
        )

        TokenService.reset()
        TokenService.initialize(
            self.SECRET_A,
            token_encryption_key=self.key_a,
        )

        assert (
            TokenService.decrypt_token(legacy_encrypted)
            == "legacy_token"
        )

    def test_secret_key_rotation_preserves_dedicated_tokens(
        self,
    ):
        """SR-017 acceptance: with tokens on the dedicated
        key, rotating SECRET_KEY no longer invalidates
        them."""
        TokenService.initialize(
            self.SECRET_A,
            token_encryption_key=self.key_a,
        )
        encrypted = TokenService.encrypt_token(
            "token_value"
        )

        TokenService.reset()
        TokenService.initialize(
            self.SECRET_B,
            token_encryption_key=self.key_a,
        )

        assert (
            TokenService.decrypt_token(encrypted)
            == "token_value"
        )

    def test_secret_key_rotation_without_dedicated_key_breaks(
        self,
    ):
        """Status quo documented: without
        TOKEN_ENCRYPTION_KEY, SECRET_KEY rotation still
        invalidates stored tokens."""
        TokenService.initialize(self.SECRET_A)
        encrypted = TokenService.encrypt_token(
            "token_value"
        )

        TokenService.reset()
        TokenService.initialize(self.SECRET_B)

        with pytest.raises(TokenEncryptionError):
            TokenService.decrypt_token(encrypted)

    def test_secret_rotation_before_reencryption_breaks_legacy(
        self,
    ):
        """Legacy tokens die if SECRET_KEY rotates before
        re-encryption — this is why the rotation guide
        orders re-encrypt before rotate."""
        TokenService.initialize(self.SECRET_A)
        legacy_encrypted = TokenService.encrypt_token(
            "legacy_token"
        )

        TokenService.reset()
        TokenService.initialize(
            self.SECRET_B,
            token_encryption_key=self.key_a,
        )

        with pytest.raises(TokenEncryptionError):
            TokenService.decrypt_token(legacy_encrypted)

    def test_fallback_key_decrypts_old_primary(self):
        """Rotating TOKEN_ENCRYPTION_KEY with the old key
        in fallbacks keeps old tokens readable; new tokens
        use the new primary."""
        TokenService.initialize(
            self.SECRET_A,
            token_encryption_key=self.key_a,
        )
        old_encrypted = TokenService.encrypt_token(
            "old_token"
        )

        TokenService.reset()
        TokenService.initialize(
            self.SECRET_A,
            token_encryption_key=self.key_b,
            fallback_keys=[self.key_a],
        )

        assert (
            TokenService.decrypt_token(old_encrypted)
            == "old_token"
        )
        new_encrypted = TokenService.encrypt_token(
            "new_token"
        )
        assert TokenService.is_encrypted_with_primary(
            new_encrypted
        )
        assert not TokenService.is_encrypted_with_primary(
            old_encrypted
        )

    def test_dropping_fallback_breaks_unrotated_tokens(
        self,
    ):
        """Removing a fallback key before re-encrypting
        orphans tokens still on it — rotation must finish
        with the re-encryption command."""
        TokenService.initialize(
            self.SECRET_A,
            token_encryption_key=self.key_a,
        )
        old_encrypted = TokenService.encrypt_token(
            "old_token"
        )

        TokenService.reset()
        TokenService.initialize(
            self.SECRET_A,
            token_encryption_key=self.key_b,
        )

        with pytest.raises(TokenEncryptionError):
            TokenService.decrypt_token(old_encrypted)

    def test_invalid_dedicated_key_raises(self):
        """A malformed TOKEN_ENCRYPTION_KEY fails
        initialization with a message pointing at Fernet
        key generation."""
        with pytest.raises(
            TokenEncryptionError, match="Fernet"
        ):
            TokenService.initialize(
                self.SECRET_A,
                token_encryption_key="not-a-fernet-key",
            )

    def test_invalid_fallback_key_raises(self):
        """A malformed fallback key fails initialization."""
        with pytest.raises(
            TokenEncryptionError, match="Fernet"
        ):
            TokenService.initialize(
                self.SECRET_A,
                token_encryption_key=self.key_a,
                fallback_keys=["garbage"],
            )

    def test_is_encrypted_with_primary_requires_init(self):
        """Primary-key check before initialization
        raises."""
        with pytest.raises(
            TokenEncryptionError, match="not initialized"
        ):
            TokenService.is_encrypted_with_primary(
                "anything"
            )

    def test_fallbacks_active_without_dedicated_key(self):
        """Fallback keys are decrypt-only even in legacy
        mode (no dedicated key configured) — retiring a
        dedicated key back to fallbacks keeps its tokens
        readable."""
        TokenService.initialize(
            self.SECRET_A,
            token_encryption_key=self.key_a,
        )
        old_encrypted = TokenService.encrypt_token(
            "old_token"
        )

        TokenService.reset()
        TokenService.initialize(
            self.SECRET_A,
            fallback_keys=[self.key_a],
        )

        assert (
            TokenService.decrypt_token(old_encrypted)
            == "old_token"
        )
