---
title: "[plan] Person, workspace and Spotify connection tables — models, migration"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration branch
`feat/workspaces`, cut from main after 02 and after the security fixes
#126 and #128 to #131 have merged). Size: M.

This phase adds the new identity tables. It doesn't yet move any product
data (04), add sign-in (05) or change any route. There is no app-secret
column, key or service: workspaces authenticate to Spotify with a client ID
only (F8).

## Evidence

- `users` today is the Spotify account (`shuffify/models/db.py:72-174`).
- `TokenService` (`shuffify/services/token_service.py`) is a single Fernet
  under `TOKEN_ENCRYPTION_KEY`. It stays the only encryption in play: it
  encrypts the connection's refresh token.
- Migrations use an Alembic chain under `migrations/`, with chain tests in
  `tests/test_migration_chain_integrity.py` and
  `scripts/check-migration-chain.py`.
- #126 and #128 to #131 (draft PRs #144 and #145) rewrite code this phase's
  successors rewrite, so they merge to main first (overview, Dependencies).

## Implementation Plan

### Dependencies
02. Security fixes #126 and #128 to #131 merged to main and merged into the
branch at the cut.

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
     - `name`, default "My workspace";
     - timestamps.
   - `SpotifyConnection`:
     - `id`;
     - `workspace_id` (FK, unique, `ondelete=CASCADE`);
     - `client_id` (not null). It is **not** unique yet: phase 06 adds the
       unique constraint in the migration that deletes the stopgap in step 4,
       so the stopgap can give every connection the site app's ID without a
       collision;
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
3. **Give `SpotifyConnection` a `__repr__`** that leaves out the refresh
   token. The `encrypted_` key prefix is already in `_SENTRY_PII_DENYLIST`
   (`shuffify/__init__.py:440-453`), so no redaction list changes here.
4. **Keep today's sign-in working on the branch (a stopgap).** Until
   phase 05, the Spotify `/callback` finds or creates a person
   (`google_sub` null), that person's workspace, and its connection.
   - The connection takes the site app's `client_id` and `status = ok`.
   - It records the connection's `spotify_id`, profile and refresh token.
   - Every phase PR keeps the harness e2e suite green.
   - Phase 06 deletes this stopgap and, in the same migration, adds the
     unique constraint on `client_id`.

## Test Plan
- **Model tests:**
  - uniqueness of `google_sub`, `spotify_connections.workspace_id` and
    `spotify_id`; `workspaces.user_id` is non-unique (many workspaces per
    person); two connections may share a `client_id` until phase 06;
  - the cascade from person to workspace to connection;
  - the status and reason check;
  - the default workspace name.
- **A repr test:** `repr(SpotifyConnection)` contains no refresh token.
- **Migration tests:** the chain integrity and drift tests pass, and
  `create-schema` on an empty database matches the models.
- **Stopgap test:** two Spotify accounts signing in through the stopgap get
  two workspaces and two connections with no constraint error.

## Verification Checklist
- [ ] `pytest tests/models` passes.
- [ ] `python scripts/check-migration-chain.py` passes.
- [ ] The CI harness job is green on `feat/workspaces`.

## What NOT To Do
- Don't add a client-secret column, an app-secret key or a rotation
  command; PKCE needs none (F8) and PR #44 removed the last keyring.
- Don't make `client_id` unique here.
- Don't add an API endpoint or field that returns a refresh token, encrypted
  or not.
- Don't move product tables yet (that's 04).

## Context
- Source skill: forge · Area: models, config · Effort: M · Risk: Medium · Priority: High
