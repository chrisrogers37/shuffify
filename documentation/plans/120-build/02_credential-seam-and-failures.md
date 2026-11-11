---
title: "[plan] One credential seam, failures that keep their cause, one refresh token — auth, worker"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, auth]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (main). Size: L. There's no
visible change. This phase does three things:
- routes every place that gets Spotify app credentials through one function;
- makes Spotify's failures keep their cause (revoked access, rejected app,
  account not allowed) all the way to the code that will act on it, without
  changing which handlers catch them today;
- makes the database the only home of the refresh token: the web and the
  worker both refresh under the per-user credential lock, and the session
  stops keeping its own copy.

Phases 06 and 08 build on all three, and they're safe on main today.

## Evidence

**Where credentials are read:**
- `shuffify/services/auth_service.py:66-82`: `_credentials()` and
  `_auth_manager()`, which fall back to `from_env` at L76-77;
- `shuffify/services/executors/base_executor.py:95-96`;
- docstring examples in `shuffify/spotify/api.py:86`, `auth.py:149` and
  `spotify/__init__.py:28`.

**What failures lose:**
- `shuffify/spotify/auth.py:302-312` (refresh) and L247-249 (exchange)
  raise a generic `SpotifyTokenError` and drop the OAuth `error` field.
- `shuffify/spotify/http_client.py:322-337` logs the refresh failure and
  raises `SpotifyTokenExpiredError("Token expired or invalid")`, losing the
  cause.
- A 403 always becomes `SpotifyForbiddenError` (L410).
- Spotify's "not registered" text is already matched at
  `shuffify/routes/core.py:220`.
- The worker wraps a failed refresh in `JobExecutionError`
  (`base_executor.py:135-136`, `raise ... from e`) and the web wraps it in
  `AuthenticationError` (`auth_service.py:222-224`, with no `from`). Nothing
  that catches those two reads the cause.

**The existing hierarchy** (`shuffify/spotify/exceptions.py`):
- `SpotifyError` → `SpotifyAuthError` → `SpotifyTokenError` →
  `SpotifyTokenExpiredError`;
- `SpotifyError` → `SpotifyAPIError` → `SpotifyForbiddenError`.

Catchers that must keep matching:
- `except SpotifyTokenError` in `auth_service.py:222` and `auth.py:260, 327`;
- `SpotifyTokenExpiredError` in `http_client.py:442` (re-raised untouched)
  and `handle_spotify_token_expired` (`error_handlers.py:329, 621`);
- `SpotifyForbiddenError` handling for playlist 403s.

**Two copies of the refresh token.**
- The worker takes `credential:{user_id}` for the whole run
  (`shuffify/worker.py:94-95`) and stores a rotated token in the database
  (`base_executor.py:112-125`).
- A web refresh reads the refresh token from the session, and
  `_persist_token_to_session` (`auth_service.py:230-255`) writes only the
  session. The database copy goes stale, and so does the session's after a
  worker rotation. A PKCE refresh can invalidate the old token (phase 06), so
  one side would then fail with `invalid_grant`.
- Sign-in already stores the database copy (`routes/core.py:175`,
  `UserService.store_refresh_token(user, refresh_token)`).

## Implementation Plan

### Dependencies
None.

### Blocks
03, 06, 08.

### Steps

1. **Add `shuffify/spotify/credentials.py: credentials_for(owner) -> SpotifyCredentials`.**
   - In this phase it returns `SpotifyCredentials.from_flask_config(current_app.config)`
     and raises `SpotifyAppUnavailable` when the ID or secret is empty.
   - Remove `from_env` (`credentials.py:73-89`) and the fallback in
     `auth_service.py:76-77`. Update the three docstring examples to use
     `credentials_for`.
   - `owner` is the `User` today; phase 06 changes it to the workspace's
     Spotify connection and drops the secret.
2. **Give `AuthService.get_auth_url`, `exchange_code_for_token`,
   `get_authenticated_api` and `revoke_access_token` an explicit credentials
   argument.** Routes get the credentials from `credentials_for`.
   `base_executor.py:95` calls `credentials_for(user)`.
3. **Add typed failures in `shuffify/spotify/exceptions.py`, under the
   existing classes.** Each keeps the OAuth `error` and `error_description`:
   - `SpotifyAppRejected` (`invalid_client`) subclasses
     `SpotifyTokenExpiredError`;
   - `SpotifyGrantRevoked` (`invalid_grant`) subclasses
     `SpotifyTokenExpiredError`;
   - `SpotifyUserNotAllowed`, a 403 whose body says the user isn't
     registered for the app, subclasses `SpotifyForbiddenError`.

   The first two are "a token that can't be refreshed", so
   `except SpotifyTokenExpiredError` and `except SpotifyTokenError` keep
   matching them. The third is still a 403, so existing 403 handling matches;
   new code tests for the subclass first. None of them subclasses
   `SpotifyAuthError` alone.

   Raise them from `auth.py` (refresh and exchange) and from
   `http_client.py`, where the 401 retry re-raises the refresh's typed
   failure instead of a generic `SpotifyTokenExpiredError`. Playlist-level
   403s stay `SpotifyForbiddenError`.
4. **Carry the cause through both wrappers.**
   - `JobExecutionError` and `AuthenticationError` gain a `cause` attribute
     set from the Spotify failure that triggered them, and `auth_service.py`
     raises its `AuthenticationError` `from` the original. Both keep their
     messages and base classes.
   - Add `spotify_failure_of(exc)`, which returns the first
     `SpotifyAppRejected`, `SpotifyGrantRevoked` or `SpotifyUserNotAllowed`
     found on `exc` or its `cause`/`__cause__` chain, or `None`. Phase 08 and
     the operation runtime use it; nothing parses messages.
5. **Map the new failures to error codes** the worker records on a failed
   run: `spotify_app_rejected`, `spotify_grant_revoked` and
   `spotify_user_not_allowed`. In `operations/commands/routines.py:359-415`,
   use these in place of the generic `write_rejected` when
   `spotify_failure_of` finds one.
6. **Make the database the only home of the refresh token.**
   - Add a refresher around `SpotifyAuthManager` that `get_authenticated_api`
     and the worker's `_get_spotify_api` hand to `SpotifyAPI` in place of the
     bare auth manager (`SpotifyAPI` only calls `refresh_token` and
     `ensure_valid_token`, `api.py:141, 187`). Each refresh:
     1. takes the per-user credential lock, `credential:{user.id}`, the same
        name the worker takes (`worker.py:95`), through the operation
        repository's `target_locks`;
     2. reads and decrypts the stored refresh token;
     3. refreshes with it;
     4. stores the rotated token, encrypted, before releasing the lock.
   - The worker already holds that lock for the whole run, and a nested
     acquisition shares ownership, so only its token read moves to refresh
     time (the closure at `base_executor.py:112-125` goes).
   - If the lock isn't acquired in time, the refresh fails as a transient
     read failure, never as a typed app failure.
   - The web path needs the signed-in user's database id; the session
     already carries it as `_db_user_id`. With no stored token, the request
     is treated as signed out.
   - Sign-in stops putting the refresh token in the session
     (`routes/core.py:151`), and `is_authenticated`
     (`routes/__init__.py`) drops any refresh token an existing
     session still carries. `_persist_token_to_session` writes the access
     token and its expiry only.
7. **Teach the fake Spotify to fail on demand.** In
   `harness/fake_spotify/app.py`, add `/_fake/faults` modes that return
   these three failures, so the harness can produce each one.

## Test Plan
- Unit tests:
  - each OAuth error body maps to its typed exception;
  - a typed refresh failure is still caught by `except SpotifyTokenError`
    and `except SpotifyTokenExpiredError`, and `handle_spotify_token_expired`
    still returns its 401; a 403 "not registered" body is caught by
    `except SpotifyForbiddenError` and maps to `SpotifyUserNotAllowed`, while
    a playlist 403 stays `SpotifyForbiddenError`;
  - a 401, then a failed refresh, re-raises the typed cause;
  - `spotify_failure_of` finds the cause through `JobExecutionError` and
    `AuthenticationError`, and returns `None` for an unrelated error;
  - `credentials_for` raises `SpotifyAppUnavailable` with empty config.
- Refresh tests (real Postgres, as the operation lock tests use):
  - a web refresh after a worker rotation uses the rotated token;
  - a web refresh stores its rotated token, and the worker then uses it;
  - two refreshes at once run one after the other, never in parallel;
  - the session never holds a refresh token after sign-in or any request;
  - a missing lock is a transient failure, not an app failure.
- A worker test: a run whose refresh fails with `invalid_client` ends
  `failed` with `spotify_app_rejected`.
- The existing suites stay green.

## Verification Checklist
- [ ] `git grep -n "from_env\|from_flask_config\|SpotifyCredentials(" -- shuffify ':!shuffify/spotify/credentials.py'`
      returns nothing: only `credentials_for` builds credentials.
- [ ] `git grep -n "refresh_token" -- shuffify/routes shuffify/services/auth_service.py`
      shows no write of a refresh token into the session.
- [ ] Each of the three faults in the fake Spotify produces its error code in
      the harness.
- [ ] CI is green.

## What NOT To Do
- Don't add any workspace concept yet.
- Don't change what users see.
- Don't change which handlers catch today's exceptions; new types subclass
  the specific existing ones.
- Don't treat every 403 as an app failure; playlist 403s are ordinary.
- Don't read a refresh token from the session or hold one in a closure
  across a refresh.

## Context
- Source skill: forge · Area: shuffify/spotify, services/auth_service.py, executors · Effort: L · Risk: Medium · Priority: High
