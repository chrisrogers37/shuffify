---
title: "[plan] Move product data from the Spotify account to the workspace — models, services, routes"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: L.

Every table that belongs to a person's product data is re-keyed from
`user_id` to `workspace_id`, so the workspace becomes the source of truth.
Every service and route changes to scope by workspace. Code that needs the
Spotify account, for ownership checks and playlist reads, takes it from the
workspace's Spotify connection.

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
- `spotify_id` is the scoping key in `UpstreamSourceService`,
  `WorkshopSessionService`, `RaidSyncService` and
  `services/base.py:get_user_or_raise`. It's also the ownership argument to
  `validate_user_can_edit` / `read_complete` (e.g.
  `services/operations/runtime.py:344-378`).
- The worker takes a `credential:{user_id}` lock (`shuffify/worker.py:94-95`).
- Admission checks for busy and needs-review span all users (#129). Keep
  their semantics; don't fix #129 here.

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
   - The credential lock becomes `credential:{spotify_connection_id}`.
   - `JobExecutorService._get_spotify_api` takes the workspace and reads the
     refresh token from its connection.
5. **Keep the request helpers working for now.** `require_auth_and_db` and
   `get_db_user` (`shuffify/routes/__init__.py:128-260`) resolve the
   workspace from the current session, which is still today's Spotify
   sign-in through the stopgap in phase 03 step 5. The app keeps running on
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
- [ ] `git grep -n "user_id" -- shuffify` matches only the person tables
      (`users`, `workspaces.user_id`, `login_history`) and session code.
- [ ] `pytest tests/isolation` passes.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't split this across PRs that leave the code half-renamed (F7).
- Don't change admission semantics or fix #129 here.
- Don't add workspace switching.

## Context
- Source skill: forge · Area: models, services, routes, worker · Effort: L · Risk: High · Priority: High
