---
title: "[plan] Person, workspace and Spotify connection tables; app-secret encryption — models, services"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, workspaces, security]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration branch
`feat/workspaces`, cut from main after 02). Size: M.

This phase adds the new identity tables and the app-secret encryption. It
doesn't yet move any product data (04), add sign-in (05) or change any route.

## Evidence

- `users` today is the Spotify account (`shuffify/models/db.py:72-174`).
- `TokenService` (`shuffify/services/token_service.py`) is a single Fernet
  with no rotation.
- storydump's `src/utils/encryption.py` is the keyring pattern to follow.
- Migrations use an Alembic chain under `migrations/`, with chain tests in
  `tests/test_migration_chain_integrity.py` and
  `scripts/check-migration-chain.py`.

## Implementation Plan

### Dependencies
02.

### Blocks
04, 05, 06.

### Steps

1. **Models (`shuffify/models/db.py`).**
   - `User` becomes the person:
     - `google_sub` (unique). It stays nullable until phase 05 makes it
       required;
     - `email` (nullable) and `email_verified`;
     - `display_name` and `profile_image_url`;
     - `last_login_at`, `login_count` and timestamps.
   - The Spotify columns move to `SpotifyConnection`: `spotify_id`,
     `country`, `spotify_product`, `spotify_uri` and
     `encrypted_refresh_token`.
   - `Workspace`:
     - `id`;
     - `user_id` (FK `users.id`, `ondelete=CASCADE`, indexed);
     - `name`;
     - timestamps.
   - `SpotifyConnection`:
     - `id`;
     - `workspace_id` (FK, unique, `ondelete=CASCADE`);
     - `client_id` (unique, not null);
     - `encrypted_client_secret` (not null);
     - `spotify_id` (unique, nullable until first connect);
     - the profile fields;
     - `encrypted_refresh_token`;
     - `status` (`unchecked` | `ok` | `broken`, not null, default
       `unchecked`);
     - `broken_reason` (nullable) and `status_changed_at`;
     - timestamps.
   - A check constraint ties `broken_reason` to `status = 'broken'`.
2. **One migration** creates `workspaces` and `spotify_connections` and
   reshapes `users`. Production is wiped (09), so the migration may drop and
   add columns freely. Downgrade raises.
3. **Write `shuffify/services/app_secret_service.py`.**
   - It holds a `MultiFernet` built from `APP_SECRET_KEYS` (comma-separated,
     newest first) and refuses to start if that's unset in production, for
     both the api and the worker roles.
   - `encrypt(connection_id, secret)` stores
     `MultiFernet.encrypt(json({"cid": connection_id, "s": secret}))`.
   - `decrypt(connection_id, token)` checks that `cid` matches and raises
     `AppSecretMismatch` otherwise.
   - Nothing else decrypts.
   - Add the setting to `config.py`, `.railway/railway.ts` shared variables,
     `.railway/README.md`, `.env.example` and the harness config. The
     harness config gets a fixed test key.
4. **Add a CLI command, `flask rotate-app-secrets`,** that re-encrypts every
   `spotify_connections.encrypted_client_secret` under the newest key in one
   transaction and reports a count.
5. **Keep today's sign-in working on the branch (a stopgap).** Until
   phase 05, the Spotify `/callback` finds or creates a person
   (`google_sub` null), that person's workspace, and its connection.
   - The connection takes the site app's `client_id` and its secret
     (encrypted with `AppSecretService`), and `status = ok`.
   - It records the connection's `spotify_id`, profile and refresh token.
   - Every phase PR keeps the harness e2e suite green.
   - Phase 06 deletes this stopgap.
6. **Redact secrets.** Add the field names `client_secret` and
   `encrypted_client_secret` to the Sentry and log redaction list, next to
   the existing `SPOTIFY_CLIENT_SECRET` entry (`shuffify/__init__.py:486`).
   Give `SpotifyConnection` a `__repr__` that leaves out both secrets.

## Test Plan
- **Model tests:**
  - uniqueness of `google_sub`, `workspaces.user_id` (non-unique: many
    workspaces per person), `spotify_connections.workspace_id`, `client_id`
    and `spotify_id`;
  - the cascade from person to workspace to connection;
  - the status and reason check.
- **Secret tests:**
  - encrypt and decrypt round-trip;
  - a ciphertext moved to another connection's id is refused;
  - after a key rotation the old ciphertext still decrypts, and
    `rotate-app-secrets` rewrites it under the new key;
  - a missing `APP_SECRET_KEYS` refuses production boot.
- **A log-capture test:** encrypting, decrypting and building credentials
  never logs the plaintext secret.
- **Migration tests:** the chain integrity and drift tests pass, and
  `create-schema` on an empty database matches the models.

## Verification Checklist
- [ ] `pytest tests/models tests/services/test_app_secret_service.py` passes.
- [ ] `python scripts/check-migration-chain.py` passes.
- [ ] The CI harness job is green on `feat/workspaces`.

## What NOT To Do
- Don't reuse `TOKEN_ENCRYPTION_KEY` for app secrets.
- Don't add an API endpoint or field that returns a secret, encrypted or
  not.
- Don't move product tables yet (that's 04).

## Context
- Source skill: forge · Area: models, services, config · Effort: M · Risk: Medium · Priority: High
