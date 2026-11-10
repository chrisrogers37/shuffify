---
title: "[plan] Broken app: detect, pause, guide, resume — worker, scheduler, web"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: M.

When Spotify rejects a workspace's app, revokes its access, or refuses its
account, the workspace is marked broken and its schedules pause. The person
sees the "uh oh" page with the matching fix. Once they fix it, the schedules
resume at their next slot, and missed runs aren't made up.

## Evidence

- Phase 02 gives the typed failures and the error codes
  `spotify_app_rejected`, `spotify_grant_revoked` and
  `spotify_user_not_allowed`.
- **Schedules have no pause reason** (`Schedule`,
  `shuffify/models/db.py:426-543`).
- **The scheduler** loads `is_enabled = True` rows
  (`services/worker_scheduler.py:298-357`). `admit_scheduled` rechecks
  `is_enabled` (`scheduled_admission.py:174-193`).
- **Missed runs:** a change to a field in `_FIELDS`
  (`services/schedule_notifications.py:57-100`) stamps
  `definition_changed_at` and notifies. That bump is what stops missed runs
  being made up (`scheduled_admission.py:101-118`).
- **The design's "uh oh" mockup:** `documentation/plans/120-mockups/uh-oh.html`.

## Implementation Plan

### Dependencies
02, 06.

### Blocks
09.

### Steps

1. **Add `Schedule.paused_reason`** (nullable; `'app_broken'` is the only
   value). It's separate from `is_enabled`, which stays the person's own
   on/off switch.
   - Add it to `_FIELDS`, so a change notifies the scheduler and bumps
     `definition_changed_at`.
   - Filter it out in both `_refresh` and `admit_scheduled` (skip code
     `app_broken`).
2. **Add `SpotifyConnectionHealth.mark_broken(connection, reason)`** in
   `shuffify/services/`. In one transaction it:
   - sets `status = broken`, `broken_reason` and `status_changed_at`;
   - sets `paused_reason = 'app_broken'` on every enabled schedule in the
     workspace;
   - logs activity.

   Reasons: `app_rejected` (`invalid_client`; an app that was deleted gives
   the same error), `access_revoked` (`invalid_grant`) and
   `user_not_allowed`.
3. **Call it from both client paths.**
   - The worker: `_get_spotify_api` and the operation runtime, when a typed
     failure from 02 surfaces.
   - The web: `require_auth`, when a refresh fails with a typed failure.

   The run in flight ends `failed` with its code, as it does today.
   Queued runs for that workspace are refused at claim time with
   `app_broken` instead of calling Spotify.
4. **Add `SpotifyConnectionHealth.mark_ok(connection)`.** It's called when
   a key check succeeds (06), when connect succeeds, and when a new secret
   is saved and checked. In one transaction it sets `status = ok` and clears
   `paused_reason` on the workspace's schedules. The `definition_changed_at`
   bump means each schedule runs at its next slot, with no making up.
5. **Make the "uh oh" page, `/setup/problem`,** from the mockup:
   - the paused-schedules banner, showing the count of paused schedules;
   - the fix for `broken_reason`:
     - `app_rejected`: paste a new secret (save and check), or "use a
       different app", which goes to setup step 2;
     - `access_revoked`: reconnect Spotify;
     - `user_not_allowed`: add the account under User Management, then
       reconnect;
   - "Use a different app" is always offered.

   If, in testing against real Spotify, `invalid_client` turns out to mean
   both "bad secret" and "deleted app", the page shows both fixes under
   `app_rejected`, which it already does.
6. **Show the state in the API.** `/api/session` `setup.spotify_app = broken`
   plus `broken_reason`. The schedules API shows `paused_reason`, so the
   Routines page can label paused schedules.

## Test Plan
- **Worker tests,** one per reason: a typed failure marks the connection
  broken and pauses exactly that workspace's enabled schedules. Another
  workspace isn't touched.
- **Scheduler tests:**
  - a paused schedule isn't admitted;
  - after `mark_ok` it fires at the next slot;
  - no catch-up fire for a slot missed while paused, inside the 3600s
    grace;
  - a schedule the person had disabled stays disabled after `mark_ok`.
- **Claim-time refusal** of queued runs for a broken workspace.
- **Playwright:** the "uh oh" page for each reason and its fix.
- **Harness e2e:** fake Spotify faults (from 02) break the app; the page
  appears; a fix resumes the schedules.

## Verification Checklist
- [ ] `pytest tests/services -k "broken or paused or health"` passes.
- [ ] The harness e2e broken-app tests pass for all three reasons.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't flip `is_enabled` to pause; that's the person's own switch.
- Don't retry a broken app automatically on a timer.
- Don't make up missed runs.

## Context
- Source skill: forge · Area: worker, scheduler, services, web/setup · Effort: M · Risk: Medium · Priority: High
