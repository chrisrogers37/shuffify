---
title: "[plan] Person, workspace and Spotify connection tables — models, migration"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration branch
`feat/workspaces`, cut from main when 02 merges). Size: S.

This phase is purely additive. It adds the new identity tables and two person
columns, and changes nothing that exists. `users` keeps every column it has,
the Spotify ones included: `spotify_id` (which is `nullable=False` and
unique), the profile fields and the encrypted refresh token. Nothing reads the
new tables yet, so no route, service, sign-in or worker changes, and today's
Spotify sign-in keeps working untouched.

**There is no stopgap in this phase.** Nothing has moved, so there is nothing
to keep working. Phase 04 moves the Spotify account's columns to the
connection and drops them from `users`, in the same PR that re-points every
reader and the refresher; the sign-in stopgap that keeps the harness green
once `users` has no Spotify columns belongs there.

There is no app-secret column, key or service: workspaces authenticate to
Spotify with a client ID only (F8).

## Evidence

- `users` today is the Spotify account (`shuffify/models/db.py:72-174`).
  `spotify_id` is `nullable=False, unique` (L86) and the encrypted refresh
  token is L93. About 50 places in 19 files under `shuffify/` read
  `.spotify_id` or `.encrypted_refresh_token`, including the ownership
  checks, `UserService.get_by_spotify_id` and `store_refresh_token`, and
  phase 02's refresher locks that row. Making those columns leave `users`
  here would break all of them until phase 04 re-points them, so the columns
  stay until phase 04 drops them with their readers.
- `TokenService` (`shuffify/services/token_service.py`) is a single Fernet
  under `TOKEN_ENCRYPTION_KEY`. It stays the only encryption in play: it
  encrypts the connection's refresh token.
- Migrations use an Alembic chain under `migrations/`, with chain tests in
  `tests/test_migration_chain_integrity.py` and
  `scripts/check-migration-chain.py`.
- #126 and #128 to #131 (draft PRs #144 and #145) touch code this phase's
  successors rewrite. They don't gate the branch: main is merged into it as
  each lands (overview, Dependencies).

## Implementation Plan

### Dependencies
02. Main is merged into the branch each time one of the security fixes
(#126, #128 to #131) lands.

### Blocks
04, 05, 06.

### Steps

1. **Models (`shuffify/models/db.py`).**
   - `User` gains two columns and loses none:
     - `google_sub` (unique). It stays nullable until phase 05 makes it
       required;
     - `email_verified`.

     Its existing `email`, `display_name`, `profile_image_url`,
     `last_login_at`, `login_count` and timestamps already exist and are
     untouched, as are `spotify_id`, `country`, `spotify_product`,
     `spotify_uri` and `encrypted_refresh_token`.
   - `Workspace`:
     - `id`;
     - `user_id` (FK `users.id`, `ondelete=CASCADE`, indexed);
     - `name`, default "My workspace";
     - timestamps.
   - `SpotifyConnection`:
     - `id`;
     - `workspace_id` (FK, unique, `ondelete=CASCADE`);
     - `client_id` (not null). It is **not** unique yet: phase 06 adds the
       unique constraint in the migration that deletes the stopgap rows
       (phase 04's and 05's), so those stopgaps can give every connection the
       site app's ID without a collision;
     - `spotify_id` (unique, nullable until first connect), `country`,
       `spotify_product` and `spotify_uri`: the Spotify account's columns
       that phase 04 moves here from `users`;
     - `encrypted_refresh_token`, which phase 04 also moves here;
     - `status` (`unchecked` | `ok` | `broken`, not null, default
       `unchecked`);
     - `broken_reason` (nullable) and `status_changed_at`;
     - `credential_generation` (integer, not null, default 0): bumped
       whenever the connection's credentials are replaced (a successful
       connect, or saving a different client ID, phase 06). A failure only
       marks the connection broken if it was produced by a client built under
       the current generation (phase 08), so a stale failure can't undo a
       reconnect;
     - timestamps.
   - A check constraint ties `broken_reason` to `status = 'broken'`.
2. **One additive migration** creates `workspaces` and `spotify_connections`
   and adds `users.google_sub` and `users.email_verified`. It drops, renames
   and relaxes nothing on `users`. Downgrade raises.
3. **Give `SpotifyConnection` a `__repr__`** that leaves out the refresh
   token. The `encrypted_` key prefix is already in `_SENTRY_PII_DENYLIST`
   (`shuffify/__init__.py:440-453`), so no redaction list changes here.

## Test Plan
- **Model tests:**
  - uniqueness of `google_sub`, `spotify_connections.workspace_id` and
    `spotify_id`; `workspaces.user_id` is non-unique (many workspaces per
    person); two connections may share a `client_id` until phase 06;
  - the cascade from person to workspace to connection;
  - the status and reason check;
  - the default workspace name.
- **Defaults:** a new connection has `credential_generation = 0`.
- **A repr test:** `repr(SpotifyConnection)` contains no refresh token.
- **`users` is unchanged:** a `users` row with a `spotify_id`, a refresh token
  and no `google_sub` still inserts and loads as before, and a schema test
  asserts that `users` still has `spotify_id`, `country`, `spotify_product`,
  `spotify_uri` and `encrypted_refresh_token`.
- **Migration tests:** the chain integrity and drift tests pass,
  `create-schema` on an empty database matches the models, and upgrading a
  database that has `users` rows keeps them and their Spotify columns and
  leaves the new tables empty.

## Verification Checklist
- [ ] `pytest tests/models` passes.
- [ ] `python scripts/check-migration-chain.py` passes.
- [ ] The migration drops no column (it contains no `drop_column` or
      `drop_constraint` on `users`).
- [ ] The existing sign-in and the harness e2e suite are unchanged and green
      on `feat/workspaces`.

## What NOT To Do
- Don't add a client-secret column, an app-secret key or a rotation
  command; PKCE needs none (F8) and PR #44 removed the last keyring.
- Don't make `client_id` unique here.
- Don't drop, rename or relax any `users` column here; phase 04 does that
  with the readers.
- Don't add a stopgap sign-in; nothing has moved.
- Don't add an API endpoint or field that returns a refresh token, encrypted
  or not.
- Don't move product tables yet (that's 04).

## Context
- Source skill: forge · Area: models, config · Effort: S · Risk: Low · Priority: High
