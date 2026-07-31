"""
Tests for bulk refresh-token re-encryption (SR-017).

Covers UserService.reencrypt_all_refresh_tokens and the
`flask rotate-token-encryption` CLI command that wraps it.
"""

import pytest
from cryptography.fernet import Fernet

from shuffify.models.db import db, User
from shuffify.services.token_service import TokenService
from shuffify.services.user_service import UserService


@pytest.fixture(autouse=True)
def _isolate_token_service():
    """Reset TokenService around every test in this module.

    TokenService is process-global class state; without this, a
    failing assertion mid-test would leak a dedicated key into
    whatever test runs next.
    """
    TokenService.reset()
    yield
    TokenService.reset()


def _seed_user(spotify_id: str, encrypted_token=None) -> User:
    """Insert a user row with an optional pre-encrypted token."""
    user = User(
        spotify_id=spotify_id,
        display_name=spotify_id,
        encrypted_refresh_token=encrypted_token,
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def dedicated_key():
    return Fernet.generate_key().decode()


@pytest.fixture
def legacy_then_dedicated(db_app, dedicated_key):
    """Seed tokens under the legacy SECRET_KEY derivation, then
    re-initialize TokenService with a dedicated key — the exact
    state of a deployment right after setting TOKEN_ENCRYPTION_KEY.

    Yields (plaintexts_by_spotify_id, dedicated_key).
    """
    secret = db_app.config["SECRET_KEY"]
    TokenService.reset()
    TokenService.initialize(secret)

    plaintexts = {
        "user_legacy_1": "refresh_token_one",
        "user_legacy_2": "refresh_token_two",
    }
    for spotify_id, token in plaintexts.items():
        _seed_user(spotify_id, TokenService.encrypt_token(token))

    TokenService.reset()
    TokenService.initialize(secret, token_encryption_key=dedicated_key)

    yield plaintexts, dedicated_key


class TestReencryptAllRefreshTokens:
    """Tests for the bulk re-encryption service method."""

    def test_reencrypts_legacy_tokens_to_primary(self, legacy_then_dedicated):
        """Legacy tokens move to the dedicated primary key and
        still decrypt to the same plaintext."""
        plaintexts, _ = legacy_then_dedicated

        result = UserService.reencrypt_all_refresh_tokens()

        assert result.total == 2
        assert result.reencrypted == 2
        assert result.already_current == 0
        assert result.failed == 0

        for spotify_id, plaintext in plaintexts.items():
            user = User.query.filter_by(spotify_id=spotify_id).one()
            assert TokenService.is_encrypted_with_primary(user.encrypted_refresh_token)
            assert TokenService.decrypt_token(user.encrypted_refresh_token) == plaintext

    def test_survives_secret_key_rotation_after_reencryption(
        self, legacy_then_dedicated
    ):
        """SR-017 end-to-end: re-encrypt, rotate SECRET_KEY,
        stored tokens still decrypt."""
        plaintexts, key = legacy_then_dedicated

        UserService.reencrypt_all_refresh_tokens()

        TokenService.reset()
        TokenService.initialize(
            "a-completely-new-secret-key",
            token_encryption_key=key,
        )

        for spotify_id, plaintext in plaintexts.items():
            user = User.query.filter_by(spotify_id=spotify_id).one()
            assert TokenService.decrypt_token(user.encrypted_refresh_token) == plaintext

    def test_already_current_tokens_left_untouched(self, legacy_then_dedicated):
        """Tokens already on the primary key are counted, not
        rewritten — the command is safely re-runnable."""
        UserService.reencrypt_all_refresh_tokens()
        second = UserService.reencrypt_all_refresh_tokens()

        assert second.reencrypted == 0
        assert second.already_current == 2
        assert second.failed == 0

    def test_dry_run_changes_nothing(self, legacy_then_dedicated):
        """Dry run reports what would happen without writing."""
        before = {u.spotify_id: u.encrypted_refresh_token for u in User.query.all()}

        result = UserService.reencrypt_all_refresh_tokens(dry_run=True)

        assert result.reencrypted == 2
        after = {u.spotify_id: u.encrypted_refresh_token for u in User.query.all()}
        assert after == before

    def test_undecryptable_token_reported_not_modified(self, legacy_then_dedicated):
        """A token no configured key can decrypt is reported as
        failed and left untouched; others still migrate."""
        corrupt = _seed_user("user_corrupt", "not-a-decryptable-token")

        result = UserService.reencrypt_all_refresh_tokens()

        assert result.total == 3
        assert result.reencrypted == 2
        assert result.failed == 1
        assert result.failed_spotify_ids == ["user_corrupt"]
        db.session.refresh(corrupt)
        assert corrupt.encrypted_refresh_token == "not-a-decryptable-token"

    def test_users_without_tokens_skipped(self, db_app):
        """NULL-token users are not counted at all."""
        TokenService.initialize(
            db_app.config["SECRET_KEY"],
            token_encryption_key=Fernet.generate_key().decode(),
        )
        _seed_user("user_no_token", None)

        result = UserService.reencrypt_all_refresh_tokens()

        assert result.total == 0


class TestRotateTokenEncryptionCommand:
    """Tests for the `flask rotate-token-encryption` command."""

    def test_command_reencrypts_and_reports(self, legacy_then_dedicated, db_app):
        runner = db_app.test_cli_runner()

        result = runner.invoke(args=["rotate-token-encryption"])

        assert result.exit_code == 0
        assert "re-encrypted: 2" in result.output
        for user in User.query.filter(User.encrypted_refresh_token.isnot(None)).all():
            assert TokenService.is_encrypted_with_primary(user.encrypted_refresh_token)

    def test_command_dry_run(self, legacy_then_dedicated, db_app):
        runner = db_app.test_cli_runner()
        before = {u.spotify_id: u.encrypted_refresh_token for u in User.query.all()}

        result = runner.invoke(args=["rotate-token-encryption", "--dry-run"])

        assert result.exit_code == 0
        assert "dry run" in result.output.lower()
        after = {u.spotify_id: u.encrypted_refresh_token for u in User.query.all()}
        assert after == before

    def test_command_exit_code_on_failures(self, legacy_then_dedicated, db_app):
        """Undecryptable tokens surface as a non-zero exit so
        operators notice, while good tokens still migrate."""
        _seed_user("user_corrupt", "not-a-decryptable-token")
        runner = db_app.test_cli_runner()

        result = runner.invoke(args=["rotate-token-encryption"])

        assert result.exit_code == 1
        assert "failed: 1" in result.output
        assert "user_corrupt" in result.output
        migrated = User.query.filter_by(spotify_id="user_legacy_1").one()
        assert TokenService.is_encrypted_with_primary(migrated.encrypted_refresh_token)
