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

The failure is seen where the Spotify client raises it, not where a route
happens to catch it: the client is built with the connection's credential
generation captured, and a hook at the raise calls `mark_broken`. So a service
that catches the failure can't hide a broken app, and a late failure from
before a reconnect can't undo the reconnect.

Whether schedules are paused is never stored: a schedule runs only while its
workspace's connection is `ok`. There is no `paused_reason` column.

## Evidence

- Phase 03 gives `credential_generation`, and phase 06 bumps it on every
  connect and replacement.
- Phase 02 gives the typed failures, `spotify_failure_of`, the error codes
  `spotify_app_rejected`, `spotify_grant_revoked` and
  `spotify_user_not_allowed`, and the failure hook slot (`on_typed_failure`) on
  `SpotifyHTTPClient`, `SpotifyAPI` and the refresher. The HTTP client calls it
  where it raises `SpotifyUserNotAllowed`, and the refresher after it releases
  its row lock. Phase 04 re-pointed that refresher to the connection row.
  Phase 06 gives `status`, `unchecked`→`ok` on a successful connect, and
  `SpotifyConnectionHealth.mark_ok`.
- **Services catch these failures before any Flask handler can** (phase 02,
  Evidence): `playlist_service.py:235, 303`, `workshop_commit_service.py:115,
  140, 169, 251, 392` and `workshop_finalize_service.py:374` re-map a 403 to
  `PlaylistAccessError`, and `routine_activation_service.py` and
  `playlist_setup_service.py` catch `SpotifyError` broadly. That is why
  detection can't live in a handler.
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
| `access_revoked` | `invalid_grant` at a refresh: the refresh token no longer works, for example because the person removed Raccoon in their Spotify account's apps | "Spotify access was removed" | Connect Spotify again through the same app | `broken` → connect succeeds → `ok` | a successful connect (the exchange succeeds and the account matches the bound one) |
| `user_not_allowed` | a 403 saying the user isn't registered for the app | "Your account isn't on your app's list" | Add the account under User Management in Spotify's dashboard, then connect again (an app allows five users) | `broken` → connect succeeds → `ok`; a connect that still says "not registered" stays `broken` | a successful connect |

- Clicking "Connect" without succeeding changes nothing. Saving a different
  client ID never marks the app `ok`; only the connect that follows does.
- While the status is `broken` or `unchecked`, schedules stay paused.
  `unchecked` after a replacement is not a new pause: it is the same pause,
  derived from the same rule.
- **One rule for the connect callback: a notice before the first `ok`,
  broken after, and `invalid_grant` never broken.** A connection that has
  never connected (no bound `spotify_id`) gets a notice on the setup page for
  `invalid_client` at the exchange or a "not registered" profile fetch, and
  its status is left as it is (`unchecked`): a new person who forgot the
  allow-list is told what to do and isn't sent to the "uh oh" page with a
  paused-schedules banner. A connection that has connected before (it has a
  bound `spotify_id`, including one whose app was replaced) is marked
  `broken` with the matching reason. An `invalid_grant` at the callback's code
  exchange (a reused or expired code, or a verifier lost with the session) is
  only the `oauth_failed` notice, in every status: `access_revoked` means a
  refresh failed, not a bad code.
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
   connect and every replacement), as one conditional update in its own short
   transaction that commits before it returns. In that transaction it sets
   `status = broken`, `broken_reason` and `status_changed_at`, and logs
   activity (workspace id and reason only). It touches no schedule. Calling
   it again for a connection that is already broken only updates the reason.
   When the generation is stale it does nothing and logs a warning with the
   workspace id: a failure from a client built before a reconnect can't undo
   the reconnect.
   - **The generation comes from the client's build, not from the failure.**
     `generation` is a required argument, supplied by the hook in step 3, so
     there is no failure without one to handle.
3. **Detect a typed failure where the client raises it, and call
   `mark_broken` from there.** Propagation can't be the detector, because
   services catch these failures first (Evidence) and a 403 on an ordinary call
   never passes through the refresher.
   - **Build the hook with the client.** The two builders,
     `AuthService.get_authenticated_api` (web) and
     `JobExecutorService._get_spotify_api` (worker), read the connection's
     `credential_generation` when they build the client and make the hook
     with `SpotifyConnectionHealth.failure_hook(connection)`: a callable that
     closes over the connection id and that generation, maps a typed failure
     to its reason (`SpotifyAppRejected` → `app_rejected`,
     `SpotifyGrantRevoked` → `access_revoked`, `SpotifyUserNotAllowed` →
     `user_not_allowed`) and calls `mark_broken(connection, reason,
     generation)`. The builders pass it to the refresher and to `SpotifyAPI`,
     which already take it (phase 02).
   - **It fires at the raise.** The HTTP client calls it where it raises
     `SpotifyUserNotAllowed` (a 403 on any call, whichever service catches it
     next), and the refresher calls it for `SpotifyAppRejected` and
     `SpotifyGrantRevoked` after releasing its row lock, so `mark_broken` never
     waits for a lock its own caller holds. The hook never raises, and its
     transaction commits before it returns, so the mark survives the
     request's own rollback when the failure propagates.
   - **The run in flight** ends `failed` with its code, as it does today. A
     queued operation for a workspace whose connection isn't `ok` is refused at
     claim time in `worker._run_one`, before the handler runs: it ends `failed`
     with `app_broken` and logs a warning, and Spotify is never called.
   - **A handler is left only for the response.** Register one Flask error
     handler for `SpotifyAppRejected`, `SpotifyGrantRevoked` and
     `SpotifyUserNotAllowed` in `shuffify/error_handlers.py`. It marks nothing:
     it answers `spotify_app_broken`, which the web app sends to
     `/setup/problem`. Flask picks the handler for the most specific class in an
     exception's hierarchy, so it wins over `handle_spotify_token_expired`
     (`error_handlers.py:328-333`, which says "Please log in again", a fix that
     can't help here) and over the ordinary 403 handling. `require_auth`
     re-raises a typed failure found by `spotify_failure_of` on an
     `AuthenticationError`'s cause chain instead of returning `None`, so the
     handler can answer for a failed refresh in `require_auth` or in the 401
     retry.
   - **A failure a service swallows still marks the app broken,** because the
     hook already fired at the raise. That one request shows whatever the
     service says for a refused playlist; the next request is refused by
     `require_auth` (step 4), and `/api/session` shows `broken`, so the web app
     goes to `/setup/problem`.
   - **A client with no connection has no hook and nothing to mark.**
     `credentials_for` raises `SpotifyAppUnavailable` when the workspace has no
     connection, so no client exists. The connect callback (phase 06) builds
     its clients without a hook and handles the three typed failures itself,
     under the rule in the matrix notes: `invalid_grant` at the exchange is a
     notice and never marks anything; `invalid_client` at the exchange and "not
     registered" at the profile fetch are a notice for a connection that has
     never connected, and for one that has, the callback calls `mark_broken`
     itself, with the generation it read when it started. None of them reaches
     the handler above.
4. **`require_auth` refuses a connection that isn't `ok`, before it builds a
   client.** Phase 06 made it return `spotify_not_connected` for `unchecked`;
   this adds `broken`, which returns `spotify_app_broken`, the same response
   the handler gives. A broken workspace's open session therefore stops calling
   Spotify with its still-valid access token; it isn't only redirected in the
   front end. The connect, save-app and `/api/session` routes check the
   signed-in person and their workspace, not `require_auth`, so they keep
   working while broken, which is how the fix is reached.
5. **Extend `SpotifyConnectionHealth.mark_ok(connection)`** (introduced in
   06). It is called only from the successful connect callback. In one
   transaction it sets `status = ok`, clears `broken_reason`, and sets
   `definition_changed_at` to now on the workspace's enabled schedules. That
   moves the eligibility floor, so each schedule runs at its next slot with
   no making up. It writes no other schedule field.
6. **Make the "uh oh" page, `/setup/problem`,** from the mockup:
   - the paused-schedules banner, showing the count of the workspace's
     enabled schedules (all of them are paused);
   - the fix for `broken_reason`, from the matrix above, with the screens
     for each;
   - "Use a different app" always offered. It says to delete the old app in
     Spotify's dashboard first (one app per developer), then goes to setup
     step 1.

   While a replacement is in progress (`unchecked` with a bound Spotify
   account), `/setup` shows the same paused-schedules banner.
7. **Show the state in the API.** `/api/session` `setup.spotify_app = broken`
   plus `broken_reason`. The schedules API shows `paused` and a derived
   `paused_reason` (`app_broken` when the connection is `broken`,
   `app_not_connected` when it's `unchecked`), so the Routines page can label
   paused schedules. Neither is stored.

## Test Plan
- **Worker tests,** one per reason: a typed failure through a worker-built
  client (the refresher's, and the HTTP client's 403) marks the connection
  broken with that reason. Another workspace isn't touched, and no schedule
  row changes.
- **Through a catching service:** `SpotifyUserNotAllowed` raised under
  `PlaylistService.get_playlist`, which re-maps `SpotifyForbiddenError` to
  `PlaylistAccessError`, still marks the app broken, and so does one raised
  under a service that catches `SpotifyError` broadly.
- **The hook doesn't wait for the lock:** a refresh that fails with
  `invalid_grant` marks the connection broken without waiting out
  `lock_timeout`.
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
- **`require_auth` refusal:** with the connection `broken`, a request through
  `require_auth_and_db` returns `spotify_app_broken` and the fake Spotify
  records no call from it; with `unchecked` it returns `spotify_not_connected`;
  the connect, save-app and `/api/session` routes still answer; after a
  successful connect the same session's request works again.
- **The web path,** for each of the three failures: raised by a refresh in
  `require_auth`, by the 401 retry's refresh mid-request, and (for "not
  registered") by a 403 on an ordinary call that reaches Flask, the connection
  is `broken` and the request returns `spotify_app_broken`, never "Session
  expired. Please log in again."
- **A stale failure:** a client built before a reconnect (it captured
  generation N), then a reconnect that bumps the generation and runs
  `mark_ok`: a typed failure raised through that old client leaves the
  connection `ok` and logs a warning. The same failure through a client built
  after the reconnect marks it broken.
- **First connect versus later:** a first connect (no bound `spotify_id`)
  that returns "not registered" or `invalid_client` shows the notice and
  leaves the status `unchecked`; the same results for a connection with a
  bound `spotify_id` mark it `broken`. An `invalid_grant` at the exchange shows
  the `oauth_failed` notice and marks nothing broken, for a never-connected,
  an `ok` and a `broken` connection.
- **Playwright:** the "uh oh" page for each reason and its fix.
- **Harness e2e:** fake Spotify faults (from 02) and its deleted-app mode
  (from 06) break the app; the page appears; the fix resumes the schedules.
- **On staging:** the owner breaks the app for real (reset or remove the
  account's grant, remove the account from User Management) and confirms the
  page and the fix. Removing the account from User Management is the case
  that a service can swallow, so watch that the page still appears on the next
  request.

## Verification Checklist
- [ ] `pytest tests/services/test_spotify_connection_health.py tests/services/test_scheduled_admission_broken.py`
      passes, and for each of `app_rejected`, `access_revoked` and
      `user_not_allowed`,
      `pytest --collect-only -q -k <reason> tests/services/test_spotify_connection_health.py tests/services/test_scheduled_admission_broken.py`
      lists at least one test.
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
- Don't refuse `/spotify/connect` because the app is broken, and don't put
  the connect, save-app or `/api/session` routes behind `require_auth`.
- Don't retry a broken app automatically on a timer.
- Don't rely on a failure reaching a Flask handler to detect a broken app, and
  don't mark anything broken from a handler.
- Don't mark a connection broken from a client built under an older
  generation.
- Don't mark a never-connected connection broken; it gets a notice. Don't
  mark any connection broken from an `invalid_grant` at the connect exchange.
- Don't call the hook while holding the connection row lock.
- Don't make up missed runs.

## Context
- Source skill: forge · Area: worker, scheduler, services, web/setup · Effort: M · Risk: Medium · Priority: High
