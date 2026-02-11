"""
Tests for the TokenService (Fernet encryption/decryption).
"""

import pytest
from shuffify.services.token_service import (
    TokenService,
    TokenEncryptionError,
)


class TestTokenService:
    """Tests for TokenService encrypt/decrypt operations."""

    def setup_method(self):
        """Initialize TokenService before each test."""
        TokenService._fernet = None  # Reset state
        TokenService.initialize(
            "test-secret-key-for-unit-tests"
        )

    def teardown_method(self):
        """Reset TokenService after each test."""
        TokenService._fernet = None

    def test_initialize_success(self):
        """TokenService should initialize with a valid key."""
        assert TokenService.is_initialized() is True

    def test_initialize_empty_key_raises(self):
        """Empty secret key should raise."""
        TokenService._fernet = None
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
        TokenService._fernet = None
        TokenService.initialize("different-secret-key")

        with pytest.raises(
            TokenEncryptionError, match="corrupted"
        ):
            TokenService.decrypt_token(encrypted)

    def test_not_initialized_encrypt_raises(self):
        """Encrypting before initialization should raise."""
        TokenService._fernet = None
        with pytest.raises(
            TokenEncryptionError,
            match="not initialized",
        ):
            TokenService.encrypt_token("some_token")

    def test_not_initialized_decrypt_raises(self):
        """Decrypting before initialization should raise."""
        TokenService._fernet = None
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
