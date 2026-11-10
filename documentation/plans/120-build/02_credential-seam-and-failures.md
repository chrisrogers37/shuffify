---
title: "[plan] One credential seam, and Spotify failures that keep their cause — auth, worker"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
tags: [enhancement, auth]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (main). Size: M. There's no
visible change. This phase does three things:
- routes every place that gets Spotify app credentials through one function;
- makes Spotify's sign-in failures keep their cause (bad secret, revoked
  access, account not allowed);
- stores the refresh token when a web request refreshes it.

Phases 06 and 08 build on all three, and they're safe on main today.

## Evidence

**Where credentials are read:**
- `shuffify/services/auth_service.py:66-82`: `_credentials()` and
  `_auth_manager()`, which fall back to `from_env` at L76-77;
- `shuffify/services/executors/base_executor.py:95-96`.

**What failures lose:**
- `shuffify/spotify/auth.py:302-312` (refresh) and L247-249 (exchange)
  raise a generic `SpotifyTokenError` and drop the OAuth `error` field.
- `shuffify/spotify/http_client.py:322-337` logs the refresh failure and
  raises `SpotifyTokenExpiredError("Token expired or invalid")`, losing the
  cause.
- A 403 always becomes `SpotifyForbiddenError` (L410).
- Spotify's "not registered" text is already matched at
  `shuffify/routes/core.py:220`.

**The stale refresh token:** `_persist_token_to_session`
(`shuffify/services/auth_service.py:230-255`) updates only the session. The
worker keeps the database copy current (`base_executor.py:112-125`), but a
web refresh doesn't.

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
     `auth_service.py:76-77`.
   - `owner` is the `User` today; phase 06 changes it to the workspace's
     Spotify connection.
2. **Give `AuthService.get_auth_url`, `exchange_code_for_token`,
   `get_authenticated_api` and `revoke_access_token` an explicit credentials
   argument.** Routes get the credentials from `credentials_for`.
   `base_executor.py:95` calls `credentials_for(user)`.
3. **Add typed failures in `shuffify/spotify/exceptions.py`.** Each keeps
   the OAuth `error` and `error_description`:
   - `SpotifyAppRejected` (`invalid_client`);
   - `SpotifyGrantRevoked` (`invalid_grant`);
   - `SpotifyUserNotAllowed`: a 403 whose body says the user isn't
     registered for the app;
   - all three subclass `SpotifyAuthError`, so existing handlers keep
     working.

   Raise them from `auth.py` (refresh and exchange) and from
   `http_client.py`, where the 401 retry re-raises the refresh's typed
   failure instead of a generic one. Playlist-level 403s stay
   `SpotifyForbiddenError`.
4. **Persist web refreshes.** In `_persist_token_to_session`, when the
   refresh token changes, also call
   `UserService.store_refresh_token(user_id, …)`. Use the `_db_user_id` from
   the session; skip it when that's absent.
5. **Map the new failures to error codes** the worker records on a failed
   run: `spotify_app_rejected`, `spotify_grant_revoked` and
   `spotify_user_not_allowed`. In `operations/commands/routines.py:359-415`,
   use these in place of the generic `write_rejected` when the cause is one
   of the new types.
6. **Teach the fake Spotify to fail on demand.** In
   `harness/fake_spotify/app.py`, add `/_fake/faults` modes that return
   these three failures, so the harness can produce each one.

## Test Plan
- Unit tests:
  - each OAuth error body maps to its typed exception;
  - a 401, then a failed refresh, re-raises the typed cause;
  - a 403 "user not registered" body maps to `SpotifyUserNotAllowed`, and a
    playlist 403 still maps to `SpotifyForbiddenError`;
  - `credentials_for` raises `SpotifyAppUnavailable` with empty config;
  - a web refresh with a rotated token updates `users.encrypted_refresh_token`.
- A worker test: a run whose refresh fails with `invalid_client` ends
  `failed` with `spotify_app_rejected`.
- The existing suites stay green.

## Verification Checklist
- [ ] `git grep -n "from_env\|from_flask_config" -- shuffify` shows only
      `credentials_for`.
- [ ] Each of the three faults in the fake Spotify produces its error code in
      the harness.
- [ ] CI is green.

## What NOT To Do
- Don't add any workspace concept yet.
- Don't change what users see.
- Don't treat every 403 as an app failure; playlist 403s are ordinary.

## Context
- Source skill: forge · Area: shuffify/spotify, services/auth_service.py, executors · Effort: M · Risk: Medium · Priority: High
