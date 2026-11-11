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

The sweep is large: about 1,300 lines across about 200 files, tests included.
That is why it is XL, and why it stays one PR (F7).

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
- Admission checks for busy and needs-review spanned all users (#129). The
  fix may land on main before or after this phase; when it lands, merge main
  in, re-key whatever it leaves and keep its semantics.

## Implementation Plan

### Dependencies
03.

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
2. **Update the models,** their relationships and backrefs. `Workspace` gets
   the collections that `User` had. `User` keeps `login_history` and gains
   `workspaces`.
3. **Re-key the code mechanically (F7).**
   - Services and routes take a `Workspace` (or `workspace_id`) wherever
     they took a `User` (or `user_id`).
   - Services that scoped by `spotify_id` scope by `workspace_id` instead.
   - The Spotify ownership checks use `workspace.spotify_connection.spotify_id`.
   - Replace `get_user_or_raise(spotify_id)` with a workspace lookup.
4. **Update the worker and the scheduler.**
   - Operation rows carry `workspace_id`.
   - The worker's run-serialisation lock becomes
     `credential:{spotify_connection_id}`. It is not what protects the refresh
     token.
   - Re-point phase 02's refresher at the connection row: its
     `SELECT ... FOR UPDATE` now locks the `spotify_connections` row that
     holds the encrypted refresh token and reads `credential_generation`
     with it, so a failure can say which generation it came from (phase 08).
     The web and the worker both use it, and
     `JobExecutorService._get_spotify_api` takes the workspace and reads the
     token at refresh time inside the refresher.
5. **Keep the request helpers working for now.** `require_auth_and_db` and
   `get_db_user` (`shuffify/routes/__init__.py:128-260`) resolve the
   workspace from the current session, which is still today's Spotify
   sign-in through the stopgap in phase 03 step 4. The app keeps running on
   the branch until phase 05 replaces sign-in.
6. **Update the test factories and fixtures** to create a person, a
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
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't split this across PRs that leave the code half-renamed (F7).
- Don't change admission semantics.
- Don't rename the Spotify-account `user_id` in `shuffify/spotify/cache.py`
  and `api.py`.
- Don't add workspace switching.

## Context
- Source skill: forge · Area: models, services, routes, worker · Effort: XL · Risk: High · Priority: High
