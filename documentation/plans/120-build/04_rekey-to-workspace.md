---
title: "[plan] Move product data from the Spotify account to the workspace — models, services, routes"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: XL.

Every table that belongs to a person's product data is re-keyed from
`user_id` to `workspace_id`, so the workspace becomes the source of truth.
Every service and route changes to scope by workspace. Code that needs the
Spotify account, for ownership checks and playlist reads, takes it from the
workspace's Spotify connection.

This is the phase that moves the Spotify account off `users`. Phase 03 only
added the new tables, and `users` still has `spotify_id` (not null, unique),
the profile fields and the encrypted refresh token. The migration here drops
those columns in the same PR that re-points every reader (about 50 reads in
19 files) and phase 02's refresher to the connection row, so the branch never
has a reader pointing at a column that's gone. Because the Spotify sign-in is
still the only sign-in until phase 05, the same PR carries the sign-in
stopgap that creates the person, workspace and connection (step 5).

The sweep is large: about 1,300 lines across about 200 files, tests included,
and now the column drop and the sign-in stopgap with it. That is why it is XL,
and why it stays one PR (F7).

## Evidence

**Tables with an FK to `users.id`** (`shuffify/models/db.py`):
- `user_settings` (L185)
- `workshop_sessions` (L273)
- `upstream_sources` (L348)
- `schedules` (L436)
- `routine_activation_receipts` (L548)
- `login_history` (L654): stays on the person
- `playlist_snapshots` (L722)
- `activity_log` (L832)
- `playlist_pairs` (L897)
- `raid_playlist_links` (L966)
- `playlist_preferences` (L1044)
- `track_locks` (L1126)
- `pending_raid_tracks` (L1219)

`job_executions.user_id` has no FK (L595).

**Code that scopes by user or Spotify id**
- `user_id` appears in 66 files under `shuffify/`.
- **`user_id` in `shuffify/spotify/cache.py` and `shuffify/spotify/api.py`
  means the Spotify account's id** (for example `api.py:153, 221-258`), not a
  database row. Those uses are never renamed, and the sweep must not touch
  them.
- `spotify_id` is the scoping key in `UpstreamSourceService`,
  `WorkshopSessionService`, `RaidSyncService` and
  `services/base.py:get_user_or_raise`. It's also the ownership argument to
  `validate_user_can_edit` / `read_complete` (e.g.
  `services/operations/runtime.py:344-378`).
- The worker takes a `credential:{user_id}` lock (`shuffify/worker.py:94-95`)
  to keep one user's runs from overlapping. Since phase 02 the refresh token
  itself is protected by a row lock inside a short refresh transaction, not
  by that lock.
- **The Spotify columns still on `users`** (phase 03 left them):
  `spotify_id` (`nullable=False`, unique, `db.py:86`), `country`,
  `spotify_product`, `spotify_uri` and `encrypted_refresh_token` (`db.py:93`).
  About 50 reads of `.spotify_id` or `.encrypted_refresh_token` in 19 files
  under `shuffify/` go through them, including `UserService.get_by_spotify_id`,
  `store_refresh_token`, `upsert_from_spotify`, the ownership checks and
  phase 02's refresher, which locks the `users` row until this phase.
- Admission checks for busy and needs-review spanned all users (#129). The
  fix may land on main before or after this phase; when it lands, merge main
  in, re-key whatever it leaves and keep its semantics.

## Implementation Plan

### Dependencies
03, and 02b, whose refresher this phase re-points.

### Blocks
05.

### Steps

1. **In one migration**, rename `user_id` to `workspace_id` on the 12 tables
   above (everything except `login_history`) and on `job_executions`.
   - Point the FKs at `workspaces.id` with `ondelete=CASCADE`. Add an FK to
     `job_executions.workspace_id`.
   - Rename every unique constraint and index that names `user_id`, keeping
     the same columns.
   - Rename `user_settings` to `workspace_settings`.
   - **Drop the Spotify columns from `users`:** `spotify_id`, `country`,
     `spotify_product`, `spotify_uri` and `encrypted_refresh_token`. The
     person keeps `google_sub`, `email`, `email_verified`, `display_name`,
     `profile_image_url` and the login columns. Production is wiped (09), so
     there is nothing to copy into the connection.
2. **Update the models,** their relationships and backrefs. `Workspace` gets
   the collections that `User` had. `User` keeps `login_history` and gains
   `workspaces`, and loses the Spotify columns.
3. **Re-key the code mechanically (F7).**
   - Services and routes take a `Workspace` (or `workspace_id`) wherever
     they took a `User` (or `user_id`).
   - Services that scoped by `spotify_id` scope by `workspace_id` instead.
   - The Spotify ownership checks use `workspace.spotify_connection.spotify_id`.
   - Replace `get_user_or_raise(spotify_id)` with a workspace lookup.
   - Every other reader of the dropped columns moves to the connection:
     `UserService`'s token read and write (`store_refresh_token`,
     `get_by_spotify_id`, `upsert_from_spotify`), the profile fields, and the
     rate-limit key. Nothing under `shuffify/` reads `user.spotify_id` or
     `user.encrypted_refresh_token` afterwards.
4. **Update the worker and the scheduler.**
   - Operation rows carry `workspace_id`.
   - The worker's run-serialisation lock becomes
     `credential:{spotify_connection_id}`. It is not what protects the refresh
     token.
   - **Re-point phase 02's refresher at the connection row, in this PR,
     together with the column drop.** Its `FOR NO KEY UPDATE` lock
     (`with_for_update(key_share=True)`, unchanged) now locks the
     `spotify_connections` row that holds the encrypted refresh token, the
     transaction-local `lock_timeout` is unchanged, and the refresher stores
     the rotated token there. The web and the worker both use it, and
     `JobExecutorService._get_spotify_api` takes the workspace and reads the
     token at refresh time inside the refresher. `credentials_for` is passed
     the connection, and still returns the site app's credentials until
     phase 06.
5. **Keep today's sign-in working on the branch (a stopgap).** With the
   Spotify columns gone from `users`, the Spotify `/callback` finds or creates
   a person (`google_sub` null), that person's workspace, and its connection.
   Until phase 05, this is the only sign-in.
   - The connection takes the site app's `client_id` and `status = ok`.
     (These rows exist only on the branch's development databases, because
     staging doesn't stand up until phase 06 and the stopgap is gone by
     then; phase 06's migration still deletes any such rows before adding the
     unique constraint.)
   - It records the connection's `spotify_id`, profile and refresh token.
   - Every phase PR keeps the harness e2e suite green.
   - The stopgap is routed through the real OAuth round trip against the fake
     Spotify, not seeded into the harness, so the e2e suite stays on the real
     flow until 06 replaces it.
   - Phase 05 replaces the sign-in, after which nothing starts this
     `/callback` any more, and phase 06 deletes it and, in the same
     migration, adds the unique constraint on `client_id`.
6. **Keep the request helpers working for now.** `require_auth_and_db` and
   `get_db_user` (`shuffify/routes/__init__.py:128-260`) resolve the
   workspace from the current session, which is still today's Spotify
   sign-in through the stopgap in step 5: the Spotify id in
   `session["user_data"]` finds the connection, and so the workspace. Don't
   rely on `_db_user_id` being set before `get_db_user()` runs:
   `require_auth()` runs first (`routes/__init__.py:159, 169`). The app keeps
   running on the branch until phase 05 replaces sign-in.
7. **Update the test factories and fixtures** to create a person, a
   workspace and a connection, and re-key every test that builds rows by
   user.

## Test Plan
- **A new isolation suite** (`tests/isolation/`): with two workspaces, each
  service's read and write paths return or change only their own
  workspace's rows. That covers schedules, snapshots, pairings, raids,
  preferences, locks, pending tracks, activity, workshop sessions and job
  history.
- **Cascade:** deleting a workspace removes its rows; the person's
  `login_history` remains.
- **The person has no Spotify columns:** a schema test asserts `users` has
  none of `spotify_id`, `country`, `spotify_product`, `spotify_uri` and
  `encrypted_refresh_token`, and that the connection has them.
- **The refresher on the connection row:** a refresh locks and updates the
  `spotify_connections` row (phase 02's refresher tests, re-run against it),
  and a refresh in `require_auth` works for a session whose `_db_user_id` was
  never set.
- **Stopgap test:** two Spotify accounts signing in through the stopgap get
  two people, two workspaces and two connections with no constraint error.
- The full backend suite and the harness e2e run pass on the branch.

## Verification Checklist
- [ ] `pytest tests/isolation` passes. It includes the schema test
      `tests/isolation/test_scoping_columns.py`, which finds a `user_id`
      column on exactly two tables, `workspaces` and `login_history`, and a
      `workspace_id` column on the 12 re-keyed tables and `job_executions`.
      That test is the proof; as a reviewer's aid,
      `git grep -nw "user_id" -- shuffify ':!shuffify/spotify/cache.py' ':!shuffify/spotify/api.py'`
      should list hits only in the person code (the `User`, `Workspace` and
      `LoginHistory` models, sign-in and session handling, and the login
      history and user services); the Spotify-account `user_id` in `cache.py`
      and `api.py` is excluded because it's never renamed.
- [ ] `pytest tests/models/test_user_is_a_person.py` passes (it asserts the
      five Spotify columns are gone from `users`), and
      `git grep -nE "user\.(spotify_id|encrypted_refresh_token)" -- shuffify`
      returns nothing.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't split this across PRs that leave the code half-renamed (F7), and
  don't drop the `users` Spotify columns in a different PR from the one that
  re-points their readers and the refresher.
- Don't change admission semantics.
- Don't rename the Spotify-account `user_id` in `shuffify/spotify/cache.py`
  and `api.py`.
- Don't add workspace switching.

## Context
- Source skill: forge · Area: models, services, routes, worker · Effort: XL · Risk: High · Priority: High
