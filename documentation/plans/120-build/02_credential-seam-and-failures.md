---
title: "[plan] One credential seam, failures that keep their cause, one refresh token — auth, worker"
type: plan
status: draft
owner: chrisrogers37
created: 2026-11-10
updated: 2026-11-10
tags: [enhancement, auth]
repos: chrisrogers37/shuffify
---

## Summary

Part of [the #120 build plan](00_overview.md) (main). Size: L, shipped as two
PRs. There's no visible change. This phase does three things:
- routes every place that gets Spotify app credentials through one function;
- makes Spotify's failures keep their cause (revoked access, rejected app,
  account not allowed) all the way to the code that will act on it, without
  changing which handlers catch them today;
- makes the database the only home of the refresh token: the web and the
  worker both refresh inside a short row-locked transaction, and the session
  stops keeping its own copy.

It ships as two PRs so the riskier half can be reverted alone:
- **02a, the credential seam and typed failures** (steps 1 to 5 and 7): low
  risk, nothing users see changes.
- **02b, the refresh-token move** (step 6): changes how the live site's web
  requests stay signed in, on main, where every merge deploys production.
  It is revertible without touching 02a.

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
  (`shuffify/worker.py:94-95`, with a deadline of up to 240s at `worker.py:33`)
  and stores a rotated token in the database (`base_executor.py:112-125`). That
  closure also keeps a rotated token in memory when the save fails, so the
  next refresh in the same run doesn't use a token Spotify has already
  invalidated.
- A web refresh reads the refresh token from the session, and
  `_persist_token_to_session` (`auth_service.py:230-255`) writes only the
  session. The database copy goes stale, and so does the session's after a
  worker rotation. A PKCE refresh can invalidate the old token (phase 06), so
  one side would then fail with `invalid_grant`.
- Sign-in already stores the database copy (`routes/core.py:175`,
  `UserService.store_refresh_token(user, refresh_token)`).

**Why the existing lock can't protect the token.**
- `OperationRepository.target_locks` takes session-level advisory locks on the
  repository's engine (`shuffify/services/operations/repository.py:509-543`).
  Production's api and worker both connect through Neon's pooled host
  (checked read-only 2026-11-10, although the guides describe the direct
  host). A session advisory lock is not reliable through a transaction-mode
  pooler, so it can't be what stops two refreshes racing. A transaction-scoped
  row lock is correct through any pooler.
- `target_locks` waits 2 seconds by default, and the worker holds
  `credential:{user_id}` for a whole run. A web refresh waiting on that lock
  would time out during any long run.
- `get_authenticated_api` turns every non-deadline error into
  `AuthenticationError` (`auth_service.py:225-227`), and `require_auth`
  (`routes/__init__.py:60-73`) turns that into "signed out". A lock or database
  timeout would therefore sign the person out.

**What #143 left in the session** (7b56eaa). Live sessions carry
`encrypted_refresh_token`, not `refresh_token`:
- `encrypt_session_token` and `decrypt_session_token`
  (`auth_service.py:32-58`);
- the upgrade path in `is_authenticated` that re-encrypts a plaintext token
  (`routes/__init__.py:46-56`) and `require_auth`'s decrypt call;
- sign-in's write (`routes/core.py:151`) and `_persist_token_to_session`
  (`auth_service.py:251`);
- `tests/routes/test_session_refresh_token.py`, which asserts the behaviour
  this phase removes.

## Implementation Plan

### Dependencies
None. 02b follows 02a.

### Blocks
03, 06, 08.

### Steps

**02a: the credential seam and typed failures**

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
7. **Teach the fake Spotify to fail on demand.** In
   `harness/fake_spotify/app.py`, add `/_fake/faults` modes that return
   these three failures, so the harness can produce each one. Add a mode that
   makes the refresh endpoint rotate the refresh token and invalidate the
   previous one, as PKCE refreshes do, so 02b's tests can see a stale token
   fail.

**02b: the refresh-token move** (its own PR, revertible on its own)

6. **Make the database the only home of the refresh token.** Spotify
   rotates a PKCE refresh token and may invalidate the old one, so a refresh
   must read the current stored token, refresh, and store the new one as one
   serialised step. That step is a short database transaction, never a lock
   around a whole run.
   - **A refresher around `SpotifyAuthManager`** that `get_authenticated_api`
     and the worker's `_get_spotify_api` hand to `SpotifyAPI` in place of the
     bare auth manager (`SpotifyAPI` only calls `refresh_token` and
     `ensure_valid_token`, `api.py:141, 187`). Each refresh, in its own
     session that commits before the refresh returns:
     1. begins a transaction and sets a bounded `lock_timeout` (10 seconds);
     2. runs `SELECT ... FOR UPDATE` on the row that holds the encrypted
        refresh token: the `users` row today, and the connection row once the
        columns move (created in phase 03, re-pointed in phase 04 step 4);
     3. reads and decrypts the stored token, and refreshes with it;
     4. stores the rotated token, encrypted, and commits, which releases the
        row lock.
   - **The row lock is the only protection for the token.** It is correct
     through Neon's pooled host, because the lock lives inside one
     transaction. The worker's `credential:{user_id}` advisory lock stays for
     what it was for, keeping one user's runs from overlapping, but the
     refresher doesn't take it and nothing relies on it to protect the token.
     A web refresh therefore never waits on a worker run.
   - **A failed save keeps the rotated token.** If Spotify has rotated the
     token and the commit fails, retry the commit once. If it still fails,
     keep the rotated token in memory in the refresher (per process, keyed by
     the row) and use it for the next refresh while the stored token is still
     the one it replaced, retrying the store each time. This replaces the
     closure at `base_executor.py:112-125` with the same guarantee for web
     and worker alike. The save failure itself is transient and is logged.
   - **A lock or database timeout is transient, never "signed out".**
     Add `SpotifyRefreshUnavailable` under `SpotifyError`, outside both
     `SpotifyAuthError` and `SpotifyTokenExpiredError`. `get_authenticated_api`
     re-raises it untouched, as it does `SpotifyDeadlineError`, instead of
     letting `except Exception` (`auth_service.py:225-227`) wrap it in
     `AuthenticationError`. `require_auth` lets it through. A Flask handler
     returns 503 with `Retry-After`, never 401, and leaves the session as it
     is. The worker treats it as transient too: it is neither a typed app
     failure nor a failed run.
   - **The access token is minted on demand.** The session's access token is
     a cache. When the session has no access token, or it has expired, build
     the client from the stored refresh token with an already-expired access
     token so it refreshes on first use, as `_get_spotify_api` does for the
     worker. A person with a healthy stored token is never treated as "not
     connected" because the session lacks an access token; without a stored
     token, the request is signed out. This is what lets a fresh Google
     sign-in (phase 05) work without reconnecting Spotify.
   - **The web path needs the signed-in user's database id;** the session
     already carries it as `_db_user_id`.
   - **Remove #143's session refresh-token handling**, which the session no
     longer needs:
     - delete `encrypt_session_token` and `decrypt_session_token`
       (`auth_service.py:32-58`);
     - delete the upgrade path in `is_authenticated`
       (`routes/__init__.py:46-56`) and make it drop both `refresh_token` and
       `encrypted_refresh_token` from a live session that still carries one;
     - stop writing a refresh token at sign-in (`routes/core.py:151`) and in
       `_persist_token_to_session`, which writes the access token and its
       expiry only;
     - delete `tests/routes/test_session_refresh_token.py` and cover the
       replacement behaviour in the tests below.


## Test Plan

**02a**
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
- A worker test: a run whose refresh fails with `invalid_client` ends
  `failed` with `spotify_app_rejected`.

**02b** (real Postgres, as the operation lock tests use)
- A web refresh after a worker rotation uses the rotated token, and a web
  refresh stores its rotated token for the worker to use.
- Two refreshes at once against the fake Spotify, which invalidates the
  previous refresh token on rotation: both succeed, one after the other, and
  neither gets `invalid_grant`.
- **A refresh during a long worker run:** with the worker holding
  `credential:{user_id}` for several seconds, a web refresh still succeeds
  and the person is not signed out.
- **A lock timeout is transient:** with another transaction holding the row
  lock past `lock_timeout`, the refresh raises `SpotifyRefreshUnavailable`,
  the request returns 503 with `Retry-After` and not 401, and the session is
  unchanged.
- **A failed save keeps the token:** make the store fail once after Spotify
  rotates the token. The next refresh uses the in-memory token and gets no
  `invalid_grant`, and the stored token catches up.
- **Sign-out and back in:** a session with a user id and no access token, and
  a healthy stored refresh token, loads data without reconnecting; the same
  session with no stored token is signed out.
- The session never holds a refresh token, plaintext or encrypted, after
  sign-in or any request, and a live session that still carries one has it
  dropped on its next request.
- The design depends only on transaction-scoped row locks, so these tests on
  plain Postgres cover Neon's pooled host too.
- The existing suites stay green.

## Verification Checklist
- [ ] **02a:** `git grep -n "from_env\|SpotifyCredentials(" -- shuffify ':!shuffify/spotify/credentials.py'`
      returns nothing: only `credentials_for` builds credentials.
      `from_flask_config` is deliberately kept here (it's the body of
      `credentials_for`); phase 06 deletes it.
- [ ] **02a:** each of the three faults in the fake Spotify produces its error
      code in the harness.
- [ ] **02b:** `pytest tests/services/test_credential_refresher.py tests/routes/test_session_holds_no_refresh_token.py`
      passes (both files exist and run their tests).
- [ ] **02b:** `git grep -n "encrypt_session_token\|decrypt_session_token\|encrypted_refresh_token" -- shuffify/routes shuffify/services/auth_service.py`
      returns nothing, and `tests/routes/test_session_refresh_token.py` no
      longer exists.
- [ ] **02b:** `git revert` of the 02b merge alone leaves 02a's seam and typed
      failures working (a revert dry run on the branch before merging).
- [ ] After 02b deploys, the production logs show no `invalid_grant` and no
      503 from a refresh over the next day of scheduled runs.
- [ ] CI is green on each PR.

## What NOT To Do
- Don't add any workspace concept yet.
- Don't change what users see.
- Don't change which handlers catch today's exceptions; new types subclass
  the specific existing ones.
- Don't treat every 403 as an app failure; playlist 403s are ordinary.
- Don't read a refresh token from the session or hold one in a closure
  across a refresh.
- Don't rely on an advisory lock to protect the refresh token, and don't hold
  a lock around a whole run for it. The refresh is its own short transaction.
- Don't let a lock or database timeout reach `AuthenticationError`: it is not
  "signed out".
- Don't mix 02a and 02b in one PR.

## Context
- Source skill: forge · Area: shuffify/spotify, services/auth_service.py, executors · Effort: L · Risk: Medium · Priority: High
