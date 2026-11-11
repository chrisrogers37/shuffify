---
title: "[plan] Broken app: detect, pause, guide, resume — worker, scheduler, web"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, workspaces]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (integration). Size: M.

When Spotify rejects a workspace's app, revokes its access, or refuses its
account, the workspace's connection is marked broken and its schedules stop
running. The person sees the "uh oh" page with the fix that matches the
reason. When the fix succeeds, the schedules run again at their next slot,
and missed runs aren't made up.

Whether schedules are paused is never stored: a schedule runs only while its
workspace's connection is `ok`. There is no `paused_reason` column.

## Evidence

- Phase 03 gives `credential_generation`, and phase 06 bumps it on every
  connect and replacement.
- Phase 02 gives the typed failures, `spotify_failure_of` and the error codes
  `spotify_app_rejected`, `spotify_grant_revoked` and
  `spotify_user_not_allowed`. Phase 06 gives `status`, `unchecked`→`ok` on a
  successful connect, and `SpotifyConnectionHealth.mark_ok`.
- **The scheduler** loads `is_enabled = True` rows
  (`services/worker_scheduler.py:298-357`). `admit_scheduled` rechecks
  `is_enabled` inside `capture` (`scheduled_admission.py:174-193`) and
  reports other skips by code. A skipped fire for a blocked playlist logs a
  warning (`scheduled_admission.py:207-214`, the #107 precedent).
- **The worker** claims a queued operation, then takes the credential lock
  and runs it (`shuffify/worker.py:85-100`).
- **Missed runs:** `eligibility_floor` takes the later of the schedule's
  `definition_changed_at` and its creation (`scheduled_admission.py:95-98`),
  and `recoverable_fire` never fires before that floor. A change to a field
  in `_FIELDS` (`services/schedule_notifications.py:57-100`) stamps
  `definition_changed_at`; that stamp is what stops missed runs being made
  up.
- **The design's "uh oh" mockup:** `documentation/plans/120-mockups/uh-oh.html`.

## Implementation Plan

### Dependencies
02, 06.

### Blocks
09.

### The status and action matrix

Connect (`GET /spotify/connect`) is allowed in every status, because it is
the fix for two of the three reasons. `mark_ok` runs only after the action
that fixes the reason, and only when the connect callback succeeds: that is
the one step that proves the client ID, the grant and the allow-list work
together.

| `broken_reason` | Spotify said | The person sees | The fix | Status changes | `mark_ok` after |
|---|---|---|---|---|---|
| `app_rejected` | `invalid_client`: the app was deleted, or Spotify no longer knows its client ID | "Spotify no longer recognises your app" | Create a new app (setup steps 1 to 3; delete the old one first if it still exists), then connect | `broken` → saving the new client ID sets `unchecked`, clears the reason and the refresh token → connect succeeds → `ok` | a successful connect with the new app, by the same Spotify account |
| `access_revoked` | `invalid_grant`: the refresh token no longer works, for example because the person removed Raccoon in their Spotify account's apps | "Spotify access was removed" | Connect Spotify again through the same app | `broken` → connect succeeds → `ok` | a successful connect (the exchange succeeds and the account matches the bound one) |
| `user_not_allowed` | a 403 saying the user isn't registered for the app | "Your account isn't on your app's list" | Add the account under User Management in Spotify's dashboard, then connect again (an app allows five users) | `broken` → connect succeeds → `ok`; a connect that still says "not registered" stays `broken` | a successful connect |

- Clicking "Connect" without succeeding changes nothing. Saving a different
  client ID never marks the app `ok`; only the connect that follows does.
- While the status is `broken` or `unchecked`, schedules stay paused.
  `unchecked` after a replacement is not a new pause: it is the same pause,
  derived from the same rule.
- **One rule for the connect callback: a notice before the first `ok`,
  broken after.** A connection that has never connected (no bound
  `spotify_id`) gets a notice on the setup page for `invalid_client` at the
  exchange or a "not registered" profile fetch, and its status is left as it
  is (`unchecked`): a new person who forgot the allow-list is told what to do
  and isn't sent to the "uh oh" page with a paused-schedules banner. A
  connection that has connected before (it has a bound `spotify_id`, including
  one whose app was replaced) is marked `broken` with the matching reason.
- A first-time connect that ends on Spotify's own "INVALID_CLIENT" page never
  returns, so it never reaches this table. `app_rejected` appears after a
  connect has worked once and the app later disappears.
- Transient failures (rate limits, timeouts, 5xx) never mark anything.

### Steps

1. **Derive "paused" from the connection.** A schedule is paused when its
   workspace's connection isn't `ok`.
   - In `admit_scheduled`'s `capture`, next to the `schedule_disabled` check,
     skip with code `app_broken` when the workspace's connection isn't `ok`,
     and log a warning with the schedule id, `scheduled_for` and workspace
     id, as the needs-review skip does (#107). Internal ids only.
   - The scheduler's `_refresh` is unchanged; admission is the check.
   - There is no `Schedule.paused_reason`, and `is_enabled` is never touched:
     it stays the person's own on/off switch.
2. **Add `SpotifyConnectionHealth.mark_broken(connection, reason, generation)`**
   in `shuffify/services/`. It applies only when `generation` equals the
   connection's current `credential_generation` (phase 03; bumped by every
   connect and every replacement), as one conditional update. In that
   transaction it sets `status = broken`, `broken_reason` and
   `status_changed_at`, and logs activity (workspace id and reason only). It
   touches no schedule. Calling it again for a connection that is already
   broken only updates the reason. When the generation is stale it does
   nothing and logs a warning with the workspace id: a failure that began
   before a reconnect can't undo the reconnect.
   - The generation travels with the failure. Phase 04's refresher reads
     `credential_generation` in the same row-locked read as the refresh
     token, and attaches it to any typed failure it raises
     (`failure.generation`); callers pass it through.
3. **Call it from every client path, only for the three typed failures
   found by `spotify_failure_of`.**
   - The worker: `_get_spotify_api` and the operation runtime.
   - The web, in one place: register a single Flask error handler for
     `SpotifyAppRejected`, `SpotifyGrantRevoked` and `SpotifyUserNotAllowed`
     in `shuffify/error_handlers.py`. It calls `mark_broken` with the
     failure's generation and returns `spotify_app_broken`, which the web app
     sends to `/setup/problem`. Flask picks the handler for the most specific
     class in an exception's hierarchy, so it wins over
     `handle_spotify_token_expired` (`error_handlers.py:328-333`, which says
     "Please log in again", a fix that can't help here) and over the ordinary
     403 handling. This covers a refresh in `require_auth`, a failed refresh
     in the 401 retry mid-request, and a "not registered" 403 on an ordinary
     call. `require_auth` re-raises a typed failure found on an
     `AuthenticationError`'s cause chain instead of returning `None`, so the
     handler sees it.
   - The connect callback, under the rule in the matrix notes: for
     `invalid_client` at the exchange and for a "not registered" profile
     fetch, a notice for a connection that has never connected and
     `mark_broken` for one that has.

   The run in flight ends `failed` with its code, as it does today. A queued
   operation for a workspace whose connection isn't `ok` is refused at claim
   time in `worker._run_one`, before the handler runs: it ends `failed` with
   `app_broken` and logs a warning, and Spotify is never called.
4. **Extend `SpotifyConnectionHealth.mark_ok(connection)`** (introduced in
   06). It is called only from the successful connect callback. In one
   transaction it sets `status = ok`, clears `broken_reason`, and sets
   `definition_changed_at` to now on the workspace's enabled schedules. That
   moves the eligibility floor, so each schedule runs at its next slot with
   no making up. It writes no other schedule field.
5. **Make the "uh oh" page, `/setup/problem`,** from the mockup:
   - the paused-schedules banner, showing the count of the workspace's
     enabled schedules (all of them are paused);
   - the fix for `broken_reason`, from the matrix above, with the screens
     for each;
   - "Use a different app" always offered. It says to delete the old app in
     Spotify's dashboard first (one app per developer), then goes to setup
     step 1.

   While a replacement is in progress (`unchecked` with a bound Spotify
   account), `/setup` shows the same paused-schedules banner.
6. **Show the state in the API.** `/api/session` `setup.spotify_app = broken`
   plus `broken_reason`. The schedules API shows `paused` and a derived
   `paused_reason` (`app_broken` when the connection is `broken`,
   `app_not_connected` when it's `unchecked`), so the Routines page can label
   paused schedules. Neither is stored.

## Test Plan
- **Worker tests,** one per reason: a typed failure marks the connection
  broken with that reason. Another workspace isn't touched, and no schedule
  row changes.
- **Matrix tests,** one per row:
  - the failure sets the reason;
  - the fix path (new client ID, or reconnect) leaves schedules paused until
    the connect succeeds;
  - a failed connect (for example, still not registered) stays `broken`;
  - a successful connect sets `ok` and clears the reason.
- **Scheduler tests:**
  - a schedule in a workspace that isn't `ok` is skipped with `app_broken`,
    and the skip logs a warning;
  - after `mark_ok` it fires at the next slot;
  - no catch-up fire for a slot missed while paused, inside the 3600s
    grace;
  - a schedule the person had disabled stays disabled after `mark_ok`;
  - a schedule enabled while the app is broken doesn't run.
- **Claim-time refusal** of queued runs for a workspace that isn't `ok`.
- **The web path,** for each of the three failures: raised by a refresh in
  `require_auth`, by the 401 retry's refresh mid-request, and (for "not
  registered") by a 403 on an ordinary call, the request marks the app broken
  and returns `spotify_app_broken`, never "Session expired. Please log in
  again."
- **A stale failure:** a failure carrying the old generation, arriving after
  a reconnect has bumped it and `mark_ok` has run, leaves the connection
  `ok` and logs a warning; the same failure with the current generation marks
  it broken.
- **First connect versus later:** a first connect (no bound `spotify_id`)
  that returns "not registered" or `invalid_client` shows the notice and
  leaves the status `unchecked`; the same results for a connection with a
  bound `spotify_id` mark it `broken`.
- **Playwright:** the "uh oh" page for each reason and its fix.
- **Harness e2e:** fake Spotify faults (from 02) and its deleted-app mode
  (from 06) break the app; the page appears; the fix resumes the schedules.
- **On staging:** the owner breaks the app for real (reset or remove the
  account's grant, remove the account from User Management) and confirms the
  page and the fix.

## Verification Checklist
- [ ] `pytest tests/services/test_spotify_connection_health.py tests/services/test_scheduled_admission_broken.py`
      passes, with one test per row of the matrix (both files exist and the
      run collects at least one test for each of the three reasons).
- [ ] The harness e2e broken-app tests pass for all three reasons.
- [ ] A schema test (`tests/models/test_no_stored_pause.py`) asserts that the
      `schedules` table has no pause column, and that `is_enabled` is
      unchanged by `mark_broken` and `mark_ok`. (A grep for `paused_reason`
      would pass today, so it proves nothing.)
- [ ] The staging drill passes.
- [ ] CI is green on `feat/workspaces`.

## What NOT To Do
- Don't flip `is_enabled` to pause; that's the person's own switch.
- Don't store a pause flag on schedules.
- Don't call `mark_ok` from saving an ID or from any step other than a
  successful connect.
- Don't refuse `/spotify/connect` because the app is broken.
- Don't retry a broken app automatically on a timer.
- Don't mark a connection broken from a failure whose generation isn't the
  current one.
- Don't mark a never-connected connection broken; it gets a notice.
- Don't make up missed runs.

## Context
- Source skill: forge · Area: worker, scheduler, services, web/setup · Effort: M · Risk: Medium · Priority: High
